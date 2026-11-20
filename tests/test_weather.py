"""Clima (Etapa 7). Todos os testes são offline: a busca HTTP é uma função falsa injetada; nenhuma rede é acessada."""
import json
import socket
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agrofleet.registry import PropertyInfo
from agrofleet.weather import NO_DATA_MESSAGE, SIMULATED_NOTE, HttpResponse, ProviderError, WeatherConfig, WeatherService
from agrofleet.weather import base as B
from agrofleet.weather import inmet as I
from agrofleet.weather import metno as M
from agrofleet.weather.base import Location, num
from backend.app.config import Settings
from backend.app.main import create_app

NOW = datetime(2026, 11, 20, 14, 10, tzinfo=timezone.utc)        # 11h10 em Brasília (período "manhã")
TZ = "America/Sao_Paulo"

# resposta real de https://apiprevmet3.inmet.gov.br/estacao/proxima/5206206 (2026-11-20, 14h10 UTC)
PROXIMA = {"estacao": {"UF": "GO", "CODIGO": "A036", "LONGITUDE": "-47.61305555", "REGIAO": "CO", "DISTANCIA_EM_KM": "14",
                       "NOME": "CRISTALINA", "LATITUDE": "-16.78499999", "GEOCODE": "5206206"},
           "dados": {"DC_NOME": "CRISTALINA", "PRE_INS": "882.1", "TEM_SEN": "25.7", "VL_LATITUDE": "-16.78499999", "PRE_MAX": "882.3",
                     "UF": "GO", "RAD_GLO": "2693.5", "PTO_INS": "17.7", "TEM_MIN": "25.7", "VL_LONGITUDE": "-47.61305555",
                     "UMD_MIN": "54", "PTO_MAX": "18.4", "VEN_DIR": "182", "DT_MEDICAO": "2026-11-20", "CHUVA": "0.2", "PRE_MIN": "882.1",
                     "UMD_MAX": "62", "VEN_VEL": "1.7", "PTO_MIN": "16.1", "TEM_MAX": "27.1", "TEN_BAT": "13.5", "VEN_RAJ": "5.3",
                     "TEM_CPU": "27", "TEM_INS": "25.9", "UMD_INS": "61", "CD_ESTACAO": "A036", "HR_MEDICAO": "1300"}}

ICON = "data:image/png;base64," + "iVBORw0KGgo" * 50


def _fc_block(resumo, tmin, tmax, umin, umax, dia, **kw):
    """Bloco no formato real de /previsao/{ibge} (campos e tipos da amostra de 20/11/2026), com ícones base64."""
    return {"uf": "GO", "entidade": "Cristalina", "resumo": resumo, "tempo": "", "temp_max": tmax, "temp_min": tmin, "dir_vento": "N-NW",
            "int_vento": "Fracos", "cod_icone": "37", "icone": ICON, "dia_semana": dia, "umidade_max": umax, "umidade_min": umin,
            "temp_max_tende": "Ligeiro Declínio", "cod_temp_max_tende_icone": "10", "temp_max_tende_icone": ICON,
            "temp_min_tende": "Ligeira Elevação", "cod_temp_min_tende_icone": "11", "temp_min_tende_icone": ICON,
            "estacao": "Primavera", "hora": "10", "nascer": "05h28", "ocaso": "18h24", "fonte": "prevmet", **kw}


PREVISAO = {"5206206": {
    "20/11/2026": {"manha": _fc_block("Muitas nuvens com pancadas de chuva isoladas", 20, 28, 70, 100, "Sexta-Feira"),
                   "tarde": _fc_block("Muitas nuvens com pancadas de chuva", 20, 28, 70, 100, "Sexta-Feira"),
                   "noite": _fc_block("Muitas nuvens com pancadas de chuva isoladas", 20, 28, 70, 100, "Sexta-Feira")},
    "21/11/2026": {"manha": _fc_block("Muitas nuvens com pancadas de chuva e trovoadas isoladas", 21, 31, 50, 100, "Sábado"),
                   "tarde": _fc_block("Muitas nuvens com pancadas de chuva e trovoadas isoladas", 21, 31, 50, 100, "Sábado"),
                   "noite": _fc_block("Muitas nuvens com possibilidade de chuva isolada", 21, 31, 50, 100, "Sábado")},
    "22/11/2026": _fc_block("Nublado com pancadas de chuva e trovoadas isoladas", 21, 32, 50, 100, "Domingo"),
    "23/11/2026": _fc_block("Nublado com pancadas de chuva e trovoadas isoladas", 21, 32, 50, 100, "Segunda-Feira"),
    "24/11/2026": _fc_block("Nublado com pancadas de chuva e trovoadas isoladas", 21, 31, 50, 100, "Terça-Feira"),
}}

