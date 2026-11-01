import json
from datetime import timezone

import pytest

from agrofleet import adapters as A
from agrofleet.adapters.mqtt import parse_message
from agrofleet.schema import SCHEMA_VERSION, State, TelemetryPoint, wrap
from agrofleet.simulator.fleet import simulate
from agrofleet.quality import assess

GOOD = {"machine_id": "M1", "ts": "2026-03-10T10:00:00Z", "lat": -16.7, "lon": -47.6}


def test_ts_ingenuo_rejeitado_e_utc_normalizado():
    with pytest.raises(ValueError):
        TelemetryPoint(machine_id="M1", ts="2026-03-10T10:00:00")
    p = TelemetryPoint(machine_id="M1", ts="2026-03-10T07:00:00-03:00")
    assert p.ts.tzinfo == timezone.utc and p.ts.hour == 10


@pytest.mark.parametrize("bad", [{"lat": -16.7}, {"lat": 0.0, "lon": 0.0}, {"lat": 91.0, "lon": 0.5}, {"speed_ms": -1},
                                 {"flow_lpm": float("nan")}, {"sensor_quality": {"radar": "ok"}}, {"extra": 1}])
def test_pontos_invalidos(bad):
    with pytest.raises(ValueError):
        TelemetryPoint(**{"machine_id": "M1", "ts": "2026-03-10T10:00:00Z", **bad})


def test_opcionais_sao_none_nao_zero():
    p = TelemetryPoint(machine_id="M1", ts="2026-03-10T10:00:00Z")
    assert p.flow_lpm is None and p.pressure_bar is None and p.lat is None and p.state == State.unknown


def test_csv_unidades_e_nulos():
    csv = "machine_id,timestamp,lat,lon,speed,pressure,flow,state\nM1,2026-03-10T10:00:00Z,-16.7,-47.6,18,300,NA,spraying\n"
    r = A.read_csv(csv, units={"speed": "km/h", "pressure": "kPa"}, origin="simulated")
    p = r.points[0]
    assert p.speed_ms == pytest.approx(5.0) and p.pressure_bar == pytest.approx(3.0) and p.flow_lpm is None
    assert p.state == State.working and r.meta["units"]["speed"] == "km/h"


def test_csv_sem_fuso_exige_declaracao():
    csv = "machine_id,ts,lat,lon\nM1,2026-03-10 10:00:00,-16.7,-47.6\n"
    r = A.read_csv(csv)
    assert not r.points and "fuso" in r.rejected[0]["reason"]
    r2 = A.read_csv(csv, assume_tz="America/Sao_Paulo")
    assert r2.points[0].ts.hour == 13


def test_csv_coluna_obrigatoria_e_unidade_invalida():
    with pytest.raises(A.AdapterError):
        A.read_csv("a,b\n1,2\n")
    with pytest.raises(A.AdapterError):
        A.read_csv("machine_id,ts\nM1,2026-03-10T10:00:00Z\n", units={"speed": "furlong/fortnight"})


def test_csv_linhas_ruins_sao_contadas_nao_escondidas():
    csv = "machine_id,ts,lat,lon,speed_ms\nM1,2026-03-10T10:00:00Z,-16.7,-47.6,5\nM1,xx,-16.7,-47.6,5\nM1,2026-03-10T10:00:02Z,-16.7,-47.6,-3\n"
    r = A.read_csv(csv, origin="real")
    assert len(r.points) == 1 and r.n_rejected_total == 2 and r.n_input == 3


def test_json_formas_e_envelope():
    lst = A.read_json(json.dumps([GOOD, {**GOOD, "ts": "2026-03-10T10:00:01Z"}]))
    assert len(lst.points) == 2
    nd = A.read_json("\n".join(json.dumps(x) for x in [GOOD, {**GOOD, "ts": "2026-03-10T10:00:01Z"}]))
    assert len(nd.points) == 2
    pts, _ = simulate(max_seconds=3)
    env = [wrap(pts[0], "id1"), {**wrap(pts[1], "id2"), "schema_version": "2.0"}]
    r = A.read_json(json.dumps(env))
    assert len(r.points) == 1 and r.n_rejected_total == 1 and "envelope" in r.rejected[0]["reason"]
    assert r.points[0].sensor_quality  # qualidade por sensor atravessa o JSON


