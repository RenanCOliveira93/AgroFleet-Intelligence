"""Provedor INMET (Instituto Nacional de Meteorologia): observação de estação automática e previsão por município.

Rotas usadas (nenhuma tem documentação oficial publicada; todas foram verificadas em 2026-11-20 e podem mudar):
- Observação SEM token: ``https://apiprevmet3.inmet.gov.br/estacao/proxima/{ibge}`` → estação automática mais próxima
  do município e a última leitura horária. É a leitura REAL de uma estação, não modelo.
- Observação COM token (``AGROFLEET_INMET_TOKEN``, obtido por Acordo de Cooperação Técnica com o INMET):
  ``https://apitempo.inmet.gov.br/token/estacao/{d0}/{d1}/{estação}/{token}``, com a estação automática OPERANTE mais
  próxima do ponto no catálogo público ``/estacoes/T`` (dentro de ``AGROFLEET_INMET_MAX_STATION_KM``). Não precisa do
  código IBGE. Token inválido ou cancelado volta como HTTP 200 com o texto ``CHAVE INVÁLIDA!``.
- Previsão: ``https://apiprevmet3.inmet.gov.br/previsao/{ibge}`` → 5 dias; os dois primeiros com manhã/tarde/noite,
  os demais com um bloco único. Os ícones base64 (~270 KB por resposta) são descartados na leitura, nunca guardados.

Unidades (cabeçalho do CSV histórico oficial): ``HR_MEDICAO`` em UTC; ``VEN_VEL``/``VEN_RAJ`` em m/s (aqui convertidos
para km/h); ``RAD_GLO`` em kJ/m² (total da hora; ÷3,6 = média em W/m²); ``CHUVA`` em mm na hora; ``PRE_INS`` em hPa
no nível da estação. Dados de estação automática são BRUTOS, sem validação do INMET.

Uso: baixa frequência de propósito (cache de 30 min para observação e 3 h para previsão). O INMET se opõe a cópia
automatizada em massa (2026); este cliente faz no máximo uma busca por município/estação a cada TTL.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .base import (Location, Provider, ProviderError, as_int_if_whole, freshness, haversine_km, iso_z, ms_to_kmh,
                   num, text)

PREVMET = "https://apiprevmet3.inmet.gov.br"
APITEMPO = "https://apitempo.inmet.gov.br"
PERIOD_LABEL = {"manha": "Manhã", "tarde": "Tarde", "noite": "Noite", "dia": "Dia"}
RAW_NOTE = "Dado bruto de estação automática do INMET, sem validação (consistência) pelo instituto."
# campos da previsão que guardamos (lista branca: garante que ícones base64 nunca entram no cache)
_FC_KEEP = ("uf", "entidade", "resumo", "temp_max", "temp_min", "dir_vento", "int_vento", "umidade_max", "umidade_min",
            "dia_semana", "nascer", "ocaso", "temp_max_tende", "temp_min_tende", "cod_icone")


def period_for_hour(hour: int) -> str:
    """Período da previsão do INMET para a hora local: antes das 12 h → manhã (de madrugada, a manhã que vem);
    12–17 h → tarde; 18 h em diante → noite."""
    return "manha" if hour < 12 else "tarde" if hour < 18 else "noite"


def _json(resp, what: str):
    if resp.status == 204 or not resp.body.strip():
        raise ProviderError("no_data", f"INMET não devolveu dados ({what})")
    if resp.status >= 400:
        raise ProviderError("http_error", f"INMET respondeu HTTP {resp.status} ({what})")
    t = resp.text().strip()
    if t.upper().startswith("CHAVE INV"):
        raise ProviderError("no_token", "token INMET inválido ou cancelado (resposta 'CHAVE INVÁLIDA!')")
    try:
        return json.loads(t)
    except ValueError:
        raise ProviderError("no_data", f"resposta do INMET não é JSON ({what})") from None


# ----------------------------------------------------------------------------------------------------------------
# leitura (funções puras: recebem o JSON do INMET, devolvem estruturas limpas, sem dependência do relógio)
# ----------------------------------------------------------------------------------------------------------------

def observed_at(d: dict) -> datetime | None:
    """``DT_MEDICAO`` (AAAA-MM-DD) + ``HR_MEDICAO`` (HHMM), ambos UTC."""
    dt, hr = text(d.get("DT_MEDICAO")), text(d.get("HR_MEDICAO"))
    if not dt or hr is None:
        return None
    digits = "".join(ch for ch in hr if ch.isdigit()).zfill(4)[:4]
    try:
        day = date.fromisoformat(dt[:10])
        return datetime(day.year, day.month, day.day, int(digits[:2]), int(digits[2:]), tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_reading(d: dict) -> dict:
    """Linha de estação automática (mesmos nomes na rota pública e na rota com token) → valores do contrato."""
    rad = num(d.get("RAD_GLO"))
    return {
        "observed_at": iso_z(observed_at(d)),
        "temperature_c": num(d.get("TEM_INS")),
        "humidity_pct": as_int_if_whole(num(d.get("UMD_INS"))),
        "wind_speed_kmh": ms_to_kmh(num(d.get("VEN_VEL"))),
        "wind_dir_deg": as_int_if_whole(num(d.get("VEN_DIR"))),
        "wind_gust_kmh": ms_to_kmh(num(d.get("VEN_RAJ"))),
        "precipitation_mm": num(d.get("CHUVA")),
        "temp_max_c": num(d.get("TEM_MAX")), "temp_min_c": num(d.get("TEM_MIN")),
        "dew_point_c": num(d.get("PTO_INS")),
        "pressure_hpa": num(d.get("PRE_INS")),
        "radiation_kj_m2": rad,
        "radiation_w_m2": None if rad is None else round(rad / 3.6, 1),
    }


_MEASURED = ("temperature_c", "humidity_pct", "wind_speed_kmh", "precipitation_mm", "pressure_hpa")


def has_measurement(r: dict) -> bool:
    return r.get("observed_at") is not None and any(r.get(k) is not None for k in _MEASURED)


def parse_proxima(payload) -> dict:
    """``/estacao/proxima/{ibge}`` → ``{"station": {...}, "reading": {...}}``."""
    if not isinstance(payload, dict):
        raise ProviderError("no_data", "resposta inesperada do INMET (estação próxima)")
    e, d = payload.get("estacao") or {}, payload.get("dados") or {}
    code = text(e.get("CODIGO")) or text(d.get("CD_ESTACAO"))
    if not code or not isinstance(d, dict) or not d:
        raise ProviderError("no_data", "INMET não informou estação ou leitura para o município")
    station = {"code": code, "name": text(e.get("NOME")) or text(d.get("DC_NOME")), "uf": text(e.get("UF")) or text(d.get("UF")),
               "lat": num(e.get("LATITUDE")) if num(e.get("LATITUDE")) is not None else num(d.get("VL_LATITUDE")),
               "lon": num(e.get("LONGITUDE")) if num(e.get("LONGITUDE")) is not None else num(d.get("VL_LONGITUDE")),
               "inmet_distance_km": num(e.get("DISTANCIA_EM_KM"))}
    reading = parse_reading(d)
    if not has_measurement(reading):
        raise ProviderError("no_data", f"estação {code} sem leitura recente publicada")
    return {"station": station, "reading": reading}


def parse_catalog(payload) -> list[dict]:
    """Catálogo ``/estacoes/T`` → estações automáticas em operação com coordenadas válidas."""
    if not isinstance(payload, list):
        raise ProviderError("no_data", "catálogo de estações do INMET em formato inesperado")
    out = []
    for e in payload:
        if not isinstance(e, dict):
            continue
        sit = (text(e.get("CD_SITUACAO")) or "operante").lower()
        tp = (text(e.get("TP_ESTACAO")) or "automatica").lower()
        lat, lon, code = num(e.get("VL_LATITUDE")), num(e.get("VL_LONGITUDE")), text(e.get("CD_ESTACAO"))
        if not code or lat is None or lon is None or sit != "operante" or not tp.startswith("autom") or text(e.get("DT_FIM_OPERACAO")):
            continue
        out.append({"code": code, "name": text(e.get("DC_NOME")), "uf": text(e.get("SG_ESTADO")) or text(e.get("UF")), "lat": lat, "lon": lon})
    return out


def nearest_station(stations: list[dict], lat: float, lon: float, max_km: float) -> dict | None:
    best = min(stations, key=lambda s: haversine_km(lat, lon, s["lat"], s["lon"]), default=None)
    if best is None or haversine_km(lat, lon, best["lat"], best["lon"]) > max_km:
        return None
    return best


def latest_reading(rows) -> dict | None:
    """Rota com token: série horária (horas ainda não publicadas vêm com valores nulos) → última hora com medida."""
    if not isinstance(rows, list):
        return None
    parsed = [parse_reading(r) for r in rows if isinstance(r, dict)]
    parsed = [r for r in parsed if has_measurement(r)]
    return max(parsed, key=lambda r: r["observed_at"], default=None)


def _fc_block(x: dict) -> dict:
    return {k: x.get(k) for k in _FC_KEEP if k in x}


def parse_forecast(payload, ibge: str) -> dict:
    """``/previsao/{ibge}`` → ``{"days": {"AAAA-MM-DD": {"periods": {...}} | {"day": {...}}}}`` sem ícones."""
    root = payload.get(ibge) if isinstance(payload, dict) else None
    if root is None and isinstance(payload, dict) and len(payload) == 1:
        root = next(iter(payload.values()))
    if not isinstance(root, dict) or not root:
        raise ProviderError("no_data", f"INMET sem previsão para o município {ibge}")
    days = {}
    for k, v in root.items():
        try:
            d = datetime.strptime(k, "%d/%m/%Y").date().isoformat()
        except ValueError:
            continue
        if not isinstance(v, dict):
            continue
        per = {p: _fc_block(v[p]) for p in ("manha", "tarde", "noite") if isinstance(v.get(p), dict)}
        days[d] = {"periods": per} if per else {"day": _fc_block(v)}
    if not days:
        raise ProviderError("no_data", f"INMET sem previsão para o município {ibge}")
    return {"days": days}


def forecast_view(parsed: dict, ibge: str, loc: Location) -> dict:
    """Escolhe o bloco de HOJE (data e hora locais do fuso da propriedade) e monta o bloco ``forecast`` do contrato."""
    local = loc.now.astimezone(ZoneInfo(loc.tz))
    today = local.date().isoformat()
    day = parsed["days"].get(today)
    if day is None:
        raise ProviderError("no_data", f"previsão do INMET não cobre a data local de hoje ({today})")
    if "periods" in day:
        want = period_for_hour(local.hour)
        order = [want] + [p for p in ("manha", "tarde", "noite") if p != want]
        period = next(p for p in order if p in day["periods"])
        b = day["periods"][period]
    else:
        period, b = "dia", day["day"]

    def day_item(d, v):
        if "periods" in v:
            any_b = next(iter(v["periods"].values()))
            return {"date": d, "weekday": text(any_b.get("dia_semana")), "temp_min_c": as_int_if_whole(num(any_b.get("temp_min"))),
                    "temp_max_c": as_int_if_whole(num(any_b.get("temp_max"))), "summary": None,
                    "periods": {p: text(x.get("resumo")) for p, x in v["periods"].items()}}
        x = v["day"]
        return {"date": d, "weekday": text(x.get("dia_semana")), "temp_min_c": as_int_if_whole(num(x.get("temp_min"))),
                "temp_max_c": as_int_if_whole(num(x.get("temp_max"))), "summary": text(x.get("resumo")), "periods": None}

    return {
        "provider": "inmet", "provider_label": "INMET", "kind": "forecast",
        "municipality_code": ibge, "municipality": text(b.get("entidade")), "uf": text(b.get("uf")), "date": today,
        "period": period, "period_label": PERIOD_LABEL[period],
        "summary": text(b.get("resumo")), "temp_min_c": as_int_if_whole(num(b.get("temp_min"))),
        "temp_max_c": as_int_if_whole(num(b.get("temp_max"))),
        "humidity_min_pct": as_int_if_whole(num(b.get("umidade_min"))), "humidity_max_pct": as_int_if_whole(num(b.get("umidade_max"))),
        "wind_dir": text(b.get("dir_vento")), "wind_intensity": text(b.get("int_vento")),
        # a previsão do INMET por município é textual/por faixa: não há valor pontual de temperatura, umidade ou vento
        "temperature_c": None, "humidity_pct": None, "wind_speed_kmh": None,
        "temp_max_trend": text(b.get("temp_max_tende")), "temp_min_trend": text(b.get("temp_min_tende")),
        "sunrise": text(b.get("nascer")), "sunset": text(b.get("ocaso")),
        "days": [day_item(d, v) for d, v in sorted(parsed["days"].items()) if d >= today],
    }


def observation_view(station: dict, reading: dict, loc: Location, max_age_s: float) -> dict:
    """Bloco ``observation`` do contrato. Distância calculada por nós (haversine) do ponto consultado à estação;
    sem coordenadas do ponto, usa a distância informada pelo INMET (referência: o município)."""
    if loc.lat is not None and loc.lon is not None and station.get("lat") is not None and station.get("lon") is not None:
        dist, basis = round(haversine_km(loc.lat, loc.lon, station["lat"], station["lon"]), 1), "point"
    else:
        dist, basis = station.get("inmet_distance_km"), "inmet_municipality" if station.get("inmet_distance_km") is not None else None
    obs_t = datetime.fromisoformat(reading["observed_at"].replace("Z", "+00:00")) if reading.get("observed_at") else None
    age, stale = freshness(obs_t, loc.now, max_age_s)
    return {"provider": "inmet", "provider_label": "INMET", "kind": "observation",
            "station_code": station.get("code"), "station_name": station.get("name"), "station_uf": station.get("uf"),
            "station_lat": station.get("lat"), "station_lon": station.get("lon"), "distance_km": dist, "distance_basis": basis,
            **reading, "age_s": age, "stale": stale, "note": RAW_NOTE}


class InmetProvider(Provider):
    name, label = "inmet", "INMET"
    supports = frozenset({"observation", "forecast"})

    def attribution(self) -> dict:
        return {"provider": "inmet", "text": "Fonte: INMET", "url": "https://portal.inmet.gov.br",
                "licence": "Dados públicos (Decreto 8.777/2016); citar a fonte"}

    # -- observação --
    def observation(self, loc: Location, reasons: list) -> dict:
        if self.cfg.inmet_token:
            if loc.lat is None or loc.lon is None:
                reasons.append({"provider": "inmet", "code": "no_data", "message": "rota INMET com token exige coordenadas do ponto"})
            else:
                try:
                    return self._token_observation(loc)
                except ProviderError as e:
                    reasons.append({"provider": "inmet", "code": e.code, "message": f"rota com token: {e.message}"})
        if not loc.ibge:
            raise ProviderError("no_municipality", "sem código IBGE do município: a estação mais próxima e a previsão do INMET dependem dele")
        data, cached, ts = self.cached(("proxima", loc.ibge), self.cfg.obs_ttl_s, lambda: parse_proxima(
            _json(self.get(f"{PREVMET}/estacao/proxima/{quote(loc.ibge)}"), "estação próxima")))
        obs = observation_view(data["station"], data["reading"], loc, self.cfg.max_age_s)
        if obs["distance_basis"] == "point" and obs["distance_km"] is not None and obs["distance_km"] > self.cfg.inmet_max_station_km:
            raise ProviderError("no_station_in_range", f"estação INMET mais próxima do município ({obs['station_code']}) fica a "
                                f"{obs['distance_km']:.0f} km do ponto (limite {self.cfg.inmet_max_station_km:.0f} km)")
        return {**obs, "_cached": cached, "_fetched_at": ts}

    def _token_observation(self, loc: Location) -> dict:
        stations, _c, _t = self.cached(("catalog",), self.cfg.catalog_ttl_s,
                                       lambda: parse_catalog(_json(self.get(f"{APITEMPO}/estacoes/T"), "catálogo de estações")))
        st = nearest_station(stations, loc.lat, loc.lon, self.cfg.inmet_max_station_km)
        if st is None:
            raise ProviderError("no_station_in_range", f"nenhuma estação automática do INMET em operação a até "
                                f"{self.cfg.inmet_max_station_km:.0f} km do ponto")
        d1 = loc.now.astimezone(timezone.utc).date()
        d0 = d1 - timedelta(days=1)

        def load():
            rows = _json(self.get(f"{APITEMPO}/token/estacao/{d0.isoformat()}/{d1.isoformat()}/{quote(st['code'])}/{quote(self.cfg.inmet_token)}"),
                         "estação com token")
            r = latest_reading(rows)
            if r is None:
                raise ProviderError("no_data", f"estação {st['code']} sem leitura publicada nas últimas 24 h")
            return r

        reading, cached, ts = self.cached(("token_obs", st["code"], d1.isoformat()), self.cfg.obs_ttl_s, load)
        return {**observation_view(st, reading, loc, self.cfg.max_age_s), "_cached": cached, "_fetched_at": ts}

    # -- previsão --
    def forecast(self, loc: Location, reasons: list) -> dict:
        if not loc.ibge:
            raise ProviderError("no_municipality", "sem código IBGE do município: a previsão do INMET é por município")
        ibge = loc.ibge
        parsed, cached, ts = self.cached(("previsao", ibge), self.cfg.forecast_ttl_s,
                                         lambda: parse_forecast(_json(self.get(f"{PREVMET}/previsao/{quote(ibge)}"), "previsão"), ibge))
        return {**forecast_view(parsed, ibge, loc), "_cached": cached, "_fetched_at": ts}

