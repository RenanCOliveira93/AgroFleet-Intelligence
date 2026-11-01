"""Os exemplos de payload_examples/ são parte da documentação: este teste garante que cada um se comporta como descrito."""
import json
from pathlib import Path

import pytest

from agrofleet.adapters import AdapterError, read_csv, read_field_boundaries, read_geojson, read_gpx, read_json
from agrofleet.adapters.mqtt import parse_message

EX = Path(__file__).resolve().parents[1] / "payload_examples"
TOPIC = "agrofleet/v1/FIELD-TEST-1/telemetry"


def raw(name):
    return (EX / name).read_bytes()


def test_envelope_valid_full_and_sparse():
    p = parse_message(TOPIC, raw("mqtt_envelope_valid.json"))
    assert p.origin == "real" and p.flow_lpm == 72.0 and p.field_id == "F1"
    q = parse_message(TOPIC, raw("mqtt_envelope_valid_sparse.json"))
    assert q.lat is None and q.flow_lpm is None and q.speed_ms is None      # ausente continua None, nunca 0


def test_envelope_invalid_major_version():
    with pytest.raises(ValueError, match="incompatível"):
        parse_message(TOPIC, raw("mqtt_envelope_invalid_major_version.json"))


def test_envelope_rejects_unknown_field_such_as_a_command():
    with pytest.raises(ValueError, match="set_speed_ms"):
        parse_message(TOPIC, raw("mqtt_envelope_invalid_command_field.json"))


def test_envelope_machine_id_must_match_topic():
    with pytest.raises(ValueError, match="difere do tópico"):
        parse_message("agrofleet/v1/OUTRA/telemetry", raw("mqtt_envelope_valid.json"))


def test_minimal_csv_accepted_with_unit_conversion():
    r = read_csv(raw("telemetry_minimal.csv"), origin="real")
    assert r.n_rejected_total == 0
    assert r.points[0].speed_ms == pytest.approx(5.0)       # 18 km/h


def test_rejected_rows_csv():
    r = read_csv(raw("telemetry_rejected_rows.csv"), origin="real")
    assert len(r.points) == 2 and r.n_rejected_total == 4
    assert r.points[1].flow_lpm is None                    # campo vazio -> None, não 0
    reasons = " | ".join(x["reason"] for x in r.rejected)
    assert "fuso" in reasons and "(0,0)" in reasons and "flow_lpm" in reasons and "lat e lon" in reasons
    ok = read_csv(raw("telemetry_rejected_rows.csv"), origin="real", assume_tz="UTC")   # declarando o fuso, a linha 3 entra
    assert len(ok.points) == 3 and ok.n_rejected_total == 3


def test_json_list_keeps_null():
    r = read_json(raw("telemetry_list.json"), origin="real")
    assert len(r.points) == 2 and r.points[1].flow_lpm is None


def test_tracks_need_time():
    assert len(read_geojson(raw("track_with_times.geojson"), machine_id="G1", origin="real").points) == 3
    none = read_geojson(raw("track_without_times.geojson"), machine_id="G1", origin="real")
    assert len(none.points) == 0 and none.n_rejected_total == 1
    assert len(read_gpx(raw("track_with_times.gpx"), machine_id="G2", origin="real").points) == 3
    nt = read_gpx(raw("track_without_time.gpx"), machine_id="G2", origin="real")
    assert len(nt.points) == 0 and nt.n_rejected_total == 2


def test_field_boundary():
    f = read_field_boundaries(raw("field_boundary.geojson"))
    assert list(f) == ["F1"]


def test_csv_without_timezone_needs_assume_tz():
    r = read_csv(raw("telemetry_sem_fuso.csv"), origin="real")
    assert len(r.points) == 0 and r.n_rejected_total == 2
    ok = read_csv(raw("telemetry_sem_fuso.csv"), origin="real", assume_tz="America/Sao_Paulo")
    assert len(ok.points) == 2 and ok.points[0].ts.hour == 15      # 12:00 em São Paulo (UTC-3) = 15:00Z