# catálogo /estacoes/T (nomes de campo do catálogo público de estações automáticas)
CATALOG = [
    {"CD_OSCAR": "0-2000-0-86716", "DC_NOME": "BRASILIA", "FL_CAPITAL": "S", "DT_FIM_OPERACAO": None, "CD_SITUACAO": "Operante",
     "TP_ESTACAO": "Automatica", "VL_LATITUDE": "-15.78944444", "CD_ESTACAO": "A001", "SG_ESTADO": "DF", "VL_LONGITUDE": "-47.92583332"},
    {"DC_NOME": "CRISTALINA", "DT_FIM_OPERACAO": None, "CD_SITUACAO": "Operante", "TP_ESTACAO": "Automatica",
     "VL_LATITUDE": "-16.78499999", "CD_ESTACAO": "A036", "SG_ESTADO": "GO", "VL_LONGITUDE": "-47.61305555"},
    {"DC_NOME": "PERTO MAS EM PANE", "DT_FIM_OPERACAO": None, "CD_SITUACAO": "Pane", "TP_ESTACAO": "Automatica",
     "VL_LATITUDE": "-16.70", "CD_ESTACAO": "A999", "SG_ESTADO": "GO", "VL_LONGITUDE": "-47.60"},
]


def _row(hr, **vals):
    base = {k: None for k in ("TEM_INS", "UMD_INS", "VEN_VEL", "VEN_DIR", "VEN_RAJ", "CHUVA", "PRE_INS", "RAD_GLO")}
    return {"CD_ESTACAO": "A036", "DC_NOME": "CRISTALINA", "UF": "GO", "DT_MEDICAO": "2026-11-20", "HR_MEDICAO": hr, **base, **vals}


TOKEN_ROWS = [_row("1100", TEM_INS="23.0", UMD_INS="70", VEN_VEL="1.0"), _row("1200", TEM_INS="24.5", UMD_INS="66", VEN_VEL="2.0"),
              _row("1300"), _row("1400")]                    # horas ainda não publicadas: tudo nulo


def metno_payload(start=datetime(2026, 11, 20, 14, tzinfo=timezone.utc), hours=12):
    ts = []
    for h in range(hours):
        t = start + timedelta(hours=h)
        ts.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_pressure_at_sea_level": 1012.3, "air_temperature": 26.0 + h * 0.5, "cloud_area_fraction": 80.5,
                                    "relative_humidity": 60.0 + h, "wind_from_direction": 175.2, "wind_speed": 2.0}},
            "next_1_hours": {"summary": {"symbol_code": "rainshowersandthunder_day" if h == 0 else "cloudy"}, "details": {"precipitation_amount": 0.4}},
            "next_6_hours": {"summary": {"symbol_code": "rain"}, "details": {"precipitation_amount": 2.1}}}})
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-47.6, -16.7, 1010]},
            "properties": {"meta": {"updated_at": "2026-11-20T13:30:00Z", "units": {"air_temperature": "celsius", "wind_speed": "m/s"}},
                           "timeseries": ts}}


OPENMETEO = {"latitude": -16.69596, "longitude": -47.621765, "utc_offset_seconds": 0, "timezone": "GMT", "elevation": 1078.0,
             "current_units": {"time": "iso8601", "temperature_2m": "°C", "wind_speed_10m": "km/h"},
             "current": {"time": "2026-11-20T13:45", "interval": 900, "temperature_2m": 26.9, "relative_humidity_2m": 67,
                         "wind_speed_10m": 8.2, "wind_direction_10m": 351, "wind_gusts_10m": 19.8, "precipitation": 0.00}}


def J(obj, status=200, headers=None):
    return HttpResponse(status, {"content-type": "application/json", **(headers or {})}, json.dumps(obj).encode())


class FakeFetch:
    """Roteia por trecho da URL. Valor: HttpResponse, exceção a levantar ou função(url, headers) → HttpResponse."""

    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        for k, v in self.routes.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return v(url, headers) if callable(v) else v
        raise AssertionError(f"URL inesperada no teste: {url}")

    def count(self, part):
        return sum(1 for u, _h in self.calls if part in u)


class Clock:
    def __init__(self, t=NOW):
        self.t, self.mono = t, 1000.0

    def utc(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)
        self.mono += s


