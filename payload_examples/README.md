# Exemplos de payload

Todos fictícios. Cada exemplo é verificado por `tests/test_payload_examples.py`: o comportamento descrito aqui é o que o código faz.

| Arquivo | Como usar | Resultado esperado |
|---|---|---|
| `mqtt_envelope_valid.json` | publicar em `agrofleet/v1/FIELD-TEST-1/telemetry` | aceito, com todos os campos |
| `mqtt_envelope_valid_sparse.json` | idem | aceito; `lat`, `lon`, `flow_lpm`, `speed_ms` ficam `None` (nunca 0) |
| `mqtt_envelope_invalid_major_version.json` | idem | **rejeitado**: versão de schema incompatível (2.0) |
| `mqtt_envelope_invalid_command_field.json` | idem | **rejeitado**: campo desconhecido `set_speed_ms` (não existe canal de comando) |
| *(o mesmo envelope válido em tópico de outra máquina)* | publicar em `agrofleet/v1/OUTRA/telemetry` | **rejeitado**: `machine_id` difere do tópico |
| `telemetry_minimal.csv` | upload `kind=csv`, `origin=real` | aceito; `speed_kmh` (18) vira 5,0 m/s, com aviso de unidade deduzida do nome |
| `telemetry_rejected_rows.csv` | upload `kind=csv`, `origin=real` | 2 válidas e 4 rejeitadas (sem fuso, `(0,0)`, vazão negativa, `lat` sem `lon`); a linha com vazão vazia entra com `None`. Com `assume_tz=UTC`: 3 válidas |
| `telemetry_sem_fuso.csv` | upload sem `assume_tz` | 2 linhas, **ambas rejeitadas** (timestamp sem fuso). Com `assume_tz=America/Sao_Paulo`, aceitas e normalizadas para UTC (12:00 local → 15:00Z) |
| `telemetry_list.json` | upload `kind=json` | aceito; `null` continua `None` |
| `track_with_times.geojson` | upload `kind=geojson`, `machine_id=G1` | 3 pontos aceitos |
| `track_without_times.geojson` | idem | **recusado** (sem tempo por vértice; não inventamos timestamps) |
| `track_with_times.gpx` / `track_without_time.gpx` | upload `kind=gpx`, `machine_id=G2` | 3 pontos aceitos / 2 descartados e contados |
| `field_boundary.geojson` | `POST /fields/upload` | talhão `F1` (polígono válido) |

Unidades canônicas no JSON/MQTT: m/s, bar, L/min, L, m, L/ha. No CSV, declare `units` ou use o sufixo no nome da coluna (`speed_kmh`).
