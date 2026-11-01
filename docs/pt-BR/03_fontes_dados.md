# 03 — Fontes de dados

## 3.1 Status de cada fonte (verdade, sem maquiagem)

O mesmo registro está em `agrofleet/adapters/__init__.py` (`REGISTRY`) e é exposto em `GET /limits`.

| Fonte | Status | Testada | Observação |
|---|---|---|---|
| CSV | implementado | sim | mapeamento de colunas e unidades explícito |
| JSON | implementado | sim | lista, `{"points": [...]}`, NDJSON ou envelope versionado |
| GeoJSON | implementado | sim | **só com tempo por vértice/ponto** |
| GPX | implementado | sim | **só com `<time>` por ponto** |
| MQTT | implementado | sim | schema versionado `agrofleet/v1`; testado com Mosquitto real (Podman) |
| Simulador | implementado | sim | **SIMULADO** (`origin = simulated`) |
| ISOXML / TASKDATA | **não implementado** | não | sem amostra válida nem parser validado |
| REST de fabricante | **não implementado** | não | nenhuma API de fabricante documentada foi usada; nenhuma integração inventada |

Nenhum conjunto de dados público ou privado de máquina real foi baixado ou usado. Tudo que foi medido aqui vem do simulador (seção 3.6). Se você tem dados reais, o caminho está em [12](12_proximos_passos.md).

## 3.2 O modelo canônico (`TelemetryPoint`)

Definido em `agrofleet/schema.py` (pydantic v2, `extra="forbid"`). Unidades fixas: **m/s, bar, L/min, L, m, L/ha**. Conversões acontecem nos adaptadores, nunca no núcleo.

| Campo | Tipo | Regra |
|---|---|---|
| `machine_id` | str 1–64 | obrigatório |
| `ts` | datetime | **obrigatório e com fuso**; normalizado para UTC; sem fuso é rejeitado |
| `origin` | `simulated`/`real`/`unknown` | padrão `unknown` |
| `lat`, `lon` | float | juntos ou nenhum; intervalo válido; **(0,0) rejeitado** |
| `speed_ms` | ≥ 0 | |
| `state` | `working`/`transport`/`idle`/`refilling`/`fault`/`off`/`unknown` | |
| `gnss_fix` | `none`/`single`/`dgps`/`rtk_float`/`rtk_fixed`/`unknown` | |
| `accuracy_m` | ≥ 0 | precisão reportada pelo receptor |
| `flow_lpm`, `pressure_bar`, `tank_level_l` | ≥ 0 | NaN/Inf rejeitados |
| `consumption_l` | ≥ 0 | contador acumulado; pode zerar |
| `working_width_m` | > 0 | |
| `application_rate_lha` | ≥ 0 | taxa reportada pelo controlador |
| `error_codes` | lista de str | |
| `sensor_quality` | `{gnss,speed,flow,pressure,tank_level} → ok/degraded/failed/unknown` | chave desconhecida é rejeitada |
| `property_id`, `field_id`, `operator_id` | str | **nunca inferidos** pelo sistema |

Regra: **todo campo opcional ausente é `None`**. Não existe preenchimento por padrão, nem pelo último valor.

## 3.3 Adaptadores tabulares (CSV / JSON)

`agrofleet/adapters/tabular.py`. O que cada decisão faz e por quê:

- **Aliases de coluna** (`machine`, `timestamp`, `velocidade`, `vazao`, …) → o mapeamento efetivo e as colunas ignoradas voltam em `meta` (`columns_mapped`, `columns_ignored`). *Alternativa descartada:* detecção "esperta" por conteúdo, que erra em silêncio.
- **Unidades** (`UNITS`): km/h, mph, kn, kPa, MPa, psi, L/s, L/h, m³, cm, ft… A unidade só é deduzida do **nome** da coluna (`speed_kmh`) — e isso gera um aviso — ou declarada em `units=`. Unidade desconhecida é **erro**.
- **Fuso:** timestamp sem fuso é rejeitado, a menos que o chamador informe `assume_tz` (por exemplo `America/Sao_Paulo`). Isso transfere a decisão (e a responsabilidade) para quem conhece a fonte.
- **Estado:** vocabulário em PT/EN (`trabalhando`, `spraying`…); valor não reconhecido fica `unknown` e é **contado em aviso** (`state_map=` permite mapear).
- **Valores vazios** (`""`, `NA`, `null`, `-`…) → `None`.
- **Rejeição por linha**, com número da linha e motivo; o restante do arquivo é processado.

Exemplo real de saída (notebook 01): 7 linhas → 5 válidas, 2 rejeitadas (`timestamp sem fuso horário`, `coordenada (0,0) rejeitada`), mais o aviso `unidade km/h deduzida do NOME da coluna`.