def make(routes=None, clock=None, **cfg):
    clock = clock or Clock()
    f = FakeFetch(routes if routes is not None else {"/estacao/proxima/": J(PROXIMA), "/previsao/": J(PREVISAO)})
    svc = WeatherService(WeatherConfig(**cfg), fetch=f, utcnow=clock.utc, monotonic=lambda: clock.mono)
    return svc, f, clock


# ---------------------------------------------------------------------------------------------------------------
# leitura e unidades
# ---------------------------------------------------------------------------------------------------------------

def test_sentinelas_viram_none_nunca_zero():
    for v in (None, "", " ", "*", "Null", "null", 9999, -9999, "9999", "-9999", "-9999.0", float("nan")):
        assert num(v) is None, v
    assert num("27.6*") == 27.6 and num("-13,01666666") == pytest.approx(-13.01666666)
    assert num("0") == 0.0 and num(0) == 0.0 and num("0.2") == 0.2
    r = I.parse_reading({**PROXIMA["dados"], "VEN_VEL": "9999", "VEN_RAJ": "Null", "CHUVA": "", "UMD_INS": None})
    assert r["wind_speed_kmh"] is None and r["wind_gust_kmh"] is None and r["precipitation_mm"] is None and r["humidity_pct"] is None
    assert r["temperature_c"] == 25.9


def test_proxima_amostra_real_unidades_e_utc():
    p = I.parse_proxima(PROXIMA)
    st, r = p["station"], p["reading"]
    assert st == {"code": "A036", "name": "CRISTALINA", "uf": "GO", "lat": -16.78499999, "lon": -47.61305555, "inmet_distance_km": 14.0}
    assert r["observed_at"] == "2026-11-20T13:00:00Z"            # HR_MEDICAO é UTC (13h00 UTC = 10h00 em Brasília)
    assert r["temperature_c"] == 25.9 and r["humidity_pct"] == 61 and r["wind_dir_deg"] == 182
    assert r["wind_speed_kmh"] == 6.1 and r["wind_gust_kmh"] == 19.1      # m/s → km/h (1,7 e 5,3 m/s)
    assert r["radiation_kj_m2"] == 2693.5 and r["radiation_w_m2"] == 748.2  # kJ/m² na hora ÷ 3,6 = W/m² médio
    assert r["pressure_hpa"] == 882.1 and r["precipitation_mm"] == 0.2


def test_observacao_distancia_propria_idade_e_stale():
    p = I.parse_proxima(PROXIMA)
    obs = I.observation_view(p["station"], p["reading"], Location(-16.70, -47.60, "5206206", TZ, NOW), 7200)
    assert obs["distance_km"] == pytest.approx(9.6, abs=0.1) and obs["distance_basis"] == "point"   # haversine nosso, não o "14" do INMET
    assert obs["age_s"] == 4200 and obs["stale"] is False and obs["kind"] == "observation"
    late = I.observation_view(p["station"], p["reading"], Location(-16.70, -47.60, "5206206", TZ, NOW + timedelta(hours=1)), 7200)
    assert late["stale"] is True and late["age_s"] == 7800
    no_pt = I.observation_view(p["station"], p["reading"], Location(None, None, "5206206", TZ, NOW), 7200)
    assert no_pt["distance_km"] == 14.0 and no_pt["distance_basis"] == "inmet_municipality"


def test_stale_continua_devolvido_com_motivo():
    svc, _f, clock = make()
    clock.advance(2 * 3600)                                    # leitura das 13h UTC vista às 16h10 UTC
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["status"] == "ok" and out["observation"]["stale"] is True
    assert [r["code"] for r in out["reasons"]] == ["stale"] and "A036" in out["reasons"][0]["message"]


