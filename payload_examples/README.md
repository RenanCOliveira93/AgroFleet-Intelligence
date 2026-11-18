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
| `mqtt_envelope_v11_diesel.json` | publicar em `agrofleet/v1/TRATOR-EX-1/telemetry` | aceito (1.1): rpm, carga, horímetro, combustível %, L/h, rumo; campos de bateria e calda ficam `None` |
| `mqtt_envelope_v11_electric.json` | publicar em `agrofleet/v1/PULV-EX-E1/telemetry` | aceito (1.1): SOC, kWh, kW, rumo e calda; campos de motor e combustível ficam `None` |
| `mqtt_envelope_v11_invalid_fuel_level.json` | idem diesel | **rejeitado**: `fuel_level_pct` = 124 (faixa 0-100) |
| `telemetry_v11_units.csv` | upload `kind=csv`, `origin=simulated`, `units={"speed":"km/h","fuel_level":"fraction","fuel_rate":"gal/h","battery_soc":"fraction","battery_energy":"wh","battery_power":"w","heading":"rad"}` | 4 linhas aceitas e convertidas (6,71 gal/h → 25,4 L/h; 21600 Wh → 21,6 kWh; 0,6545 rad → 37,5°). Sem declarar `fraction`, os valores entram como 0,62 % e a qualidade sinaliza `unit_suspect` (não corrige) |

Unidades canônicas no JSON/MQTT: m/s, bar, L/min, L, m, L/ha e, na 1.1, rpm, %, h, L/h, kWh, kW, graus. No CSV, declare `units` ou use o sufixo no nome da coluna (`speed_kmh`).

Os exemplos 1.1 usam `origin: "simulated"`: os valores são ilustrativos e não vêm de nenhuma máquina.