## 3.4 GeoJSON e GPX

`agrofleet/adapters/geofiles.py`. Uma trilha é geometria; **telemetria precisa de tempo**. Portanto:

- `LineString` sem `properties.times` (ou `coordinateProperties.times`) com um tempo por vértice é **rejeitado** — não inventamos timestamp (nem velocidade).
- GPX: pontos sem `<time>` são descartados e **contados**; GPX define tempo em UTC.
- Esses formatos só trazem posição, tempo (e `speed` se existir): o resto fica `None`, e um aviso diz isso.
- `read_field_boundaries` lê **limites de talhão** (Polygon/MultiPolygon válidos, com `properties.id`) — geometria de referência, não telemetria.

Exemplos aceitos e rejeitados: `payload_examples/` (cobertos por `tests/test_payload_examples.py`).

## 3.5 MQTT: contrato versionado

- Tópico: `agrofleet/v1/<machine_id>/telemetry`.
- Envelope: `{schema_version, type:"telemetry", message_id, sent_at, data:{...TelemetryPoint}}`.
- `schema_version` com **major diferente** (`2.0`) é rejeitado; `1.x` aceito. Não "adivinhamos" versões futuras.
- `data.machine_id` **deve ser igual** ao do tópico (evita que um dispositivo publique em nome de outro).
- Campo desconhecido (`extra="forbid"`) → mensagem rejeitada, vira registro em `/ingest/rejects` (mantemos os 500 mais recentes).
- QoS 1 no cliente; ver em [09](09_limitacoes.md) por que isso não garante entrega até o banco.

## 3.6 O simulador (origem **simulada**)

`agrofleet/simulator/fleet.py`. Quatro pulverizadores, semente fixa (`seed=7`), reprodutível bit a bit (teste de determinismo). **Local fictício** (origem em −16,70 / −47,60).

| Máquina | Cenário injetado | Verdade-terreno (do simulador) |
|---|---|---|
| SIM-01 | normal | espaçamento 22 m com barra 24 m ⇒ sobreposição analítica ≈ 8,3% |
| SIM-02 | parada por falha | `stop` 10:10:30–10:17:30 |
| SIM-03 | perda de GNSS + dados faltantes | `gnss_loss` 10:08:30–10:12:30; `missing_data` 10:03:24–10:05:54 |
| SIM-04 | vazão anormal | `abnormal_flow` 10:14:15–10:19:15 |

Modelo: passadas paralelas de comprimento *L*, velocidade ~N(*v*, 0,15 m/s) suavizada, vazão = taxa·v·w/600 + ruído, pressão base + ruído, fila de tarefas com reabastecimento, ruído GNSS de 2 cm (RTK fixed), 0,35 m na reconvergência RTK-float. Total: **11 331 amostras** em 4 máquinas.

**O que o simulador não representa:** vento, relevo, deriva, falhas reais de bico, comportamento real de bomba/válvula, falhas de controle de seção, multipath, cabeceiras irregulares, operadores. Qualquer afirmação de desempenho aqui vale **para o simulador**.

## 3.7 Verificação de qualidade (`agrofleet/quality.py`)

Antes de qualquer análise, um arquivo ou lote recebe `assess()`:

| Flag | O que detecta |
|---|---|
| `duplicates` | mesma `(machine_id, ts)` repetida |
| `out_of_order` | amostras fora de ordem no arquivo (reordenadas na análise) |
| `gaps` | lacunas > 30 s |
| `position_jumps` | velocidade implícita > 40 m/s entre posições |
| `unit_suspect` | valores que sugerem unidade trocada |
| `counter_reset` | contador acumulado que zera |
| `future` / `ancient` timestamps | relógio errado |

Veredito: `ok` / `warnings` / `blocking` (`blocking` quando não há nenhuma linha válida). Na interface, o upload mostra o relatório **antes** de gravar. No notebook 01, um arquivo com defeitos injetados gerou `warnings` com 1 duplicata, 1 fora de ordem, 1 salto e 1 lacuna de 270 s — exatamente os defeitos plantados.

## 3.8 Escolhendo a fonte para o seu caso

| Você tem… | Use | Cuidado |
|---|---|---|
| Exportação de plataforma em CSV | `read_csv` com `units`, `assume_tz`, `origin="real"` | confirme unidades e fuso com o fornecedor |
| Telemetria ao vivo própria | MQTT `agrofleet/v1` (ver [manual de integração](manual_integracao.md)) | `origin` é **declaração de quem envia** |
| Trilha de app de celular | GPX/GeoJSON com tempo | sem vazão, estado ou pressão |
| TASKDATA/time log ISOXML | **ainda não** | ver [12](12_proximos_passos.md) |
| API de fabricante | **ainda não** | exige documentação e credenciais do fabricante |