def test_previsao_periodo_pela_hora_local_e_sem_icones():
    parsed = I.parse_forecast(PREVISAO, "5206206")
    assert "base64" not in json.dumps(parsed) and "icone" not in json.dumps(parsed).replace("cod_icone", "")
    at = lambda h: Location(-16.7, -47.6, "5206206", TZ, datetime(2026, 11, 20, h, 30, tzinfo=timezone.utc))  # noqa: E731
    assert I.forecast_view(parsed, "5206206", at(14))["period"] == "manha"     # 11h30 local
    fc = I.forecast_view(parsed, "5206206", at(18))                            # 15h30 local
    assert fc["period"] == "tarde" and fc["period_label"] == "Tarde" and fc["summary"] == "Muitas nuvens com pancadas de chuva"
    assert fc["temp_min_c"] == 20 and fc["temp_max_c"] == 28 and fc["humidity_min_pct"] == 70 and fc["humidity_max_pct"] == 100
    assert fc["wind_dir"] == "N-NW" and fc["wind_intensity"] == "Fracos" and fc["municipality"] == "Cristalina" and fc["uf"] == "GO"
    assert fc["temperature_c"] is None and fc["wind_speed_kmh"] is None       # previsão do INMET não tem valor pontual
    assert I.forecast_view(parsed, "5206206", at(23))["period"] == "noite"     # 20h30 local
    # 01h30 UTC do dia 21 = 22h30 local do dia 20 → ainda "noite" do dia 20 (a data é a LOCAL)
    late = Location(-16.7, -47.6, "5206206", TZ, datetime(2026, 11, 21, 1, 30, tzinfo=timezone.utc))
    assert I.forecast_view(parsed, "5206206", late)["date"] == "2026-11-20"
    madrugada = Location(-16.7, -47.6, "5206206", TZ, datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc))   # 03h local
    assert I.forecast_view(parsed, "5206206", madrugada)["period"] == "manha"
    d3 = I.forecast_view(parsed, "5206206", Location(-16.7, -47.6, "5206206", TZ, datetime(2026, 11, 22, 15, tzinfo=timezone.utc)))
    assert d3["period"] == "dia" and d3["period_label"] == "Dia" and d3["temp_max_c"] == 32
    assert [d["date"] for d in d3["days"]] == ["2026-11-22", "2026-11-23", "2026-11-24"]
    with pytest.raises(ProviderError) as e:
        I.forecast_view(parsed, "5206206", Location(-16.7, -47.6, "5206206", TZ, datetime(2026, 11, 26, 15, tzinfo=timezone.utc)))
    assert e.value.code == "no_data"
    assert [I.period_for_hour(h) for h in (0, 5, 6, 11, 12, 17, 18, 23)] == ["manha"] * 4 + ["tarde"] * 2 + ["noite"] * 2


# ---------------------------------------------------------------------------------------------------------------
# serviço: status, motivos, ordem, cache
# ---------------------------------------------------------------------------------------------------------------

def test_lookup_ok_contrato_a():
    svc, f, _c = make()
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ, source="field_centroid", fictitious=True)
    assert out["status"] == "ok" and out["message"] is None and out["reasons"] == []
    assert out["location"] == {"lat": -16.7, "lon": -47.6, "source": "field_centroid", "fictitious": True, "note": SIMULATED_NOTE,
                               "ibge_municipality": "5206206"}
    o, fc = out["observation"], out["forecast"]
    for k in ("provider", "provider_label", "kind", "station_code", "station_name", "station_uf", "distance_km", "observed_at", "age_s",
              "stale", "temperature_c", "humidity_pct", "wind_speed_kmh", "wind_dir_deg", "wind_gust_kmh", "precipitation_mm"):
        assert k in o, k
    for k in ("provider", "provider_label", "kind", "municipality_code", "municipality", "uf", "date", "period", "period_label",
              "summary", "temp_min_c", "temp_max_c", "humidity_min_pct", "humidity_max_pct", "wind_dir", "wind_intensity"):
        assert k in fc, k
    assert "_cached" not in o and "_fetched_at" not in fc
    assert out["attribution"] == "Fonte: INMET" and out["non_commercial"] is False and out["cached"] is False
    assert out["fetched_at"] == "2026-11-20T14:10:00Z"
    ua = {h["User-Agent"] for _u, h in f.calls}
    assert ua == {B.DEFAULT_USER_AGENT} and "curl" not in B.DEFAULT_USER_AGENT.lower()


def test_sem_dados_meteorologicos_com_motivos_http_e_timeout():
    svc, _f, _c = make({"/estacao/proxima/": J({"erro": 1}, status=500), "/previsao/": ProviderError("timeout", "tempo esgotado (8 s)")})
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["status"] == "unavailable" and out["message"] == NO_DATA_MESSAGE == "Sem dados meteorológicos"
    assert out["observation"] is None and out["forecast"] is None and out["attribution"] is None
    assert sorted(r["code"] for r in out["reasons"]) == ["http_error", "timeout"]


def test_resposta_vazia_ou_nao_json_vira_no_data():
    svc, _f, _c = make({"/estacao/proxima/": HttpResponse(204, {}, b""), "/previsao/": HttpResponse(200, {}, b"<html>manuten")})
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["status"] == "unavailable" and [r["code"] for r in out["reasons"]] == ["no_data", "no_data"]


def test_parcial_quando_so_um_bloco():
    svc, _f, _c = make({"/estacao/proxima/": J(PROXIMA), "/previsao/": J({}, status=502)})
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["status"] == "partial" and out["observation"] and out["forecast"] is None and out["message"] is None


