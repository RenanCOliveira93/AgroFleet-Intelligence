from .base import AdapterError, AdapterResult, NotImplementedAdapter
from .geofiles import read_field_boundaries, read_geojson, read_gpx
from .isoxml import read_isoxml
from .tabular import read_csv, read_json

# Status honesto de cada fonte. "tested" = coberta por teste automatizado nesta entrega.
REGISTRY = {
    "csv": {"status": "implemented", "tested": True, "note": "mapeamento de colunas/unidades explícito"},
    "json": {"status": "implemented", "tested": True, "note": "lista, {points}, NDJSON ou envelope versionado"},
    "geojson": {"status": "implemented", "tested": True, "note": "só com tempo por ponto/vértice"},
    "gpx": {"status": "implemented", "tested": True, "note": "só com <time> por ponto"},
    "mqtt": {"status": "implemented", "tested": True, "note": "schema versionado agrofleet/v1; testado com Mosquitto real"},
    "simulator": {"status": "implemented", "tested": True, "note": "SIMULADO (origin=simulated)"},
    "isoxml": {"status": "not_implemented", "tested": False, "note": "sem amostra válida nem parser validado"},
    "manufacturer_rest": {"status": "not_implemented", "tested": False,
                          "note": "nenhuma API de fabricante documentada foi usada; nenhuma integração inventada"},
}