def test_geojson_pontos_e_linha_sem_tempo():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-47.6, -16.7]}, "properties": {"time": "2026-03-10T10:00:00Z", "speed_ms": 4}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-47.6, -16.7], [-47.61, -16.7]]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}, "properties": {}}]}
    r = A.read_geojson(json.dumps(fc), machine_id="M1", origin="real")
    assert len(r.points) == 1 and r.n_rejected_total == 2
    assert any("não inventamos" in x["reason"] for x in r.rejected)
    ls = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-47.6, -16.7], [-47.61, -16.7]]},
          "properties": {"times": ["2026-03-10T10:00:00Z", "2026-03-10T10:00:10Z"]}}
    assert len(A.read_geojson(json.dumps(ls), machine_id="M1").points) == 2


GPX = """<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="-16.7" lon="-47.6"><time>2026-03-10T10:00:00Z</time></trkpt>
<trkpt lat="-16.7001" lon="-47.6"><time>2026-03-10T10:00:01Z</time></trkpt>
<trkpt lat="-16.7002" lon="-47.6"></trkpt></trkseg></trk></gpx>"""


def test_gpx_sem_tempo_descartado():
    r = A.read_gpx(GPX, machine_id="M1", origin="real")
    assert len(r.points) == 2 and r.n_rejected_total == 1 and r.points[0].speed_ms is None


def test_limites_de_talhao():
    ok = {"type": "Feature", "properties": {"id": "T1"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}
    assert list(A.read_field_boundaries(json.dumps(ok))) == ["T1"]
    bow = {**ok, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}}
    with pytest.raises(A.AdapterError):
        A.read_field_boundaries(json.dumps(bow))


def test_isoxml_e_rest_nao_implementados():
    with pytest.raises(A.NotImplementedAdapter):
        A.read_isoxml(b"<ISO11783_TaskData/>")
    assert A.REGISTRY["isoxml"]["status"] == "not_implemented" and A.REGISTRY["manufacturer_rest"]["status"] == "not_implemented"


def test_mqtt_parse_message():
    pts, _ = simulate(max_seconds=2)
    p = pts[0]
    ok = parse_message(f"agrofleet/v1/{p.machine_id}/telemetry", json.dumps(wrap(p, "a")).encode())
    assert ok.machine_id == p.machine_id and SCHEMA_VERSION == "1.0"
    for topic, payload, frag in [("agrofleet/v1/OTHER/telemetry", json.dumps(wrap(p, "a")), "difere do tópico"),
                                 ("outro/topico", json.dumps(wrap(p, "a")), "fora do padrão"),
                                 (f"agrofleet/v1/{p.machine_id}/telemetry", "{nao json", "não é JSON"),
                                 (f"agrofleet/v1/{p.machine_id}/telemetry", json.dumps({**wrap(p, "a"), "schema_version": "9.0"}), "incompatível"),
                                 (f"agrofleet/v1/{p.machine_id}/telemetry", json.dumps({**wrap(p, "a"), "comando": "stop"}), "")]:
        with pytest.raises(ValueError) as e:
            parse_message(topic, payload.encode())
        assert frag in str(e.value)


def test_qualidade_aponta_problemas():
    csv = ("machine_id,ts,lat,lon,speed_ms,pressure_bar,flow_lpm,state\n"
           "M1,2026-03-10T10:00:00Z,-16.7,-47.6,5,3,70,working\n"
           "M1,2026-03-10T10:00:00Z,-16.7,-47.6,5,3,70,working\n"            # duplicada
           "M1,2026-03-10T10:05:00Z,-16.7,-47.6,60,300,70,working\n"          # lacuna + unidade suspeita
           "M1,2026-03-10T10:00:30Z,-16.7,-47.6,5,3,70,working\n"             # fora de ordem
           "M1,2026-03-10T10:05:01Z,-16.6,-47.6,5,3,70,working\n")            # salto de ~11 km em 1 s
    q = assess(A.read_csv(csv, origin="simulated"))
    codes = {f["code"] for f in q["flags"]}
    assert {"duplicates", "gaps", "out_of_order", "position_jumps", "unit_suspect"} <= codes and q["verdict"] == "warnings"
    assert assess(A.read_csv("machine_id,ts\nM1,garbage\n"))["verdict"] == "blocking"


def test_simulador_deterministico_e_marcado_como_simulado():
    a, ta = simulate(seed=3, max_seconds=300)
    b, tb = simulate(seed=3, max_seconds=300)
    c, _ = simulate(seed=4, max_seconds=300)
    assert [p.model_dump_json() for p in a] == [p.model_dump_json() for p in b]
    assert [p.model_dump_json() for p in a] != [p.model_dump_json() for p in c]
    assert {p.origin for p in a} == {"simulated"} and ta.meta["origin"] == "simulated"
    assert {p.machine_id for p in a} == {"SIM-01", "SIM-02", "SIM-03", "SIM-04"}