def test_sem_municipio_e_ibge_padrao_do_ambiente():
    svc, f, _c = make()
    out = svc.lookup(-16.70, -47.60, ibge=None, tz=TZ)
    assert out["status"] == "unavailable" and {r["code"] for r in out["reasons"]} == {"no_municipality"} and not f.calls
    svc2, f2, _c = make(default_ibge="5206206")
    assert svc2.lookup(-16.70, -47.60, tz=TZ)["status"] == "ok" and f2.count("/5206206") == 2
    bad = svc2.lookup(-16.70, -47.60, ibge="12", tz=TZ)          # inválido: motivo + cai no padrão
    assert bad["status"] == "ok" and bad["reasons"][0]["code"] == "no_municipality"


def test_estacao_do_municipio_longe_demais_do_ponto():
    svc, _f, _c = make(inmet_max_station_km=5)
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["observation"] is None and out["status"] == "partial"
    assert [r["code"] for r in out["reasons"]] == ["no_station_in_range"]


def test_token_estacao_mais_proxima_operante_sem_ibge():
    svc, f, _c = make({"/estacoes/T": J(CATALOG), "/token/estacao/": J(TOKEN_ROWS)}, inmet_token="tok-123")
    out = svc.lookup(-16.70, -47.60, ibge=None, tz=TZ)
    o = out["observation"]
    assert o["station_code"] == "A036" and o["observed_at"] == "2026-11-20T12:00:00Z" and o["temperature_c"] == 24.5   # ignora horas nulas
    assert o["wind_speed_kmh"] == 7.2 and o["distance_km"] == pytest.approx(9.6, abs=0.1)     # A999 (em pane) foi descartada
    assert f.count("/token/estacao/2026-11-19/2026-11-20/A036/tok-123") == 1
    # previsão precisa do IBGE; leitura das 12h UTC vista às 14h10 passou do limite de 2 h
    assert out["status"] == "partial" and [r["code"] for r in out["reasons"]] == ["no_municipality", "stale"] and o["stale"] is True
    assert "tok-123" not in json.dumps(out)


def test_chave_invalida_vira_no_token_e_cai_na_rota_publica():
    invalid = HttpResponse(200, {"content-type": "text/plain"}, "CHAVE INVÁLIDA!".encode())
    svc, f, _c = make({"/estacoes/T": J(CATALOG), "/token/estacao/": invalid, "/estacao/proxima/": J(PROXIMA), "/previsao/": J(PREVISAO)},
                      inmet_token="cancelado")
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["status"] == "ok" and out["observation"]["station_code"] == "A036"
    assert out["reasons"][0]["code"] == "no_token" and "CHAVE INVÁLIDA" in out["reasons"][0]["message"]
    assert f.count("/estacao/proxima/5206206") == 1


def test_token_sem_estacao_no_raio():
    far = [{**CATALOG[0]}]                                     # só Brasília, ~110 km
    svc, _f, _c = make({"/estacoes/T": J(far), "/estacao/proxima/": J(PROXIMA), "/previsao/": J(PREVISAO)}, inmet_token="t")
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["reasons"][0]["code"] == "no_station_in_range" and out["observation"]["station_code"] == "A036"


def test_cache_ttl_e_falha_em_cache():
    svc, f, clock = make()
    svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["cached"] is True and len(f.calls) == 2 and out["fetched_at"] == "2026-11-20T14:10:00Z"
    clock.advance(1801)                                        # passou o TTL da observação (1800 s), não o da previsão (10800 s)
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert f.count("/estacao/proxima/") == 2 and f.count("/previsao/") == 1 and out["cached"] is False
    assert out["observation"]["age_s"] == 4200 + 1801         # idade calculada na hora da resposta, não congelada no cache

    bad, fb, clock = make({"/estacao/proxima/": J({}, status=503), "/previsao/": J({}, status=503)})
    for _ in range(3):
        assert bad.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)["status"] == "unavailable"
    assert len(fb.calls) == 2                                  # falha fica em cache (120 s): não martela o provedor
    clock.advance(121)
    bad.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert len(fb.calls) == 4


def test_cache_por_municipio_e_distancia_por_ponto():
    svc, f, _c = make()
    a = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    b = svc.lookup(-16.75, -47.61, ibge="5206206", tz=TZ)
    assert len(f.calls) == 2 and b["cached"] is True
    assert b["observation"]["distance_km"] < a["observation"]["distance_km"]


