"""Verificação INDEPENDENTE contra o PostGIS real (via API da pilha no ar): compara
  (a) comprimento da trilha: UTM (Python) vs geography/elipsoide (PostGIS) vs pyproj.Geod;
  (b) área do talhão: UTM (Python/shapely) vs geography (PostGIS);
e grava experiments/geodesy_check.json. Requer a pilha com PostgreSQL+PostGIS (compose) e senha de admin em AGROFLEET_E2E_ADMIN_PASSWORD."""
import json
import os
import sys
from pathlib import Path

import requests
from pyproj import Geod

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agrofleet.analytics.frame import to_frame  # noqa: E402
from agrofleet.simulator.fleet import simulate  # noqa: E402

URL = os.environ.get("AGROFLEET_E2E_URL", "http://localhost:8081")
pw = os.environ["AGROFLEET_E2E_ADMIN_PASSWORD"]
tok = requests.post(f"{URL}/api/auth/login", json={"username": "admin", "password": pw}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
requests.delete(f"{URL}/api/data", params={"origin": "simulated"}, headers=H)
requests.post(f"{URL}/api/demo/load", headers=H).raise_for_status()
pts, truth = simulate()
df = to_frame(pts)
out = {"db": requests.get(f"{URL}/api/health").json()["db"], "origin": "simulated", "machines": {}}
for mid, fid in [("SIM-01", "TALHAO-A"), ("SIM-02", "TALHAO-B"), ("SIM-03", "TALHAO-C"), ("SIM-04", "TALHAO-D")]:
    pg = requests.get(f"{URL}/api/postgis/check/{mid}", params={"field_id": fid}, headers=H).json()
    cov = requests.get(f"{URL}/api/coverage", params={"machine_ids": mid, "field_id": fid}, headers=H).json()["summary"]
    g = df[df.machine_id == mid].dropna(subset=["lat", "lon"])
    geod_len = Geod(ellps="WGS84").line_length(g.lon.to_numpy(), g.lat.to_numpy())
    m = requests.get(f"{URL}/api/metrics/{mid}", headers=H).json()
    out["machines"][mid] = {
        "track_length_m": {"postgis_geography": pg["track_length_m_geography_all_points"], "pyproj_geod": geod_len,
                           "python_utm_metrics_distance_km_x1000": m["distance_km"] * 1000,
                           "note": "as 2 primeiras usam TODOS os pontos com posição (inclui reta sobre perda de GNSS); a 3ª exclui saltos e lacunas por definição"},
        "field_area_m2": {"postgis_geography": pg["field_area_m2_geography"], "python_utm": cov["field_ha"] * 1e4, "truth_planar_m2": truth.areas[mid]["field_ha"] * 1e4},
        "points_within_field_postgis_st_within": pg["points_within_field"], "samples_with_position": int(len(g))}
    e = out["machines"][mid]
    e["rel_diff_track_postgis_vs_geod"] = e["track_length_m"]["postgis_geography"] / geod_len - 1
    e["rel_diff_area_utm_vs_geography"] = e["field_area_m2"]["python_utm"] / e["field_area_m2"]["postgis_geography"] - 1
(ROOT / "experiments" / "geodesy_check.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: {"d_track": round(v["rel_diff_track_postgis_vs_geod"], 7), "d_area": round(v["rel_diff_area_utm_vs_geography"], 6)} for k, v in out["machines"].items()}))
requests.delete(f"{URL}/api/data", params={"origin": "simulated"}, headers=H)
