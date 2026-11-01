# Manual de integração

Para quem **conecta fontes de dados**, consome a API ou administra a pilha.

## 1. Mapa das integrações

| Integração | Status | Seção |
|---|---|---|
| MQTT (`agrofleet/v1`) | implementado, testado com Mosquitto real | 2 |
| Upload CSV/JSON/GeoJSON/GPX | implementado, testado | 3 |
| REST/OpenAPI + WebSocket | implementado, testado | 4 |
| ISOXML / TASKDATA | **não implementado** (recusado com motivo) | 6 |
| API REST de fabricante | **não implementado** (nenhuma documentação usada) | 6 |

## 2. MQTT

**Tópico:** `agrofleet/v1/<machine_id>/telemetry` — o `machine_id` do payload **deve** ser idêntico ao do tópico.

**Envelope** (exemplo completo em `payload_examples/mqtt_envelope_valid.json`):

```json
{
  "schema_version": "1.0",
  "type": "telemetry",
  "message_id": "FIELD-TEST-1-000001",
  "sent_at": "2026-05-01T12:00:00+00:00",
  "data": {
    "machine_id": "FIELD-TEST-1",
    "ts": "2026-05-01T12:00:00+00:00",
    "origin": "real",
    "lat": -20.1, "lon": -45.1, "speed_ms": 5.0,
    "state": "working", "gnss_fix": "rtk_fixed", "accuracy_m": 0.03,
    "flow_lpm": 72.0, "pressure_bar": 3.0, "tank_level_l": 1480.0,
    "working_width_m": 24.0, "application_rate_lha": 100.0,
    "error_codes": [], "sensor_quality": {"flow": "ok", "pressure": "ok"},
    "field_id": "F1", "operator_id": "OP-9"
  }
}
```

**Regras** (todas verificadas por `tests/test_payload_examples.py`):

| Regra | Efeito |
|---|---|
| `ts` com fuso explícito | sem fuso → rejeitado |
| Unidades fixas: m/s, bar, L/min, L, m, L/ha | converta **antes** de publicar |
| Campo ausente = omita ou envie `null` | nunca envie 0 para "não sei" (ver `mqtt_envelope_valid_sparse.json`) |
| `lat`/`lon` juntos; `(0,0)` rejeitado | receptor sem fix: **omita** a posição e envie `gnss_fix:"none"` |
| Campo desconhecido (por exemplo `set_speed_ms`) | **mensagem rejeitada** — não há canal de comando |
| `schema_version` major ≠ 1 | rejeitado (`mqtt_envelope_invalid_major_version.json`) |
| `origin` | `real` é declaração sua; use `simulated` para teste |
| QoS recomendado | 1; a entrega até o banco **não** é garantida (fila em memória, ver [09](09_limitacoes.md)) |

**Credenciais e ACL:** o broker exige usuário/senha. Usuário *device*: só **escreve** em `agrofleet/v1/+/telemetry`. Usuário *ingest* (backend): só **lê**. Senhas e ACL são geradas em runtime por `infra/mosquitto/entrypoint.sh` a partir de `MQTT_DEVICE_*` e `MQTT_INGEST_*`.

**TLS:** o cliente (`MqttSource`/`MqttPublisher`) aceita `tls_cafile`; a verificação de certificado **nunca** é desligada. Para produção, habilite um listener 8883 no Mosquitto com certificados e defina `MQTT_CAFILE` no backend. O compose local usa 1883 sem TLS (**só para desenvolvimento**).

**Publicar de teste** com o simulador (origem `simulated`):

```bash
export MQTT_DEVICE_PASSWORD=...        # a do seu .env
python simulator/publish.py --host localhost --port 1883 --user device --password-env MQTT_DEVICE_PASSWORD --speedup 20
```

**Rejeições:** mensagens inválidas viram registros em `GET /ingest/rejects` (papel `analyst`), mantidas as 500 mais recentes.

## 3. Upload de arquivos

`POST /uploads` (multipart; papel `analyst`). Campos: `file`, `kind` (`csv|json|geojson|gpx`), `origin`, `machine_id` (obrigatório para GeoJSON/GPX), `assume_tz`, `units` (JSON), `commit` (booleano; **padrão falso**: só valida).

```bash
TOKEN=$(curl -s localhost:8080/api/auth/login -H 'content-type: application/json' \
  -d '{"username":"analyst","password":"..."}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 1) valida sem gravar
curl -s localhost:8080/api/uploads -H "authorization: Bearer $TOKEN" \
  -F file=@payload_examples/telemetry_rejected_rows.csv -F kind=csv -F origin=real -F assume_tz=UTC | python -m json.tool

# 2) grava (somente se o veredito for aceitável)
curl -s localhost:8080/api/uploads -H "authorization: Bearer $TOKEN" \
  -F file=@payload_examples/telemetry_minimal.csv -F kind=csv -F origin=real -F commit=true
```