def test_sem_provedores_e_desconhecido():
    svc, f, _c = make(providers=["none"])
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["status"] == "unavailable" and out["reasons"][0]["code"] == "disabled" and not f.calls
    svc, _f, _c = make(providers=["foo", "inmet"])
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["status"] == "ok" and out["reasons"][0] == {"provider": "foo", "code": "disabled", "message": "provedor de clima desconhecido: 'foo'"}


# ---------------------------------------------------------------------------------------------------------------
# MET Norway e Open-Meteo
# ---------------------------------------------------------------------------------------------------------------

def test_symbol_code_em_portugues():
    assert M.symbol_pt("partlycloudy_day") == "Parcialmente nublado"
    assert M.symbol_pt("rainshowersandthunder_night") == "Pancadas de chuva com trovoadas"
    assert M.symbol_pt("heavyrain") == "Chuva forte" and M.symbol_pt("desconhecido") is None and M.symbol_pt(None) is None


def test_ordem_e_fallback_inmet_para_metno():
    hdr = {"expires": "Fri, 20 Nov 2026 14:40:00 GMT", "date": "Fri, 20 Nov 2026 14:10:00 GMT", "last-modified": "Fri, 20 Nov 2026 13:30:00 GMT"}
    routes = {"/estacao/proxima/": J(PROXIMA), "/previsao/": J({}, status=500), "api.met.no": J(metno_payload(), headers=hdr)}
    svc, f, _c = make(routes, providers=["inmet", "metno"])
    out = svc.lookup(-16.7012, -47.6049, ibge="5206206", tz=TZ)
    fc = out["forecast"]
    assert out["status"] == "ok" and out["observation"]["provider"] == "inmet" and fc["provider"] == "metno"
    assert fc["provider_label"] == "MET Norway (modelo)" and fc["period"] == "manha" and fc["summary"] == "Pancadas de chuva com trovoadas"
    assert fc["temperature_c"] == 26.0 and fc["wind_speed_kmh"] == 7.2 and fc["wind_dir"] == "S" and fc["humidity_pct"] == 60
    assert out["attribution"] == "Fonte: INMET · Data from MET Norway" and [a["provider"] for a in out["attributions"]] == ["inmet", "metno"]
    assert [r["code"] for r in out["reasons"]] == ["http_error"]
    assert any("lat=-16.70&lon=-47.60" in u for u, _h in f.calls)          # ToS: no máximo 4 casas (usamos 2)

    svc, f, _c = make(routes={"/estacao/proxima/": J(PROXIMA), "api.met.no": J(metno_payload(), headers=hdr)}, providers=["metno", "inmet"])
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    assert out["forecast"]["provider"] == "metno" and out["observation"]["provider"] == "inmet" and f.count("/previsao/") == 0


def test_metno_respeita_expires_e_if_modified_since():
    hdr = {"expires": "Fri, 20 Nov 2026 14:40:00 GMT", "date": "Fri, 20 Nov 2026 14:10:00 GMT", "last-modified": "Fri, 20 Nov 2026 13:30:00 GMT"}
    seen = []

    def route(url, headers):
        seen.append(headers.get("If-Modified-Since"))
        return J(metno_payload(), headers=hdr) if len(seen) == 1 else HttpResponse(304, {**hdr, "expires": "Fri, 20 Nov 2026 15:40:00 GMT"}, b"")

    svc, f, clock = make({"api.met.no": route}, providers=["metno"], forecast_ttl_s=60)
    assert svc.lookup(-16.70, -47.60, tz=TZ)["forecast"]["provider"] == "metno"
    clock.advance(120)                                         # cache do serviço expirou, mas o Expires (30 min) não
    assert svc.lookup(-16.70, -47.60, tz=TZ)["forecast"] is not None and len(seen) == 1
    clock.advance(1800)                                        # passou o Expires: revalida com If-Modified-Since, 304 reaproveita
    out = svc.lookup(-16.70, -47.60, tz=TZ)
    assert len(seen) == 2 and seen[1] == "Fri, 20 Nov 2026 13:30:00 GMT" and out["forecast"]["provider"] == "metno"


def test_openmeteo_bloqueado_fora_de_dev_ou_sem_flag():
    routes = {"/estacao/proxima/": J({}, status=500), "/previsao/": J(PREVISAO), "open-meteo.com": J(OPENMETEO)}
    for cfg in ({"env": "prod", "allow_noncommercial": True}, {"env": "dev", "allow_noncommercial": False}):
        svc, f, _c = make(routes, providers=["inmet", "openmeteo"], **cfg)
        out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
        assert f.count("open-meteo") == 0 and out["observation"] is None and out["non_commercial"] is False
        assert any(r["provider"] == "openmeteo" and r["code"] == "not_allowed" for r in out["reasons"])
        assert svc.summary()["usable"] == ["inmet"]


