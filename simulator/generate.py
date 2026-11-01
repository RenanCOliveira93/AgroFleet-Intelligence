"""Gera arquivos SIMULADOS reprodutíveis (CSV, JSON, GeoJSON de talhões e verdade do simulador).

Uso: python simulator/generate.py [--seed 7] [--out sample_data] [--seconds 10800]
Tudo é fictício (origin=simulated). A verdade-terreno (eventos injetados, áreas planejadas) vai em sim_truth.json.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agrofleet.schema import point_to_dict, wrap  # noqa: E402
from agrofleet.simulator.fleet import simulate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="sample_data")
    ap.add_argument("--seconds", type=int, default=3 * 3600)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pts, truth = simulate(seed=a.seed, max_seconds=a.seconds)
    cols = ["machine_id", "ts", "origin", "lat", "lon", "speed_ms", "state", "gnss_fix", "accuracy_m", "flow_lpm", "pressure_bar", "tank_level_l",
            "consumption_l", "working_width_m", "application_rate_lha", "error_codes", "property_id", "field_id", "operator_id",
            "q_gnss", "q_speed", "q_flow", "q_pressure", "q_tank_level"]
    with open(out / "sim_fleet.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in pts:
            d = point_to_dict(p)
            row = []
            for c in cols:
                if c.startswith("q_"):
                    row.append(d["sensor_quality"].get(c[2:], ""))
                elif c == "error_codes":
                    row.append(";".join(d[c]))
                else:
                    v = d.get(c)
                    row.append("" if v is None else v)
            w.writerow(row)
    with open(out / "sim_fleet.ndjson", "w") as f:
        for i, p in enumerate(pts):
            f.write(json.dumps(wrap(p, f"sim-{a.seed}-{i}")) + "\n")
    (out / "sim_fields.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": k, "origin": "simulated"}, "geometry": g} for k, g in truth.fields.items()]}, indent=1))
    (out / "sim_truth.json").write_text(json.dumps({"events": truth.events, "areas": truth.areas, "meta": truth.meta}, indent=1))
    print(f"{len(pts)} pontos simulados -> {out}/ (sim_fleet.csv, sim_fleet.ndjson, sim_fields.geojson, sim_truth.json)")


if __name__ == "__main__":
    main()