**Mapeamento de colunas CSV/JSON** (aliases reconhecidos, ver `ALIASES` em `agrofleet/adapters/tabular.py`): `machine_id` (`machine`, `id`, `maquina`…), `ts` (`timestamp`, `time`…), `lat`/`lon`, velocidade (`speed_ms`, `speed`, `speed_kmh`), `state`, `gnss_fix`, `flow`, `pressure`, `tank_level`, `consumption`, `width`, `rate`, `error_codes`, `origin`, `property_id`, `field_id`, `operator_id`.

**Unidades** (fator para a canônica): velocidade m/s·km/h·mph·kn; pressão bar·kPa·MPa·psi; vazão L/min·L/s·L/h; volume L·m³; largura m·cm·ft. Declare no campo `units`, por exemplo `{"speed":"km/h","pressure":"kpa"}`. Sem declaração, a unidade só é deduzida do **nome** da coluna (`speed_kmh`), com aviso; unidade desconhecida é erro.

**Fuso:** timestamps sem fuso são rejeitados a menos que `assume_tz` seja informado (ex.: `America/Sao_Paulo`). **Confirme com o fornecedor**: errar o fuso desloca todos os horários em silêncio.

**Limites de talhão:** `POST /fields/upload` com GeoJSON de `Polygon`/`MultiPolygon` válidos e `properties.id`.

Exemplos aceitos e rejeitados: `payload_examples/` (veja o README da pasta).

## 4. API REST e WebSocket

- **OpenAPI/Swagger:** `GET /docs` (e `/openapi.json`). Pelo nginx do compose, a API fica sob `/api/` (por exemplo, `/api/docs`, `/api/health`).
- **Autenticação:** `POST /auth/login` `{username, password}` → `{access_token, role, expires_in}`; envie `Authorization: Bearer <token>`.
- **Papéis:** `viewer` < `analyst` < `admin` (tabela no [manual do usuário](manual_usuario.md)).

| Grupo | Endpoints principais |
|---|---|
| meta | `GET /health`, `GET /limits` (inclui o registro de adaptadores) |
| frota | `GET /summary`, `/machines`, `/machines/{id}/telemetry`, `/machines/{id}/track`, `/playback` |
| talhões | `GET /fields`, `POST /fields/upload` |
| análise | `GET /metrics/{id}`, `/coverage`, `/reports` (`format=json|csv`), `/postgis/check/{id}` |
| alertas | `GET /alerts`, `POST /alerts/recompute`, `GET|PUT /config/alerts` |
| ingestão | `POST /uploads`, `GET /uploads`, `GET /ingest/rejects` |
| demo | `POST /demo/load`, `GET /demo/evaluation`, `DELETE /data?origin=` |
| ROI | `GET /roi/example`, `POST /roi`, `POST /roi/from-telemetry` |

Parâmetros comuns: `origin` (`all|simulated|real|unknown`), `start`/`end` (ISO 8601 com fuso), `group_by` (`machine|property|field|operator`).

**WebSocket `/ws`:** abra a conexão e envie em até 5 s `{"type":"auth","token":"<jwt>"}`. Sem isso o servidor fecha com código 4401. Depois, recebe `{"type":"hello"}`, `{"type":"telemetry","points":[...]}` e `{"type":"alert","alert":{...}}`. É **só servidor → cliente**; qualquer coisa que o cliente envie é ignorada.

## 5. Variáveis de ambiente

| Variável | Para quê | Observação |
|---|---|---|
| `POSTGRES_USER/PASSWORD/DB` | banco | senha **obrigatória** no compose |
| `MQTT_DEVICE_USER/PASSWORD` | quem publica | obrigatória |
| `MQTT_INGEST_USER/PASSWORD` | backend lê | obrigatória |
| `MQTT_PORT_HOST` | porta publicada do broker | padrão 1883 |
| `AGROFLEET_AUTH_SECRET` | assinatura JWT | ≥ 32 caracteres; obrigatória no compose |
| `AGROFLEET_{ADMIN,ANALYST,VIEWER}_USER/PASSWORD` | usuários | usuário **sem senha não é criado** |
| `AGROFLEET_TOKEN_TTL_S` | validade do token | padrão 28800 |
| `AGROFLEET_MAX_UPLOAD_MB` | limite de upload | padrão 50 |
| `AGROFLEET_ALERT_WINDOW_S` | janela das regras ao vivo | padrão 600 |
| `AGROFLEET_CORS_ORIGINS` | CORS (lista por vírgula) | vazio = sem CORS |
| `MQTT_HOST/PORT/USER/PASSWORD/CAFILE` | cliente MQTT do backend | `MQTT_HOST` vazio desliga o MQTT |
| `HTTP_PORT` | porta do frontend | padrão 8080 |
| `SIM_SPEEDUP/SEED/MAX_SECONDS` | simulador (perfil `sim`) | |