def test_openmeteo_em_dev_e_modelo_nao_comercial():
    routes = {"/estacao/proxima/": J({}, status=500), "/previsao/": J(PREVISAO), "open-meteo.com": J(OPENMETEO)}
    svc, f, _c = make(routes, providers=["inmet", "openmeteo"], env="dev", allow_noncommercial=True)
    out = svc.lookup(-16.70, -47.60, ibge="5206206", tz=TZ)
    o = out["observation"]
    assert o["provider"] == "openmeteo" and o["kind"] == "model" and o["provider_label"] == "Open-Meteo (modelo)"
    assert o["station_code"] is None and o["distance_km"] is None and o["temperature_c"] == 26.9 and o["wind_speed_kmh"] == 8.2
    assert o["observed_at"] == "2026-11-20T13:45:00Z" and o["age_s"] == 1500 and o["stale"] is False
    assert out["non_commercial"] is True and "Weather data by Open-Meteo.com" in out["attribution"]
    om = next(a for a in out["attributions"] if a["provider"] == "openmeteo")
    assert om["licence"] == "CC BY 4.0" and om["url"].startswith("https://open-meteo.com")
    url = next(u for u, _h in f.calls if "open-meteo" in u)
    assert url.startswith("https://api.open-meteo.com/v1/forecast?") and "timezone=GMT" in url and "apikey" not in url


def test_openmeteo_plano_pago_libera_em_producao():
    routes = {"customer-api.open-meteo.com": J(OPENMETEO)}
    svc, f, _c = make(routes, providers=["openmeteo"], env="prod", openmeteo_base_url="https://customer-api.open-meteo.com",
                      openmeteo_api_key="k1")
    out = svc.lookup(-16.70, -47.60, tz=TZ)
    assert out["observation"]["kind"] == "model" and out["non_commercial"] is False and "apikey=k1" in f.calls[0][0]


# ---------------------------------------------------------------------------------------------------------------
# HTTP padrão (urllib), sem rede
# ---------------------------------------------------------------------------------------------------------------

def test_urllib_fetch_timeout_e_erros(monkeypatch):
    def boom(exc):
        def f(*a, **k):
            raise exc
        return f
    monkeypatch.setattr(B.urllib.request, "urlopen", boom(socket.timeout("t")))
    with pytest.raises(ProviderError) as e:
        B.urllib_fetch("https://x.invalid/", {}, 1)
    assert e.value.code == "timeout"
    monkeypatch.setattr(B.urllib.request, "urlopen", boom(urllib.error.URLError(socket.timeout("t"))))
    with pytest.raises(ProviderError) as e:
        B.urllib_fetch("https://x.invalid/", {}, 1)
    assert e.value.code == "timeout"
    monkeypatch.setattr(B.urllib.request, "urlopen", boom(urllib.error.URLError("Name or service not known")))
    with pytest.raises(ProviderError) as e:
        B.urllib_fetch("https://x.invalid/", {}, 1)
    assert e.value.code == "http_error"
    monkeypatch.setattr(B.urllib.request, "urlopen", boom(urllib.error.HTTPError("https://x.invalid/", 503, "x", {}, None)))
    assert B.urllib_fetch("https://x.invalid/", {}, 1).status == 503


# ---------------------------------------------------------------------------------------------------------------
# cadastro e API
# ---------------------------------------------------------------------------------------------------------------

def test_propriedade_codigo_ibge():
    assert PropertyInfo(property_id="P1", name="F", ibge_municipality="5206206").ibge_municipality == "5206206"
    assert PropertyInfo(property_id="P1", name="F").ibge_municipality is None
    for bad in ("520620", "52062060", "9906206", "52O6206"):
        with pytest.raises(ValidationError):
            PropertyInfo(property_id="P1", name="F", ibge_municipality=bad)
    from agrofleet.simulator.operations import run_scenarios
    r = run_scenarios(["A"], seed=7, start=NOW, max_seconds=60)[0]
    assert r.property.property_id == "PROP-SIM-SC" and r.property.ibge_municipality == "5206206"


USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}


@pytest.fixture(scope="module")
def api():
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    app = create_app(st)
    with TestClient(app) as c:
        hs = {}
        for k, (u, p) in USERS.items():
            hs[k] = {"Authorization": f"Bearer {c.post('/auth/login', json={'username': u, 'password': p}).json()['access_token']}"}
        r = c.post("/demo/operations/load", headers=hs["admin"], params={"scenarios": "A,B", "hours": 0.05, "with_alerts": False})
        assert r.status_code == 200
        yield c, app, hs, r.json()["operations"]


def test_app_nao_busca_clima_na_subida(monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("rede acessada")
    monkeypatch.setattr(B.urllib.request, "urlopen", no_net)
    app = create_app(Settings(database_url="sqlite://", auth_secret="x" * 40, users=USERS, mqtt_host=None))
    with TestClient(app) as c:
        h = c.get("/health").json()["weather"]
    assert h["providers"] == ["inmet"] and h["inmet_token"] is False and h["cache"]["entries"] == 0


def test_endpoint_operacao_centroide_ibge_da_propriedade_e_nota(api):
    c, app, hs, ops = api
    svc, f, _c = make()
    app.state.weather = svc
    assert c.get(f"/operations/{ops[0]}/weather").status_code == 401
    r = c.get(f"/operations/{ops[0]}/weather", headers=hs["viewer"])
    assert r.status_code == 200
    out = r.json()
    loc = out["location"]
    assert loc["source"] == "field_centroid" and loc["fictitious"] is True and loc["note"] == SIMULATED_NOTE
    assert loc["ibge_municipality"] == "5206206" and abs(loc["lat"] + 16.70) < 0.05 and abs(loc["lon"] + 47.60) < 0.05
    assert out["status"] == "ok" and out["operation_id"] == ops[0] and out["origin"] == "simulated"
    assert out["observation"]["station_code"] == "A036" and out["forecast"]["municipality"] == "Cristalina"
    assert f.count("/5206206") == 2
    assert c.get("/operations/NAO-EXISTE/weather", headers=hs["viewer"]).status_code == 404


def test_endpoint_operacao_ultima_posicao_quando_talhao_sem_geometria(api):
    c, app, hs, ops = api
    from backend.app.db import FieldRow, OperationRow
    with app.state.SF() as s:
        fid = s.get(OperationRow, ops[1]).spec["field_id"]
        row = s.get(FieldRow, fid)
        keep, row.geojson = row.geojson, {}
        s.commit()
    try:
        app.state.weather = make()[0]
        out = c.get(f"/operations/{ops[1]}/weather", headers=hs["viewer"]).json()
        assert out["location"]["source"] == "last_position" and out["location"]["fictitious"] is True and out["status"] == "ok"
    finally:
        with app.state.SF() as s:
            s.get(FieldRow, fid).geojson = keep
            s.commit()


def test_endpoint_falha_do_provedor_nunca_5xx(api):
    c, app, hs, ops = api
    app.state.weather = make({"/estacao/proxima/": ProviderError("timeout", "tempo esgotado (8 s)"), "/previsao/": J({}, status=500)})[0]
    r = c.get(f"/operations/{ops[0]}/weather", headers=hs["viewer"])
    assert r.status_code == 200 and r.json()["status"] == "unavailable" and r.json()["message"] == "Sem dados meteorológicos"

    def crash(url, headers, timeout):
        raise RuntimeError("bug inesperado")
    app.state.weather = WeatherService(WeatherConfig(), fetch=crash, utcnow=lambda: NOW)
    r = c.get(f"/operations/{ops[0]}/weather", headers=hs["viewer"])
    assert r.status_code == 200 and r.json()["status"] == "unavailable"


def test_endpoint_weather_por_ponto(api):
    c, app, hs, _ops = api
    svc, f, _c = make()
    app.state.weather = svc
    out = c.get("/weather", params={"lat": -16.70, "lon": -47.60, "ibge": "5206206"}, headers=hs["viewer"]).json()
    assert out["status"] == "ok" and out["location"]["source"] == "query" and out["location"]["fictitious"] is False
    assert out["location"]["note"] is None
    assert c.get("/weather", params={"lat": -16.7, "lon": -47.6, "ibge": "52062"}, headers=hs["viewer"]).status_code == 422
    assert c.get("/weather", params={"lat": -16.7}, headers=hs["viewer"]).status_code == 422
    assert c.get("/weather", params={"lat": -116.7, "lon": 0}, headers=hs["viewer"]).status_code == 422
    assert c.get("/weather", params={"lat": -16.7, "lon": -47.6}).status_code == 401
    only_ibge = c.get("/weather", params={"ibge": "5206206"}, headers=hs["viewer"]).json()
    assert only_ibge["status"] == "ok" and only_ibge["observation"]["distance_basis"] == "inmet_municipality"
    assert "/weather" in c.get("/openapi.json").json()["paths"]