Segredos ficam **só** no `.env` (fora do repositório; `.gitignore` já o exclui). `.env.example` tem apenas `CHANGE_ME_*`.

## 6. ISOXML e API de fabricante (não implementados)

**ISOXML/TASKDATA:** `POST /uploads` com `kind=isoxml` devolve **501** com o motivo; `read_isoxml` levanta `NotImplementedAdapter`. Para implementar (ver [12](12_proximos_passos.md)): obter amostras `TASKDATA.XML` + `TLG*.XML/BIN` **válidas**, avaliar um parser existente, mapear DDI → campos canônicos, escrever um adaptador que devolva `AdapterResult` e **cobri-lo com testes sobre as amostras reais**.

**API de fabricante:** só com documentação oficial e credenciais. Mantenha os segredos em variáveis de ambiente e devolva `AdapterResult` (rejeições com motivo, avisos, `meta` de mapeamento).

**Escrevendo um adaptador novo (contrato):** devolver `AdapterResult(points=[TelemetryPoint…], rejected=[…], warnings=[…], meta={…})`; nunca preencher campo ausente; nunca inventar timestamp; declarar unidades; registrar em `REGISTRY` com `status` e `tested` **verdadeiros**.

## 7. Operação (compose)

```bash
cp .env.example .env && $EDITOR .env           # troque todos os CHANGE_ME
podman compose up -d --build                    # ou docker compose (não testado aqui)
podman compose --profile sim up -d simulator    # opcional: publica a frota SIMULADA
podman compose restart frontend                 # depois de recriar o backend
podman compose down                             # (-v apaga os volumes pgdata/mqttdata)
```

Backup do banco: `podman compose exec db pg_dump -U $POSTGRES_USER $POSTGRES_DB > backup.sql`.

## 8. Testes de infraestrutura

Os testes `-m infra` precisam de PostGIS e Mosquitto **reais**. Com contêineres avulsos (portas altas para não colidir com a pilha):

```bash
# PostGIS (imagem amd64; em Apple Silicon roda emulada: aguarde o healthcheck)
podman run -d --name af-test-pg -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=agrofleet -p 55432:5432 docker.io/postgis/postgis:16-3.4

# Mosquitto com a MESMA configuração/ACL do compose
podman run -d --name af-test-mq -p 18883:1883 \
  -e MQTT_DEVICE_USER=device -e MQTT_DEVICE_PASSWORD=devpw -e MQTT_INGEST_USER=ingest -e MQTT_INGEST_PASSWORD=ingpw \
  -v "$PWD/infra/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  -v "$PWD/infra/mosquitto/entrypoint.sh:/entrypoint.sh:ro" \
  --entrypoint /bin/sh docker.io/library/eclipse-mosquitto:2 /entrypoint.sh

export AGROFLEET_TEST_PG_URL='postgresql+psycopg://postgres:testpw@localhost:55432/agrofleet'
export AGROFLEET_TEST_MQTT=localhost:18883
export AGROFLEET_TEST_MQTT_DEVICE_USER=device AGROFLEET_TEST_MQTT_DEVICE_PASSWORD=devpw
export AGROFLEET_TEST_MQTT_INGEST_USER=ingest AGROFLEET_TEST_MQTT_INGEST_PASSWORD=ingpw
PYTHONPATH=. pytest -m infra

podman rm -f af-test-pg af-test-mq
```

**E2E (Playwright/Chromium)** contra a pilha no ar: `pip install playwright && playwright install chromium`; defina `AGROFLEET_E2E_URL` (padrão `http://localhost:8081`, **a porta do seu `HTTP_PORT`**) e `AGROFLEET_E2E_{ADMIN,ANALYST,VIEWER}_PASSWORD` (os valores padrão do teste são senhas locais de desenvolvimento; use as do seu `.env`); pare o simulador antes (`podman compose stop simulator`) e rode `PYTHONPATH=. pytest -m e2e`. **Atenção:** o teste E2E apaga e recarrega os dados da pilha em execução.
