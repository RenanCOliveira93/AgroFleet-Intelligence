# AgroFleet Intelligence: guia técnico

Documentação de engenharia do AgroFleet Intelligence: arquitetura, fontes de dados, instalação e execução, integração e API, metodologia, validação, limitações e solução de problemas.

> **Escopo e segurança.** O sistema monitora e audita; **não controla** direção, freio nem aplicação e **não gera comandos** para máquinas. Alertas são informativos. Não é um sistema de segurança funcional (ISO 13849 / ISO 25119).
>
> **Honestidade dos dados.** Todos os números deste repositório vêm de um **simulador** (`origin = simulated`). Nenhuma telemetria de máquina real foi usada. ISOXML/TASKDATA e API de fabricante **não estão implementados**.

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Arquitetura](#2-arquitetura)
3. [Fontes de dados e proveniência](#3-fontes-de-dados-e-proveniência)
4. [Instalação, execução e reprodução](#4-instalação-execução-e-reprodução)
5. [Integração e API](#5-integração-e-api)
6. [Uso da interface](#6-uso-da-interface)
7. [Pipeline e metodologia](#7-pipeline-e-metodologia)
8. [Validação, métricas e testes](#8-validação-métricas-e-testes)
9. [Interpretação dos resultados](#9-interpretação-dos-resultados)
10. [Limitações conhecidas](#10-limitações-conhecidas)
11. [Segurança](#11-segurança)
12. [Licenças e atribuições](#12-licenças-e-atribuições)
13. [Solução de problemas](#13-solução-de-problemas)
14. [Referências](#14-referências)

---

## 1. Visão geral

Cada máquina (pulverizador terrestre autônomo ou convencional com telemetria) reporta, a cada segundo, posição, velocidade, estado operacional, vazão, pressão, nível do tanque e qualidade do sinal GNSS. O sistema lê esses registros, valida, armazena e responde:

| Pergunta | Onde |
|---|---|
| Estado e posição atual da máquina | Painel Frota (`frontend/src/Fleet.tsx`), `/machines`, WebSocket |
| O que aconteceu em um instante | Reprodução (`Playback.tsx`), `/playback` |
| Área coberta, repetida e não coberta | `agrofleet/analytics/coverage.py`, `/coverage` |
| Tempo por estado, distância, produtividade, custo/ha | `agrofleet/analytics/metrics.py`, `/metrics`, `/reports` |
| Progresso da missão, ETA com faixa, autonomia e viabilidade | `agrofleet/analytics/mission.py`, `autonomy.py`, `/operations/{id}/status` |
| Vazão coerente com taxa x velocidade x largura | `agrofleet/analytics/anomalies.py`, `/alerts` |
| Qualidade dos dados recebidos | `agrofleet/quality.py`, `/uploads` |
| ROI com premissas declaradas | `agrofleet/analytics/roi.py`, `/roi` |

### 1.1 O que o sistema não é

- **Não controla nem comanda** máquinas. Garantido em três camadas: (1) o schema `TelemetryPoint` tem `extra="forbid"`, e um campo desconhecido (por exemplo `set_speed_ms`) é rejeitado; (2) a ACL do Mosquitto permite ao dispositivo apenas escrever em `agrofleet/v1/+/telemetry` e ao backend apenas ler; não existe tópico de comando (`infra/mosquitto/entrypoint.sh`); (3) o WebSocket é unidirecional (servidor para cliente).
- **Não diagnostica bico entupido**: a pressão da barra é uma grandeza agregada.
- **Não foi validado com telemetria real.**

### 1.2 Princípios de projeto

1. **Nulo nunca vira zero.** Campo ausente é `None` (`null` na API); `flow_lpm = 0` preenchido por padrão seria indistinguível de "bomba desligada".
2. **Origem é dado de primeira classe.** `origin` (`simulated`, `real`, `unknown`) percorre banco, API e interface.
3. **Rejeitar com motivo, não corrigir em silêncio.** Linhas inválidas são contadas e explicadas; unidade só é deduzida do nome da coluna e registrada; timestamp sem fuso exige `assume_tz`.
4. **Não interpolar o que não foi observado.** Perda de GNSS e lacunas não são preenchidas.
5. **Verdade-terreno onde possível.** O simulador injeta eventos com horário exato e áreas analíticas.
6. **Registro honesto de adaptadores.** O que não existe é declarado em `GET /limits`.

---

## 2. Arquitetura

### 2.1 Componentes

```mermaid
flowchart LR
    subgraph Fontes
        SIM[Simulador<br/>origin=simulated]
        DEV[Dispositivo/gateway<br/>origin=real, declarado]
        FILE[Arquivos<br/>CSV / JSON / GeoJSON / GPX]
    end
    subgraph Infra
        MQ[(Mosquitto<br/>auth + ACL)]
        PG[(PostgreSQL + PostGIS)]
    end
    subgraph Backend[Backend FastAPI]
        ING[Ingestão MQTT<br/>thread, fila, lote]
        UP[Upload + qualidade]
        AN[Analytics<br/>coverage · metrics · anomalies · roi]
        API[REST + OpenAPI]
        WS[WebSocket /ws]
    end
    UI[Frontend React/TS<br/>MapLibre GL JS]

    SIM -- publish --> MQ
    DEV -- publish --> MQ
    MQ -- subscribe --> ING
    FILE -- POST /uploads --> UP
    ING --> PG
    UP --> PG
    ING -- regras de alerta --> WS
    PG --> AN --> API
    API --> UI
    WS --> UI
```

Não existe seta do sistema para a máquina.

### 2.2 Fluxo ao vivo (MQTT para a tela)

1. O simulador ou dispositivo publica um envelope v1 em `agrofleet/v1/<machine_id>/telemetry`.
2. A thread MQTT do backend valida tópico, versão, schema e `machine_id`; mensagens inválidas viram rejeições com motivo.
3. Pontos válidos entram em uma fila em memória (capacidade 200 000).
4. Um laço assíncrono a cada 0,5 s drena até 5 000 pontos, grava com `INSERT ... ON CONFLICT DO NOTHING` (idempotente), carrega uma janela (padrão 600 s) das máquinas presentes no lote, roda as regras de anomalia (sem ML), faz `upsert` de alertas (funde por sobreposição de ±15 s) e envia `telemetry`/`alert` por WebSocket. Desde a Etapa 5, o mesmo laço também avalia os alertas operacionais das máquinas com operação ativa (no máximo a cada 30 s por máquina) e roda, a cada 30 s, um vigia de conexão que dispara mesmo sem mensagens (7.10). Desde a Etapa 6, com cliente WebSocket conectado, envia também `operation_status` da operação corrente de cada máquina do lote (no máximo a cada `AGROFLEET_STATUS_INTERVAL_S`, padrão 5 s) e no ciclo do vigia; alertas, status e WebSocket usam um único cálculo incremental da missão (7.11).
5. Exceções no lote são registradas em `stats.last_error` e não derrubam o laço; fila cheia incrementa `dropped_queue_full` (visível em `/health`).

Implementação: `backend/app/live.py`.

### 2.3 Modelo de dados

Tabelas: `telemetry` (única por `(machine_id, ts, origin)`, restrição `uq_tel_machine_ts_origin`; coluna `geom` gerada e com índice GIST no PostgreSQL), `fields` (limites de talhão), `alerts` (id = `sha1(machine|type|rule|start)[:16]`), `uploads`, `ingest_rejects`, `users` (hash scrypt) e `settings`. `field_id` em telemetria é declarado pela fonte, nunca inferido. Em PostgreSQL, `ON CONFLICT DO NOTHING ... RETURNING` é usado para contar o que de fato entrou (o `rowcount` do driver não é confiável nesse caso). Código: `backend/app/db.py`.

### 2.4 Implantação (Docker Compose)

| Serviço | Descrição |
|---|---|
| `frontend` | nginx 1.27 com o build Vite, porta `HTTP_PORT`; proxy de `/api` e upgrade de WebSocket para o backend |
| `backend` | FastAPI/uvicorn, usuário não-root |
| `db` | `postgis/postgis:16-3.4` (imagem amd64; emulada em Apple Silicon) |
| `mosquitto` | `eclipse-mosquitto:2`; senhas e ACL geradas em runtime por `infra/mosquitto/entrypoint.sh` |
| `simulator` | perfil `sim`: publica a frota simulada por MQTT |

O compose recusa subir sem `POSTGRES_PASSWORD`, `MQTT_*_PASSWORD` e `AGROFLEET_AUTH_SECRET`. Não há senha padrão.

### 2.5 Decisões de arquitetura

| Decisão | Motivo | Trade-off |
|---|---|---|
| Geometria em Python/UTM; PostGIS para auditoria e filtro espacial | análises iterativas e testes sem banco | SQL (`ST_Union`) escala melhor em volumes muito grandes |
| SQLite nos testes, PostgreSQL em execução real | testes rápidos e herméticos | testes `-m infra` cobrem as diferenças |
| Fila em memória no backend | simplicidade | perda de mensagens se o processo cair |
| Regras no streaming; ML sob demanda | regra é explicável e barata | ML sem rótulos gera muito alarme |
| TimescaleDB não usado | volume atual não exige | pode ser adotado depois |
| JWT HS256 com 3 papéis | suficiente para o escopo | sem revogação nem MFA |

### 2.6 Estrutura do código

```
agrofleet/          núcleo: schema, geo, quality, adapters/, analytics/, simulator/, weather/ (clima)
backend/app/        FastAPI, banco, auth, pipeline ao vivo
frontend/           React + TypeScript + MapLibre GL JS
simulator/          CLIs generate.py e publish.py
infra/              Dockerfiles, nginx, Mosquitto
notebooks/          3 notebooks (fontes build_*.py)
payload_examples/   exemplos aceitos e rejeitados (testados)
sample_data/        dados SIMULADOS gerados (semente 7)
scripts/, experiments/   validação geodésica e sua saída
tests/              pytest (unit, API, infra, E2E)
```

---

## 3. Fontes de dados e proveniência

### 3.1 Status dos adaptadores

O mesmo registro está em `agrofleet/adapters/__init__.py` (`REGISTRY`) e é exposto em `GET /limits`.

| Fonte | Status | Testada | Observação |
|---|---|---|---|
| CSV | implementado | sim | mapeamento de colunas e unidades explícito |
| JSON | implementado | sim | lista, `{"points": [...]}`, NDJSON ou envelope versionado |
| GeoJSON | implementado | sim | somente com tempo por vértice/ponto |
| GPX | implementado | sim | somente com `<time>` por ponto |
| MQTT | implementado | sim | schema versionado `agrofleet/v1`; testado com Mosquitto real (Podman) |
| Simulador | implementado | sim | `origin = simulated` |
| ISOXML / TASKDATA | **não implementado** | não | sem amostra válida nem parser validado |
| API REST de fabricante | **não implementado** | não | nenhuma documentação de fabricante usada |

Nenhum conjunto de dados público ou privado de máquina real foi baixado ou usado. Não há dados externos no repositório.

### 3.2 Modelo canônico (`TelemetryPoint`)

Definido em `agrofleet/schema.py` (pydantic v2, `extra="forbid"`), versão **1.1**. Unidades fixas: **m/s, bar, L/min, L, m, L/ha** e, desde a 1.1, **rpm, %, h, L/h, kWh, kW, graus**. Conversões acontecem nos adaptadores, nunca no núcleo.

| Campo | Tipo | Regra |
|---|---|---|
| `machine_id` | str 1-64 | obrigatório |
| `ts` | datetime | obrigatório, com fuso; normalizado para UTC |
| `origin` | `simulated`/`real`/`unknown` | padrão `unknown` |
| `lat`, `lon` | float | juntos ou nenhum; (0,0) rejeitado |
| `speed_ms` | >= 0 | |
| `state` | `working`/`transport`/`idle`/`refilling`/`fault`/`off`/`unknown` | |
| `gnss_fix` | `none`/`single`/`dgps`/`rtk_float`/`rtk_fixed`/`unknown` | |
| `accuracy_m` | >= 0 | precisão reportada pelo receptor |
| `flow_lpm`, `pressure_bar`, `tank_level_l` | >= 0 | NaN/Inf rejeitados |
| `consumption_l` | >= 0 | contador acumulado; pode zerar |
| `working_width_m` | > 0 | |
| `application_rate_lha` | >= 0 | taxa reportada pelo controlador |
| `error_codes` | lista de str | |
| `sensor_quality` | `{gnss,speed,flow,pressure,tank_level,engine,fuel_level,fuel_rate,battery,heading}` para `ok/degraded/failed/unknown` | chave desconhecida é rejeitada; as 5 últimas chaves são da 1.1 |
| `property_id`, `field_id`, `operator_id` | str | nunca inferidos |

**Campos 1.1 — motor e energia** (todos opcionais; lista única em `schema.ENGINE_ENERGY_FIELDS`). A referência indica de onde um dispositivo futuro poderia ler o valor; a disponibilidade varia por fabricante e **não foi validada com máquina real**.

| Campo | Unidade | Faixa aceita | Referência típica |
|---|---|---|---|
| `engine_rpm` | rpm | >= 0 | J1939 SPN 190 (Engine Speed) |
| `engine_load_pct` | % | 0-250 | J1939 SPN 92 (Percent Load At Current Speed) |
| `engine_hours_h` | h | >= 0 | J1939 SPN 247 (Engine Total Hours), horímetro acumulado |
| `fuel_level_pct` | % | 0-100 | J1939 SPN 96 (Fuel Level 1) |
| `fuel_rate_lph` | L/h | >= 0 | J1939 SPN 183 (Engine Fuel Rate) |
| `battery_soc_pct` | % | 0-100 | BMS: estado de carga |
| `battery_energy_kwh` | kWh | >= 0 | BMS: energia remanescente disponível |
| `battery_power_kw` | kW | qualquer valor finito | BMS: **+ descarga**, **− recarga/regeneração** |
| `heading_deg` | graus | [0, 360) | GNSS: rumo verdadeiro sobre o solo (COG) |

- `tank_level_l` continua sendo o **tanque de calda** (produto). Combustível só aparece em `fuel_level_pct`. Os **litros** de combustível dependem da capacidade do tanque, que é cadastro da máquina e não telemetria; não existe campo `fuel_level_l`.
- Uma máquina a combustão envia os campos de motor e combustível; uma elétrica envia os de bateria. O que a máquina não envia fica `None`: o sistema não deduz uma fonte de energia a partir da outra.
- Na coluna `telemetry.ingested_at`, o servidor grava o horário de gravação, que **não** é o `ts` reportado pela máquina. É a base para detectar telemetria atrasada ou desconectada sem depender do relógio do dispositivo (nem do relógio simulado de `publish.py --speedup`). Linhas gravadas antes da 1.1 ficam com `NULL`.

Todo campo opcional ausente é `None`: não há preenchimento por padrão nem pelo último valor.

**Compatibilidade e migração (1.0 → 1.1).** Payloads 1.0 continuam válidos sem alteração (testado com os exemplos 1.0). O tópico continua `agrofleet/v1`. Como o modelo usa `extra="forbid"`, um backend 1.0 **rejeita** os campos novos: atualize o backend antes dos dispositivos. No banco, `init_db` executa `add_missing_columns`, uma migração **aditiva e idempotente**: só adiciona colunas anuláveis que faltam (`ALTER TABLE … ADD COLUMN`), nunca remove nem reescreve, e recusa com erro uma coluna nova não anulável. Foi testada em SQLite e no PostGIS real, preservando a coluna gerada `geom`.

### 3.2.1 Cadastro (`agrofleet/registry.py`)

O cadastro é **configuração informada por pessoas**, não telemetria. Fica nas tabelas `properties`, `machine_profiles` e `operations`; cada linha guarda o modelo validado em `spec` (JSON), o que deixa o cadastro evoluir sem migração.

| Modelo | Campos principais | Regras |
|---|---|---|
| `PropertyInfo` | `property_id`, `name`, `timezone` (IANA, padrão `America/Sao_Paulo`), `ibge_municipality` (opcional) | o fuso serve só para exibir horários e escolher o período da previsão do tempo; o armazenamento é sempre UTC. `ibge_municipality`: código IBGE do município com 7 dígitos, começando pelo código de uma UF (ex.: `5206206`); usado pelo clima (7.12) |
| `MachineProfile` | `name`, `kind` (trator, pulverizador, colheitadeira, outro), `energy_source` (diesel, elétrico, não informado), `autonomous`, `fuel_tank_capacity_l` ou `battery_capacity_kwh`, `energy_reserve_pct`, `energy_warning_pct`, `energy_critical_pct`, `autonomy_margin_pct`, `maintenance_interval_h` + `last_maintenance_engine_h` | só a capacidade da fonte declarada; crítico < aviso; crítico ≥ reserva; manutenção exige os dois campos e não se aplica a máquina elétrica |
| `OperationPlan` | `name`, `operation_type`, `machine_id`, `field_id`, `planned_area_ha` (vazio = área do talhão), `implement_width_m`, `start`/`end` (com fuso), `status` | máquina e talhão precisam existir; **operação, máquina e talhão com a mesma `origin`** |

Campo não informado fica `None`, e o cálculo que depende dele é marcado como indisponível: não há limite nem capacidade padrão escondidos. `GET /machines` inclui `name`, `kind` e `energy_source` do perfil **somente** se o perfil tiver a mesma origem da telemetria.

### 3.3 Adaptadores tabulares (CSV/JSON)

`agrofleet/adapters/tabular.py`:

- **Aliases de coluna** (`machine`, `timestamp`, `velocidade`, `vazao`, ...); o mapeamento efetivo e as colunas ignoradas voltam em `meta` (`columns_mapped`, `columns_ignored`).
- **Unidades**: km/h, mph, kn, kPa, MPa, psi, L/s, L/h, m³, cm, ft. A unidade é declarada em `units=` ou deduzida do nome da coluna (`speed_kmh`), o que gera aviso. Unidade desconhecida é erro.
- **Unidades 1.1** (chave de `units=`: unidades aceitas): `engine_hours`: h, min, s; `fuel_level` e `battery_soc`: %, fraction (0-1); `fuel_rate`: L/h, L/min, gal/h (galão americano); `battery_energy`: kWh, Wh; `battery_power`: kW, W; `heading`: deg, rad. Aliases de coluna incluem `rpm`, `carga_motor`, `horimetro`, `nivel_combustivel`, `soc`, `rumo` e `cog`.
- **Suspeita de unidade (só sinalizada, nunca corrigida)**: nível de combustível ou SOC sempre ≤ 1 % (fração 0-1 enviada como %?), rotação > 5000 rpm, consumo mediano > 150 L/h, energia de bateria mediana > 2000 kWh.
- **Fuso**: timestamp sem fuso é rejeitado, a menos que `assume_tz` seja informado (por exemplo `America/Sao_Paulo`). Errar o fuso desloca todos os horários em silêncio; confirme com o fornecedor.
- **Estado**: vocabulário PT/EN (`trabalhando`, `spraying`...); valor não reconhecido vira `unknown` e é contado em aviso (`state_map=` permite mapear).
- **Valores vazios** (`""`, `NA`, `null`, `-`) viram `None`.
- **Rejeição por linha**, com número da linha e motivo; o restante do arquivo é processado.

### 3.4 GeoJSON e GPX

`agrofleet/adapters/geofiles.py`. Telemetria precisa de tempo:

- `LineString` sem `properties.times` (ou `coordinateProperties.times`) com um tempo por vértice é rejeitado; timestamps não são inventados.
- GPX: pontos sem `<time>` são descartados e contados; GPX usa UTC.
- Esses formatos só trazem posição, tempo e (se existir) `speed`; o resto fica `None`, com aviso.
- `read_field_boundaries` lê limites de talhão (`Polygon`/`MultiPolygon` válidos, com `properties.id`); é geometria de referência, não telemetria.

### 3.5 Contrato MQTT

- Tópico: `agrofleet/v1/<machine_id>/telemetry`; `data.machine_id` deve ser igual ao do tópico.
- Envelope: `{schema_version, type:"telemetry", message_id, sent_at, data:{...TelemetryPoint}}`.
- `schema_version` com major diferente de 1 é rejeitado; `1.x` é aceito. O backend emite `1.1`. O minor é informativo: um envelope que declara `1.0` mas traz um campo 1.1 válido é aceito.
- Campo desconhecido gera rejeição, registrada em `GET /ingest/rejects` (500 mais recentes).

### 3.6 Simulador (origem simulada)

`agrofleet/simulator/fleet.py`. Quatro pulverizadores, semente fixa (`seed=7`), reprodutível (teste de determinismo). Local fictício (origem em -16,70 / -47,60).

| Máquina | Cenário injetado | Verdade-terreno |
|---|---|---|
| SIM-01 | normal | espaçamento 22 m com barra 24 m: sobreposição analítica ~8,3% |
| SIM-02 | parada por falha | `stop` 10:10:30-10:17:30 |
| SIM-03 | perda de GNSS + dados faltantes | `gnss_loss` 10:08:30-10:12:30; `missing_data` 10:03:24-10:05:54 |
| SIM-04 | vazão anormal | `abnormal_flow` 10:14:15-10:19:15 |

Modelo: passadas paralelas de comprimento L, velocidade ~N(v; 0,15 m/s) suavizada, vazão = taxa·v·w/600 + ruído, pressão base + ruído, fila de tarefas com reabastecimento, ruído GNSS de 2 cm (RTK fixed) e 0,35 m na reconvergência RTK-float. Total: 11 331 amostras em 4 máquinas.

Não representa: vento, relevo, deriva, falhas reais de bico, comportamento real de bomba/válvula, controle de seção, multipath, cabeceiras irregulares, operadores. Qualquer afirmação de desempenho vale para o simulador.

### 3.6.1 Simulador de operações (cenários A, B e C)

`agrofleet/simulator/operations.py`. É um módulo novo, ao lado do simulador original, que continua intacto. Tudo é `origin = simulated`, inclusive o cadastro gerado (propriedade "Fazenda Santa Clara (simulada)", máquinas, talhões e operações). Os **modelos de energia são sintéticos e não calibrados**: servem para testar ingestão, cobertura, autonomia e alertas com verdade conhecida, nunca para estimar o desempenho de uma máquina real.

| Cenário | Máquina | Talhão | O que exercita |
|---|---|---|---|
| A | `SIM-TR-320`, trator diesel, implemento de 12 m a 8,5 km/h | Talhão 03, 120 ha | consumo (L/h) variando com a carga do motor (resistência do solo + modo); nível de combustível com oscilação e resolução de 0,4%; horímetro; rumo. Completo em cerca de 12,3 h, consome cerca de 414 L de 570 L (autonomia suficiente) |
| B | `SIM-PE-07`, pulverizador elétrico autônomo, 12 m a 7,2 km/h | Talhão 05, 40 ha | potência = base + tração (velocidade e massa de calda) + bomba; SOC e kWh; reabastecimento de calda. Ao atingir 12% de SOC, a máquina simulada volta à base e a operação fica incompleta (cerca de 62%): **autonomia insuficiente** |
| C | `SIM-TR-410`, trator diesel | Talhão 04, 60 ha | falhas de **enlace** na entrega, sem alterar a medição: 90 s perdidos, 4 min guardados e reenviados em rajada, 5 min com 25 s de latência (chegam fora de ordem), 10 min com 5% de duplicação |

- **Geometria:** talhão convexo irregular, determinístico pela semente e com área exata. As passadas são paralelas, recortadas ao polígono e percorridas em boustrophedon; as manobras são segmentos fora de trabalho. Não há cabeceira trabalhada, então o teto de cobertura é `planned_coverage_in_field_ha` (cerca de 99,6%).
- **Verdade-terreno** (`truth`): combustível ou energia inicial e final, consumo médio em trabalho, passadas feitas, eventos (reabastecimento de calda, retorno à base, falhas de enlace) e conclusão.
- **Uso:** `POST /demo/operations/load?scenarios=A,B,C&hours=3` grava cadastro e telemetria, terminando *agora*. Para um teste ao vivo por MQTT: `POST …&with_telemetry=false&start=<T>` e depois `python simulator/publish.py --scenario A,B,C --start <T> --speedup 20`.
- **Verificado em teste** (`tests/test_simulator_operations.py`): a integral do consumo reportado bate com a verdade (±1% em L, ±2% em kWh); a queda do nível bate com o consumo (±0,5 p.p.); rumo coerente e ausente com a máquina parada; cada máquina só reporta os sensores do seu tipo; e a cobertura estimada fica entre −3% e +0,5% da planejada.

### 3.7 Verificação de qualidade (`agrofleet/quality.py`)

`assess()` é aplicado a cada arquivo ou lote antes da análise. Não corrige; sinaliza.

| Flag | Detecta |
|---|---|
| `duplicates` | mesma `(machine_id, ts)` repetida |
| `out_of_order` | amostras fora de ordem (reordenadas na análise) |
| `gaps` | lacunas > 30 s |
| `position_jumps` | velocidade implícita > 40 m/s entre posições |
| `unit_suspect` | velocidade > 25 m/s, pressão mediana > 15 bar, vazão mediana > 1500 L/min, largura > 60 m |
| `counter_reset` | contador acumulado que zera |
| `future` / `ancient` | relógio errado (> 5 min no futuro, antes de 2000) |

Veredito: `ok`, `warnings` ou `blocking` (sem nenhuma linha válida). No upload, o relatório é exibido antes de gravar.

### 3.8 Escolha da fonte

| Cenário | Fonte | Cuidado |
|---|---|---|
| Exportação de plataforma em CSV | `read_csv` com `units`, `assume_tz`, `origin="real"` | confirmar unidades e fuso |
| Telemetria ao vivo própria | MQTT `agrofleet/v1` | `origin` é declaração de quem envia |
| Trilha de aplicativo de celular | GPX/GeoJSON com tempo | sem vazão, estado ou pressão |
| TASKDATA/time log ISOXML | não suportado | ver seção 5.6 |
| API de fabricante | não suportado | exige documentação e credenciais |

---

## 4. Instalação, execução e reprodução

### 4.1 Requisitos

Podman (testado) ou Docker (não testado) com compose. Para testes locais: Python 3.11 e Node 22. Não há `pyproject.toml`; o pacote `agrofleet` está na raiz e exige `PYTHONPATH=.`.

### 4.2 Subir a pilha

```bash
cp .env.example .env            # troque TODOS os CHANGE_ME
podman compose up -d --build    # ou: docker compose up -d --build
podman compose --profile sim up -d simulator     # opcional: frota SIMULADA por MQTT
podman compose restart frontend                  # após recriar o backend
podman compose down                              # (-v apaga os volumes pgdata/mqttdata)
```

Interface em `http://localhost:${HTTP_PORT:-8080}`; Swagger em `/api/docs`. Backup do banco:

```bash
podman compose exec db pg_dump -U $POSTGRES_USER $POSTGRES_DB > backup.sql
```

### 4.3 Variáveis de ambiente

| Variável | Uso | Observação |
|---|---|---|
| `POSTGRES_USER/PASSWORD/DB` | banco | senha obrigatória no compose |
| `MQTT_DEVICE_USER/PASSWORD` | quem publica | obrigatória |
| `MQTT_INGEST_USER/PASSWORD` | backend lê | obrigatória |
| `MQTT_PORT_HOST` | porta publicada do broker | padrão 1883 |
| `AGROFLEET_AUTH_SECRET` | assinatura JWT | >= 32 caracteres; obrigatória no compose |
| `AGROFLEET_{ADMIN,ANALYST,VIEWER}_USER/PASSWORD` | usuários | usuário sem senha não é criado |
| `AGROFLEET_TOKEN_TTL_S` | validade do token | padrão 28800 |
| `AGROFLEET_MAX_UPLOAD_MB` | limite de upload | padrão 50 |
| `AGROFLEET_ALERT_WINDOW_S` | janela das regras ao vivo | padrão 600 |
| `AGROFLEET_STATUS_INTERVAL_S` | intervalo mínimo entre mensagens `operation_status` da mesma operação | padrão 5 |
| `AGROFLEET_OPS_CACHE_SIZE` | operações mantidas no cache incremental (LRU, por processo) | padrão 8 (~40 MB por operação de 5 h, ~65 MB de 12 h) |
| `AGROFLEET_CORS_ORIGINS` | CORS (lista por vírgula) | vazio = sem CORS |
| `AGROFLEET_WEATHER_PROVIDERS` | provedores de clima, em ordem de preferência (`inmet`, `metno`, `openmeteo`, `none`) | padrão `inmet` (ver 7.12) |
| `AGROFLEET_WEATHER_TIMEOUT_S` | tempo limite de cada busca de clima | padrão 8 |
| `AGROFLEET_WEATHER_MAX_AGE_S` | idade a partir da qual a observação é marcada `stale` | padrão 7200 (2 h) |
| `AGROFLEET_WEATHER_OBS_TTL_S` / `_FORECAST_TTL_S` / `_FAIL_TTL_S` | cache em memória da observação, da previsão e das falhas | padrão 1800 / 10800 / 120 |
| `AGROFLEET_WEATHER_IBGE` | município padrão (7 dígitos) quando a propriedade não tem `ibge_municipality` | vazio = sem padrão |
| `AGROFLEET_WEATHER_USER_AGENT` | User-Agent das buscas de clima | padrão `AgroFleet-Intelligence/1.1 (+https://github.com/RenanCOliveira93/AgroFleet-Intelligence)` |
| `AGROFLEET_INMET_TOKEN` | token da API de estações do INMET (só via Acordo de Cooperação Técnica) | vazio = só rotas públicas |
| `AGROFLEET_INMET_MAX_STATION_KM` | distância máxima do ponto à estação INMET | padrão 60 |
| `AGROFLEET_WEATHER_ALLOW_NONCOMMERCIAL` | `1` libera o Open-Meteo gratuito (não comercial) | só vale com `AGROFLEET_ENV=dev` |
| `OPENMETEO_BASE_URL` / `OPENMETEO_API_KEY` | endpoint e chave do plano pago do Open-Meteo | com chave, o Open-Meteo vale em qualquer ambiente |
| `MQTT_HOST/PORT/USER/PASSWORD/CAFILE` | cliente MQTT do backend | `MQTT_HOST` vazio desliga o MQTT |
| `HTTP_PORT` | porta do frontend | padrão 8080 |
| `SIM_SPEEDUP/SEED/MAX_SECONDS` | simulador (perfil `sim`) | |

Segredos ficam apenas no `.env`, que está no `.gitignore`. O `.env.example` contém apenas `CHANGE_ME_*`.

### 4.4 Ambiente de desenvolvimento

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=.
python -c "import agrofleet.schema as s; print(s.SCHEMA_VERSION)"     # 1.0
```

### 4.5 Reprodução dos dados e análises

```bash
python simulator/generate.py --seed 7 --out sample_data     # opções: --seed, --out, --seconds
```

Gera `sim_fleet.csv`, `sim_fleet.ndjson` (um envelope MQTT por linha), `sim_fields.geojson` e `sim_truth.json` (eventos injetados e áreas planejadas). Com a mesma semente a saída é determinística.

Publicar a frota simulada em um broker:

```bash
export MQTT_DEVICE_PASSWORD=...        # a do seu .env
python simulator/publish.py --host localhost --port 1883 --user device --password-env MQTT_DEVICE_PASSWORD --speedup 20
```

Opções de `publish.py`: `--host`, `--port`, `--user`, `--password-env`, `--cafile`, `--seed`, `--speedup`, `--repeat`, `--max-seconds`.

Notebooks (os `.ipynb` são gerados pelos `build_*.py`):

```bash
cd notebooks
python build_01.py && python build_02.py && python build_03.py
jupyter nbconvert --to notebook --execute --inplace 0*.ipynb
```

### 4.6 Testes

```bash
pytest                                                    # unitários + API (sem Docker)
(cd frontend && npm ci && npx tsc --noEmit && npx vitest run)
```

`pytest.ini` exclui por padrão os marcadores `e2e` e `infra`.

**Testes de infraestrutura (`-m infra`)** exigem PostGIS e Mosquitto reais. Com contêineres avulsos em portas altas:

```bash
# PostGIS (imagem amd64; em Apple Silicon roda emulada: aguarde o healthcheck)
podman run -d --name af-test-pg -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=agrofleet -p 55432:5432 docker.io/postgis/postgis:16-3.4

# Mosquitto com a mesma configuração/ACL do compose
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

**E2E (`-m e2e`, Playwright/Chromium)** contra a pilha no ar: `pip install playwright && playwright install chromium`; defina `AGROFLEET_E2E_URL` (padrão `http://localhost:8081`; use a porta do seu `HTTP_PORT`) e `AGROFLEET_E2E_{ADMIN,ANALYST,VIEWER}_PASSWORD` (os padrões do teste são senhas locais de desenvolvimento; use as do seu `.env`); pare o simulador antes (`podman compose stop simulator`) e rode `PYTHONPATH=. pytest -m e2e`. O teste E2E apaga e recarrega os dados da pilha em execução.

**Validação geodésica** (requer PostGIS no ar; escreve `experiments/geodesy_check.json`): `python scripts/validate_geodesy.py`.

---

## 5. Integração e API

### 5.1 Mapa das integrações

| Integração | Status | Seção |
|---|---|---|
| MQTT (`agrofleet/v1`) | implementado, testado com Mosquitto real | 5.2 |
| Upload CSV/JSON/GeoJSON/GPX | implementado, testado | 5.3 |
| REST/OpenAPI + WebSocket | implementado, testado | 5.4 |
| ISOXML / TASKDATA | não implementado (recusado com motivo) | 5.6 |
| API REST de fabricante | não implementado | 5.6 |

### 5.2 MQTT

Tópico `agrofleet/v1/<machine_id>/telemetry`. Exemplo completo em `payload_examples/mqtt_envelope_valid.json`:

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

Regras (cobertas por `tests/test_payload_examples.py`):

| Regra | Efeito |
|---|---|
| `ts` com fuso explícito | sem fuso: rejeitado |
| Unidades fixas: m/s, bar, L/min, L, m, L/ha | converter antes de publicar |
| Campo ausente: omitir ou enviar `null` | nunca enviar 0 para "desconhecido" (`mqtt_envelope_valid_sparse.json`) |
| `lat`/`lon` juntos; (0,0) rejeitado | receptor sem fix: omitir a posição e enviar `gnss_fix:"none"` |
| Campo desconhecido (por exemplo `set_speed_ms`) | mensagem rejeitada; não há canal de comando |
| `schema_version` com major diferente de 1 | rejeitado (`mqtt_envelope_invalid_major_version.json`) |
| `origin` | `real` é declaração do remetente; use `simulated` para teste |
| QoS | recomendado 1; a entrega até o banco não é garantida (fila em memória) |

**Credenciais e ACL.** O broker exige usuário e senha (`allow_anonymous false`). O usuário *device* só escreve em `agrofleet/v1/+/telemetry`; o usuário *ingest* (backend) só lê. Senhas e ACL são geradas em runtime por `infra/mosquitto/entrypoint.sh` a partir de `MQTT_DEVICE_*` e `MQTT_INGEST_*`.

**TLS.** O cliente (`MqttSource`/`MqttPublisher`) aceita `tls_cafile`; a verificação de certificado nunca é desligada. Para produção, habilite um listener 8883 no Mosquitto com certificados e defina `MQTT_CAFILE` no backend. O compose local usa 1883 sem TLS, apenas para desenvolvimento.

**Rejeições.** Mensagens inválidas viram registros em `GET /ingest/rejects` (papel `analyst`), mantidas as 500 mais recentes.

### 5.3 Upload de arquivos

`POST /uploads` (multipart; papel `analyst`). Campos: `file`, `kind` (`csv|json|geojson|gpx`), `origin`, `machine_id` (obrigatório para GeoJSON/GPX), `assume_tz`, `units` (JSON), `commit` (booleano; padrão falso: apenas valida).

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

O reenvio do mesmo arquivo não duplica dados (`stored: 0`, `duplicates_ignored` igual ao total); `blocking` impede a gravação.

**Colunas CSV/JSON** (aliases em `ALIASES`, `agrofleet/adapters/tabular.py`): `machine_id` (`machine`, `id`, `maquina`...), `ts` (`timestamp`, `time`...), `lat`/`lon`, velocidade (`speed_ms`, `speed`, `speed_kmh`), `state`, `gnss_fix`, `flow`, `pressure`, `tank_level`, `consumption`, `width`, `rate`, `error_codes`, `origin`, `property_id`, `field_id`, `operator_id`.

**Unidades aceitas:** velocidade m/s, km/h, mph, kn; pressão bar, kPa, MPa, psi; vazão L/min, L/s, L/h; volume L, m³; largura m, cm, ft. Declare em `units`, por exemplo `{"speed":"km/h","pressure":"kpa"}`.

**Limites de talhão:** `POST /fields/upload` com GeoJSON de `Polygon`/`MultiPolygon` válidos e `properties.id`.

**Exemplos** (`payload_examples/`, todos fictícios e verificados por `tests/test_payload_examples.py`): ver `payload_examples/README.md`.

### 5.4 API REST e WebSocket

- **OpenAPI/Swagger:** `GET /docs` e `/openapi.json`. Pelo nginx do compose, a API fica sob `/api/` (por exemplo `/api/docs`, `/api/health`).
- **Autenticação:** `POST /auth/login` com `{username, password}` retorna `{access_token, role, expires_in}`; envie `Authorization: Bearer <token>`.
- **Papéis:** `viewer` < `analyst` < `admin`.

| Papel | Pode |
|---|---|
| `viewer` | consultar frota, reprodução, cobertura, alertas, relatórios e ROI |
| `analyst` | tudo do viewer + upload, envio de limites de talhão, recomputar alertas |
| `admin` | tudo do analyst + alterar configuração de alertas, carregar/apagar a demonstração simulada |

| Grupo | Endpoints principais |
|---|---|
| meta | `GET /health`, `GET /limits` (inclui o registro de adaptadores) |
| frota | `GET /summary`, `/machines`, `/machines/{id}/telemetry`, `/machines/{id}/track`, `/playback` |
| talhões | `GET /fields`, `POST /fields/upload` |
| análise | `GET /metrics/{id}`, `/coverage`, `/reports` (`format=json\|csv`), `/postgis/check/{id}` |
| alertas | `GET /alerts` (filtros `status`, `category`, `severity`, `operation_id`; `sort=priority`), `POST /alerts/recompute` (`operational=true` reabre os alertas operacionais sustentados), `GET\|PUT /config/alerts`, `GET\|PUT /config/alerts/operational`, `POST /operations/{id}/alerts/replay` (analyst; com `end` antes do último dado é auditoria e não grava nada: `persisted=false`) |
| ingestão | `POST /uploads`, `GET /uploads`, `GET /ingest/rejects` |
| cadastro | `GET /registry/properties`, `PUT /registry/properties/{id}` (admin); `GET /registry/machines`, `GET\|PUT /registry/machines/{id}` (PUT: admin); `GET /operations`, `GET\|PUT /operations/{id}` (PUT: analyst) |
| operação (Etapa 4) | `GET /operations/{id}/status` (missão, autonomia, conexão, último estado, série por intervalo), `GET /operations/{id}/geometry` (talhão, trabalhado, pendente, fora do talhão, trajeto, última posição e rumo). Ambos aceitam `as_of` (ver 7.9). Desde a Etapa 6, calculados com cache incremental (7.11) |
| clima (Etapa 7) | `GET /operations/{id}/weather` (ponto da operação: centroide do talhão ou última posição) e `GET /weather?lat=&lon=&ibge=&tz=` (ponto ou município). Papel viewer. Falha do provedor nunca vira 5xx: volta `status: "unavailable"`, `message: "Sem dados meteorológicos"` e `reasons`. Ver 7.12 |
| demonstração | `POST /demo/load`, `POST /demo/operations/load` (cenários A/B/C), `GET /demo/evaluation`, `DELETE /data?origin=` (também apaga o cadastro dessa origem) |
| ROI | `GET /roi/example`, `POST /roi`, `POST /roi/from-telemetry` |

Parâmetros comuns: `origin` (`all|simulated|real|unknown`), `start`/`end` (ISO 8601 com fuso), `group_by` (`machine|property|field|operator`).

**WebSocket `/ws`.** Após abrir a conexão, envie em até 5 s `{"type":"auth","token":"<jwt>"}`; sem isso o servidor fecha com código 4401. Em seguida o servidor envia `{"type":"hello"}`, `{"type":"telemetry","points":[...]}` e `{"type":"alert","alert":{...}}`. Alertas operacionais chegam com `category: "operational"` e `transition` (`raise`, `escalate`, `deescalate`, `resolve`). Desde a Etapa 6, `{"type":"operation_status","operation_id","machine_id","origin","status":{...}}`, em que `status` é o mesmo corpo de `GET /operations/{id}/status`: enviado só com cliente conectado, para a operação corrente de cada máquina, no máximo a cada `AGROFLEET_STATUS_INTERVAL_S` por operação e também a cada 30 s sem dado novo (o estado da conexão muda com o tempo). Cerca de 11 KB por mensagem com 5 h de operação (série de 30 intervalos). As mensagens anteriores não mudaram; clientes que não conhecem um tipo o ignoram. O canal é somente servidor para cliente; mensagens do cliente são ignoradas.

### 5.5 Alertas e configuração

Os limiares das regras de evento são alterados por `PUT /config/alerts` (papel `admin`); a interface não tem formulário para isso. `POST /alerts/recompute?with_ml=true` recalcula incluindo o modelo experimental.

Os alertas operacionais (7.10) têm duas fontes de configuração:
- **Limites por máquina**, no cadastro (`PUT /registry/machines/{id}`): `energy_warning_pct`, `energy_critical_pct`, `energy_reserve_pct`, `autonomy_margin_pct`, `maintenance_interval_h` e `last_maintenance_engine_h`. Sem o limite, a regra correspondente não roda e aparece em `skipped_rules` com o motivo.
- **Parâmetros anti-ruído do sistema**, em `PUT /config/alerts/operational` (admin): `confirm_s` (60), `clear_s` (120), `level_hysteresis_pct` (2), `margin_hysteresis_pct` (5), `offline_after_s` (300), `online_within_s` (30), `maintenance_notice_h` (nulo = sem aviso prévio), `critical_requires_sensor_rate` (verdadeiro) e `eval_interval_s` (30).

Para registrar uma manutenção feita, atualize `last_maintenance_engine_h` no cadastro: o alerta resolve na avaliação seguinte.

### 5.6 ISOXML e API de fabricante (não implementados)

- **ISOXML/TASKDATA:** `POST /uploads` com `kind=isoxml` devolve 501 com o motivo; `read_isoxml` levanta `NotImplementedAdapter`. Para implementar: obter amostras válidas de `TASKDATA.XML` + `TLG*.XML/BIN`, avaliar um parser existente, mapear DDI para os campos canônicos, escrever um adaptador que devolva `AdapterResult` e cobri-lo com testes sobre amostras reais.
- **API de fabricante:** somente com documentação oficial e credenciais; segredos em variáveis de ambiente.
- **Contrato de um adaptador novo:** devolver `AdapterResult(points=[TelemetryPoint...], rejected=[...], warnings=[...], meta={...})`; nunca preencher campo ausente; nunca inventar timestamp; declarar unidades; registrar em `REGISTRY` com `status` e `tested` verdadeiros.

---

## 6. Uso da interface

Acesse `http://<servidor>:<HTTP_PORT>` (padrão 8080) com usuário e senha definidos no `.env` (não existe usuário padrão).

**Cabeçalho e barra de contexto.**
- Cinco seções: Operação, Frota, Planejamento, Análises e Alertas. O sino mostra quantos alertas estão ativos.
- À direita ficam o seletor de propriedade e o menu da conta, com Tema (claro, escuro ou automático, guardado no navegador), Sobre / limites e Sair.
- A barra abaixo do cabeçalho mostra:
  - a procedência dos dados, com a pílula "MODO SIMULAÇÃO" quando tudo é simulado;
  - o filtro de origem, que vale para todas as seções;
  - o estado da conexão ao vivo.
- `real` é declaração de quem enviou os dados.

| Seção | Subaba | Função |
|---|---|---|
| Operação | — | Painel da operação selecionada (a ativa mais recente da propriedade; troca pelo seletor no card da máquina). Detalhes abaixo. |
| Frota | — | Cartões por máquina (estado, velocidade, GNSS, vazão, pressão, tanque, motor e energia quando reportados, "ao vivo"); mapa com posição atual e trilha. Campo ausente aparece como "—", nunca como 0 |
| Planejamento | Operações | Cadastro (somente leitura) de operações, máquinas e talhões da propriedade; "Ver no painel" abre a operação |
| Planejamento | Upload | Tipo, origem, fuso, unidades e `machine_id`; envio sem gravar mostra o relatório de qualidade; só então gravar. ISOXML retorna o motivo da recusa |
| Análises | Reprodução | Controle deslizante sobre o histórico. Sem extrapolação: antes do primeiro ponto, depois do último ou durante perda de GNSS a máquina não é desenhada |
| Análises | Cobertura | Área bruta, única, excesso e região >= 2x; cobertura do talhão; motivos de exclusão |
| Análises | Relatórios | Agrupamento por máquina, propriedade, talhão ou operador, com período; exportação CSV |
| Análises | ROI | Premissas editáveis; custo/ha, benefício, payback, VPL, tornado, pontos de equilíbrio e grade. O exemplo é ilustrativo |
| Alertas | — | Lista com tipo, severidade, intervalo, regra e evidências; filtro por máquina quando aberta a partir do painel; `analyst` pode recalcular |
| Sobre / limites (menu da conta) | — | Escopo, limites e estado de cada adaptador; `admin` carrega ou apaga a demonstração simulada |

**Painel Operação.** Todos os valores vêm da API; o frontend só formata.

| Bloco | Origem do valor |
|---|---|
| Mapa | `GET /operations/{id}/geometry`: talhão, área trabalhada, pendente, fora do talhão, trajetória e última posição com rumo |
| Barra do talhão | Nome, área e progresso (`/status`); clima de `GET /operations/{id}/weather` (7.12). Sem dado: "Sem dados meteorológicos" |
| Máquina | Nome do cadastro, operação, estado e conexão. O tempo de operação só conta com a conexão ativa; sem conexão, mostra o tempo até o último dado |
| KPIs | Velocidade, rotação, carga do motor e combustível (% e litros pela capacidade cadastrada); em máquina elétrica, bateria (% e kWh). Sensor ausente: "—" e "não reportado" |
| Missão e previsão | `mission`: área trabalhada / planejada, progresso, ETA no fuso da propriedade com faixa; sem ritmo confiável, "Indeterminada" e o motivo |
| Autonomia | `autonomy`: autonomia estimada, tempo restante e o bloco de status (suficiente, apertada, insuficiente, indeterminado). Em vermelho aparece a nota de severidade crítica |
| Consumo e produtividade | `consumption.avg` (L/h ou kW, média ponderada pelo tempo observado) e `mission.rate_ha_h`, com as últimas séries de 10 min |
| Alertas | Alertas ativos da operação, críticos primeiro; cada linha abre a seção Alertas filtrada pela máquina |

Atualização: `/status` a cada 15 s e a mensagem WebSocket `operation_status`; geometria quando chega dado novo (no máximo a cada 20 s); clima a cada 10 min.

**Mapa-base (decisão A).**
- O alternador Mapa | Satélite usa provedores definidos por variável de ambiente na construção do frontend (`frontend/.env.example`). Os padrões são gratuitos e permitem uso comercial:
  - OpenFreeMap (mapa; estilo escuro no tema escuro);
  - EOX Sentinel-2 cloudless 2016 (satélite, CC BY 4.0, 10 m de resolução).
- Se o mapa-base falhar, o mapa mostra um fundo liso com o aviso "Mapa-base indisponível", e as camadas continuam.
- Sobre talhões simulados aparece "Local fictício: o talhão simulado não corresponde à imagem de fundo".
- O navegador do usuário contata os servidores de tiles (IP e Referer): informe isso no aviso de privacidade ou hospede os tiles.

| Variável | Padrão | Uso |
|---|---|---|
| `VITE_MAP_STYLE_URL` / `VITE_MAP_STYLE_URL_DARK` | OpenFreeMap `liberty` / `dark` | estilo vetorial do "Mapa"; vazio desliga |
| `VITE_SATELLITE_TILES`, `_ATTRIBUTION`, `_MAXZOOM`, `_TILESIZE`, `_LABEL` | EOX 2016 | camada "Satélite" (XYZ); vazio desliga |
| `VITE_BASEMAP_DEFAULT` | `map` | `map`, `satellite` ou `none` |
| `VITE_BASEMAP_TILES` / `VITE_BASEMAP_ATTRIBUTION` | — | legado: raster como "Mapa" quando `VITE_MAP_STYLE_URL` não está definido |

**Demonstração rápida (admin).**
- **Dados gravados:** no painel Operação vazio (ou em *Sobre / limites*), carregue a demonstração SIMULADA. Explore Operação, Frota, Análises e Alertas, e encerre com *Apagar dados simulados*.
- **Ao vivo:** registre as operações com `POST /demo/operations/load?scenarios=A,B,C&with_telemetry=false&start=<início>` e rode `simulator/publish.py --scenario A,B,C --start <início>`. Com um início no passado, o histórico até agora é publicado de imediato e o resto segue o relógio, então a operação aparece em andamento e conectada.

---

## 7. Pipeline e metodologia

Comandos partem da raiz do repositório, com `PYTHONPATH=.`.

### 7.1 Ingestão e validação

```python
from agrofleet.adapters import read_csv
from agrofleet.quality import assess

res = read_csv(open("payload_examples/telemetry_rejected_rows.csv", "rb").read(), origin="real")
print(len(res.points), res.n_rejected_total)          # 2 4
for r in res.rejected: print(r["row"], r["reason"])
print(assess(res)["verdict"])
```

Resultado esperado (coberto por `tests/test_payload_examples.py`): 2 válidas e 4 rejeitadas (sem fuso, (0,0), vazão negativa, `lat` sem `lon`); a linha com vazão vazia entra com `flow_lpm = None`. Com `assume_tz="UTC"`, a linha sem fuso é aceita (3 válidas).

### 7.2 Armazenamento idempotente

Via `POST /uploads` ou `insert_points(session, pontos)` (`backend/app/db.py`). A restrição `(machine_id, ts, origin)` garante que reenviar o mesmo arquivo não duplica; em PostgreSQL a coluna `geom` é gerada a partir de `lat/lon`.

### 7.3 Projeção e vazão esperada (`agrofleet/geo.py`)

`Projector(epsg)` converte (lon, lat) para (x, y) em metros (UTM do fuso do centróide); toda a geometria de cobertura roda nesse plano. `transform_geom` usa `shapely.transform`. Vazão esperada: `expected_flow_lpm(R, v, w) = R · (v·3,6) · w / 600`, com R em L/ha, v em m/s e w em m.

### 7.4 Tempo, distância e volume (`analytics/metrics.py`)

- **Tempo por estado.** Seja dt_i = t_{i+1} - t_i e `thr = max(max_gap_s, 3·mediana(dt))`. Se 0 < dt_i <= thr, o intervalo soma ao estado da amostra i; senão soma a `no_data`. Invariante testado: soma dos estados = intervalo total. Interpolação/reamostragem foi descartada porque inventa o que a máquina fez na lacuna.
- **Distância.** Soma de trechos consecutivos com posição nos dois extremos, `dt <= thr` e velocidade implícita <= `max_jump_ms` (25 m/s). Trechos descartados por salto são contados (`position_jumps_discarded`). A distância em linha reta sobre perda de posição fica em `distance_across_gaps_km_straight_line` e não é somada.
- **Volumes.** Integração da vazão (soma de Q·dt/60) e contador `consumption_l` (resets contados). `volume_source_discrepancy_pct` compara os dois.
- **Taxa realizada** (L/ha) = volume / área única. **Produtividade:** `unique_ha_per_working_h` e `unique_ha_per_engaged_h` (inclui deslocamento, parada e reabastecimento). **Custo/ha** = horas engajadas x custo horário / área (+ produto, se informado o preço por litro); custos são premissas do usuário, sem valor padrão.
- `report(df, by="machine"|"property"|"field"|"operator")` agrega; para grupos que não são máquina, a área única é a união entre máquinas. Amostras sem a chave vão para `sem_valor`.

```python
from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.metrics import machine_metrics
from agrofleet.simulator.fleet import simulate

pts, truth = simulate(seed=7, max_seconds=3 * 3600)
df = to_frame(pts)
m = machine_metrics(df[df.machine_id == "SIM-02"])
print(m["time_h"]["fault"], m["span_h"], sum(m["time_h"].values()))
```

### 7.5 Cobertura (`analytics/coverage.py`)

Para amostras consecutivas i, i+1 da mesma máquina, o segmento gera faixa somente se todas as condições valem (cada exclusão é contada em `excluded`):

1. ambos têm posição (`no_position`);
2. ambos em estado de trabalho (`not_working`);
3. `dt <= max_gap_s` (`gap`);
4. precisão reportada <= `max_accuracy_m`, se informada (`low_accuracy`);
5. há largura de trabalho (`no_width`);
6. velocidade >= `min_speed_ms` (`low_speed`);
7. se `require_flow_on`, vazão > 0 e sensor presente (`no_flow`).

Cada segmento de comprimento L e largura w vira um retângulo (segmento expandido w/2 para cada lado, extremidades retas). Para a área única, os segmentos de um mesmo **trecho contínuo** (mesma máquina, amostras seguidas, mesma largura) formam uma faixa só: buffer da polilinha com ponta reta e junção arredondada (`run_swaths`):

```
bruta     = soma de L_s · w_s
única     = área( união das faixas por trecho contínuo )   # shapely.unary_union
excesso   = max(bruta - única, 0)
região>=2x = união de (ret_s ∩ ret_t), s<t, não adjacentes no tempo
```

Por que faixa por trecho (Etapa 4): retângulos isolados deixam uma cunha aberta no lado externo de cada mudança de rumo, e a passada vizinha fecha essas cunhas em micro-buracos. No Talhão 03, 3 h geravam 9.157 anéis internos (39.807 no talhão inteiro), com subestimação da área única e mapa hachurado. Com o buffer da polilinha, a área da junção difere da área varrida em ~h²(θ/2 − tan θ/2) por vértice, desprezível a 1 Hz. Os anéis que restam no cenário A (2.494 em 3 h, 191 m² no total, ~1,4 cm de espessura) são frestas reais entre passadas planejadas exatamente a 12 m, somadas ao ruído RTK de 2 cm: a área os mantém; só o desenho (`/operations/{id}/geometry`) aplica fechamento de 5 cm. A região ≥ 2x continua por retângulo de segmento.

Pares da mesma máquina com |dt| <= 4x o dt mediano (`skip_s`) são ignorados na região >= 2x, pois retângulos consecutivos da mesma passada sempre se tocam.

Cobertura do talhão: `coberta = união ∩ talhão`, `não_coberta = talhão - união`, `fora_do_talhão = união - talhão` (pode ser deriva do GNSS, erro de limite ou aplicação indevida; o sistema não distingue).

Complexidade: `unary_union` sobre N retângulos é ~O(N log N); 4 máquinas x 3 h (11 331 amostras) rodam em segundos. Para volumes muito maiores, alternativas são rasterização em grade ou `ST_Union` no banco.

```python
from agrofleet.analytics.coverage import coverage
r = coverage(df[df.machine_id == "SIM-01"], with_geojson=False)
print(r.gross_m2 / 1e4, r.unique_m2 / 1e4, 100 * r.excess_m2 / r.gross_m2)   # 22.48 20.74 7.76
```

### 7.6 Anomalias (`analytics/anomalies.py`)

Todas as regras exigem persistência (`min_duration_s`, 20 s) e só avaliam amostras em que o sensor existe. Cada alerta de evento é `info` ou `warning` (`critical` é exclusivo dos alertas operacionais de energia e autonomia, 7.10) e carrega a nota "Alerta informativo; decisões críticas permanecem com sistemas certificados e pessoas."

| Regra (`type`) | Condição | Observações |
|---|---|---|
| `flow_deviation` | em `working`, abs(Q - Q_esp)/Q_esp > tol (padrão 15%), Q_esp = R·v·w/600 | R = alvo configurado ou taxa reportada; exige largura e taxa |
| `pressure_out_of_band` / `pressure_outlier` | pressão fora da banda configurada, ou abs(z robusto MAD) > 6 em trabalho | a pressão é global |
| `speed_out_of_range` | fora da banda, ou desvio > 35% da mediana de trabalho da máquina | |
| `unexpected_stop` | `state == working` com velocidade < 0,2 m/s | |
| `flow_while_stopped` | vazão > 0,5 L/min com velocidade < 0,2 m/s (>= 10 s) | possível aplicação concentrada |
| `fault_stop` | `state == fault` ou `error_codes` não vazio | persistência 0 s |
| `gnss_loss` | sem posição / `fix == none` por >= `gnss_min_s` (10 s) | |
| `gnss_degraded`, `gnss_accuracy_low` | `fix` diferente do esperado (`rtk_fixed`), `accuracy_m` > 0,10 m | `expected_fix=None` desliga |
| `data_gap` | intervalo > `max(30 s, 3x dt mediano)` | |
| `sensor_missing` / `sensor_flatline` | nulo durante o trabalho; leitura idêntica por >= 60 s em movimento | só vazão e pressão |
| `ml_multivariate_outlier` | IsolationForest | `experimental=True` |

**Identidade do alerta:** `id = sha1(machine|type|rule|start)[:16]`; alertas da mesma máquina/tipo/regra que se sobrepõem (+-15 s) são fundidos (`upsert_alert`). Incluir `rule` na identidade corrigiu um bug em que `sensor_missing` de vazão e de pressão tinham o mesmo id.

**Por que não há diagnóstico de bico entupido.** Entupimento reduz a vazão local de um bico; um fluxômetro e um manômetro globais veem a soma, e a pressão pode subir, cair ou não mudar conforme o controle. Uma queda de vazão indica problema de aplicação, não qual componente. Distinguir bico exige sensor por seção/bico.

**IsolationForest (experimental).** Vetor de taxa realizada (Q·600/(v·3,6·w)), pressão e velocidade do trabalho de cada máquina, padronizado por máquina, `IsolationForest(n_estimators=200, contamination=c, random_state=seed)`. Pontos marcados são agrupados em eventos sustentados (`ml_multivariate_outlier`, `experimental=True`, `info`). `contamination` é um orçamento de alarmes: uma máquina sem falhas também terá ~100·c% de amostras "mais raras". Requisitos: vazão, pressão, velocidade e largura presentes e >= 300 amostras de trabalho; caso contrário é pulado com o motivo. Não roda no streaming; roda sob demanda (`POST /alerts/recompute?with_ml=true`). Alternativas não implementadas: resíduo de modelo físico, CUSUM/Page-Hinkley, autoencoders temporais (todas exigem dados reais rotulados).

**Avaliação.** `evaluate` considera um evento `abnormal_flow` detectado se `flow_deviation`, `pressure_outlier` ou `ml_multivariate_outlier` o cobrir (mapa `ACCEPT`). Com tolerância de 45% a regra de vazão não dispara no SIM-04, mas o evento ainda conta como detectado por `pressure_outlier`. Como no simulador a pressão é função da vazão, o recall de 4/4 não prova que a regra de vazão enxerga a falha; para avaliar uma regra isolada, filtre os alertas por tipo antes de chamar `evaluate`.

```python
from agrofleet.analytics import anomalies as A
res = A.detect(df, A.AnomalyConfig(use_iforest=False))
ev = A.evaluate(res.alerts, [e for e in truth.events])
print(ev["recall"], [(a.machine_id, a.type) for a in res.alerts][:4])
```

### 7.7 ROI (`analytics/roi.py`)

Cada entrada (`RoiInputs`) tem proveniência (`assumed`, `user`, `measured_telemetry` + origem). `EXAMPLE_INPUTS` é ilustrativo (`is_example=True` em toda saída).

```
fixo_máquina/ano  = (capex - residual)/vida + juros·(capex + residual)/2 + manutenção%·capex
horas/ano         = (área_ano · aplicações) / produtividade_autônoma
custo_autônomo/ha = [n·fixo_máquina + (energia + supervisão)·horas] / área_tratada + produto/ha·(1 + sobrep_auto)
custo_base/ha     = custo_horário_base / produtividade_base + produto/ha·(1 + sobrep_base)
benefício/ano     = (custo_base - custo_autônomo)/ha · área_tratada
payback (anos)    = capex_total / benefício/ano          (indefinido se benefício <= 0)
VPL               = -capex_total + soma_{t=1..vida} (benefício + residual·n em t=vida)/(1+r)^t
```

Sensibilidade: tornado (cada premissa +-20%, uma por vez, `sensitivity`), grade 2-D (`grid`) e ponto de equilíbrio por bissecção (`break_even`; `None` se não houver troca de sinal no intervalo). Não inclui impostos, financiamento, custo de falha/parada, ganho agronômico nem risco regulatório; o resultado depende integralmente das premissas.

```python
from agrofleet.analytics import roi as R
I0 = R.EXAMPLE_INPUTS
out = R.compute(I0)
print(out["net_benefit_per_year"], out["payback_years"])     # -105600 None
print(R.break_even(I0, "area_ha_per_year", 1000, 20000))     # 6974.17
```

### 7.8 Autenticação (`backend/app/auth.py`)

JWT HS256 (`sub`, `role`, `iat`, `exp`), três papéis ordenados, senhas com scrypt (`hashlib.scrypt`, n=2^14, r=8, p=1, salt aleatório de 16 bytes, comparação em tempo constante).

### 7.9 Missão e autonomia (`analytics/mission.py`, `analytics/autonomy.py`)

Cálculos puros (DataFrame + cadastro → resultado), expostos por `GET /operations/{id}/status`. São **extrapolações do ritmo e do consumo observados nesta operação**, não previsões validadas; a resposta traz esse aviso em `basis`.

**Missão.** Entram só amostras da máquina da operação, da mesma origem, entre `start` e `end` (ou `as_of`).

| Grandeza | Definição |
|---|---|
| área planejada | `planned_area_ha` da operação; sem ela, área do talhão. `planned_source` diz qual |
| área trabalhada | área(união das faixas ∩ talhão). Repasse não conta duas vezes, faixa fora do talhão não conta. Sem geometria: área única total, com aviso `no_field_geometry` |
| série | intervalos de 10 min desde o início. Área **nova** = parte da união do intervalo que ainda não estava coberta (Σ novas = trabalhada) |
| ha/h efetivo | área nova / tempo observado (relógio, com manobras e paradas; exclui só o tempo sem dado) |
| ritmo atual | Σ área nova / Σ tempo observado nos intervalos dos últimos 60 min |
| faixa do ritmo | quartis 25% e 75% do ha/h dos intervalos completos (faixa empírica, não intervalo de confiança) |
| ETA | última amostra + restante / ritmo. Faixa: restante / max(Q3, ritmo) e restante / min(Q1, ritmo) |

Sem ETA (`eta_reasons`): área planejada desconhecida, menos de 30 min, progresso abaixo de 2%, nenhuma área nova na janela, dado com mais de 5 min. Avisos que não impedem o cálculo (`reliability`): menos de 3 intervalos completos, coeficiente de variação > 0,5, mais de 10% da janela sem dado, área acima da planejada, área coberta sem a operação ter sido encerrada.

**Autonomia.** U = L (diesel) ou kWh (elétrico).

| Grandeza | Definição |
|---|---|
| nível | mediana das leituras válidas dos últimos 5 min (filtra quantização). Diesel: % × capacidade cadastrada. Elétrico: `battery_energy_kwh`, ou SOC × capacidade (`level_source` diz qual) |
| utilizável | max(nível − reserva, 0); reserva = `energy_reserve_pct` × capacidade |
| taxa | média ponderada pelo tempo de `fuel_rate_lph` ou `battery_power_kw` nos últimos 60 min (mínimo 15 min com leitura). Sem sensor: inclinação do nível (`level_slope`), sinalizada. Com os dois, diferença > 25% gera `rate_sources_disagree`. Abastecimento ou recarga na janela: usa só o trecho posterior |
| autonomia | utilizável / taxa |
| necessidade | taxa × tempo restante (central e pessimista) |
| margem | (utilizável − necessidade) / necessidade |

| Estado | Regra |
|---|---|
| verde, Viável | margem central ≥ `autonomy_margin_pct` **e** margem no pior caso ≥ 0 |
| amarelo, Margem apertada | margem central ≥ 0, mas abaixo da exigida, ou pior caso negativo ou indeterminado |
| vermelho, Insuficiente | margem central < 0: na taxa atual, a energia utilizável acaba antes de concluir |
| cinza, Indeterminado | falta capacidade, reserva, margem exigida, leitura de nível, taxa positiva ou tempo restante, ou o dado está velho; `reasons` lista o motivo. Nada é preenchido com valor presumido |

Sensor com `sensor_quality = failed` não entra (não vira zero). O nível tem uma classe própria (`level_class`: normal, baixo ou crítico pelos limites do cadastro), independente da viabilidade: uma máquina pode estar com nível baixo e ainda assim ter energia para concluir. Histerese e alertas são da Etapa 5.

**Instante de avaliação e conexão.** Sem `as_of`, avalia agora: idade = max(agora − último `ingested_at`, agora − último `ts`), assim carga histórica recém-importada não parece ao vivo e relógio adiantado não esconde enlace parado. Conexão: Conectado (≤ 30 s ou MQTT ativo), Atrasado (≤ 5 min), Sem conexão. Com `as_of`, avalia como naquele instante (reprodução, auditoria). `mode_label` = "MODO SIMULAÇÃO" para origem simulada.

**Validação contra a verdade-terreno** (`tests/test_mission_autonomy.py`, 39 testes):

| Caso | Resultado |
|---|---|
| A em 3 h: ETA | 3 + 9,19 h = 12,19 h; real 12,31 h (−1%; a execução para em 99,6% do talhão porque não há cabeceira trabalhada) |
| A em 3 h: necessidade de diesel | 304 L previstos contra 316 L consumidos (−4%); taxa do sensor e queda do nível concordam a 0,5%; status verde (margem de 36% contra 15%) |
| B em 1 h e 3 h | vermelho (3 h: autonomia de 0,84 h para 2,96 h restantes, margem −72%); a verdade confirma o retorno à base sem concluir. Nível "Baixo" (19,5% contra 30%), informado à parte |
| C (falhas de enlace) | 90 s perdidos aparecem como `no_data_h` (90 ± 3 s); a área perdida não é interpolada |
| Casos à mão | 600 L, 50%, reserva de 10%, 30 L/h, restante 5 h → utilizável 240 L, autonomia 8 h, necessidade 150 L, margem 60% → verde; variações para amarelo, vermelho e cada motivo de cinza |
| Geometria | sobreposição analítica (3 faixas de 2 m × 495 m) não infla o progresso; repasse não conta duas vezes; faixa fora do talhão não conta |

Desempenho sem cache: 1,2 s por chamada de status para 3 h do cenário A; 7,6 s para o talhão completo (12 h). Com o cálculo incremental da Etapa 6 (7.11), cerca de 0,15 s com o cache quente.

### 7.10 Alertas operacionais (`analytics/op_alerts.py`, `backend/app/op_alerts_service.py`)

Diferentemente das regras de evento (7.6), cada família tem um **ciclo de vida** por máquina e origem: pendente, ativo (com severidade que sobe ou desce no mesmo alerta) e resolvido. Há no máximo um alerta ativo por família, e o histórico de severidades fica em `history`.

| Família (`type`) | Severidade | Condição | Saída |
|---|---|---|---|
| `energy_level` | warning / critical | nível (mediana de 5 min, 7.9) <= `energy_warning_pct` / `energy_critical_pct` do cadastro | nível > limite da severidade atual + `level_hysteresis_pct`, por `clear_s` |
| `autonomy_risk` | warning / critical | viabilidade amarela / vermelha (7.9) | crítico: margem >= `margin_hysteresis_pct`; aviso: margem >= exigida + histerese e pior caso >= 0; por `clear_s`. Também resolve quando a área planejada é coberta |
| `maintenance_due` | warning (info opcional) | horímetro (última leitura válida) >= `last_maintenance_engine_h` + `maintenance_interval_h`; aviso prévio só com `maintenance_notice_h` | cadastro atualizado |
| `connection_lost` | warning | operação ativa e idade do dado > `offline_after_s` (mesma idade do `/status`: max(enlace, dado)) | idade <= `online_within_s` |

Nível baixo e risco de não conclusão são **alertas distintos**: uma máquina com 20% de combustível que termina com folga tem `energy_level` sem `autonomy_risk`, e uma com 60% que não termina tem o inverso.

**Anti-ruído**
- **Confirmação.** A condição precisa se manter por `confirm_s` antes de abrir ou subir de severidade, e cada severidade tem a própria contagem: um `critical` nunca nasce de um único instante, nem herda o tempo de um aviso anterior. Um retorno à normalidade no meio da contagem a reinicia.
- **Histerese.** Para descer ou resolver, o valor sai da faixa com folga e fica fora por `clear_s`. Na faixa intermediária o alerta continua ativo e marcado com `in_hysteresis_band`.
- **Congelamento.** Leitura ausente, com falha ou velha, e autonomia cinza não abrem nem resolvem nada: o estado fica como está, com `indeterminate` na evidência. Dado parado não significa problema resolvido.
- **Portões do `critical` de autonomia.** Taxa medida por sensor (não pela queda do nível), sensor e nível concordantes e janela sem lacunas. Se algum falha, o alerta fica em `warning` e a evidência diz por quê (`critical_gates_failed`).

**Execução**
- Ao vivo: avaliação completa da **operação corrente** de cada máquina do lote (a ativa de início mais recente; outra operação ativa da mesma máquina não é avaliada), no máximo a cada `eval_interval_s`; vigia de conexão a cada 30 s.
- Troca de operação: o alerta ativo de uma operação anterior é encerrado ("deixou de ser a operação corrente"), nunca herdado nem atualizado com dado da nova; a nova operação começa as contagens do zero.
- Estado (pendências e alerta ativo por família) persistido em `settings`: sobrevive a reinícios.
- `POST /operations/{id}/alerts/replay`: reconstrói com estado novo a partir do histórico, sem partir do estado ao vivo. Padrão: a janela de confirmação no fim dos dados. `window_s`/`step_s` permitem auditar a operação inteira (uma avaliação por passo, incremental desde a Etapa 6).
  - **Reconstrução** (sem `end`, ou `end` no último dado): substitui os alertas operacionais desta operação. Se ela é a operação corrente da máquina, o estado reconstruído vira o estado ao vivo, e alertas ativos de outras operações nesse estado são encerrados. Se não é, o estado ao vivo só perde as referências a esta operação, e o que ficou ativo no fim da reconstrução é encerrado.
  - **Auditoria** (`end` anterior ao último dado): devolve as transições e não grava nada (`persisted=false`); estado ao vivo e alertas gravados ficam intactos.
- `POST /demo/operations/load` faz esse replay no fim de cada operação; `POST /alerts/recompute` também, salvo `operational=false`. O recálculo de eventos não apaga alertas operacionais.
- Em `/operations/{id}/status`, `alerts` lista os ativos da máquina por prioridade (crítico, aviso, informativo; recentes antes). É o estado atual, não reavaliado em `as_of`.

Todo alerta crítico traz a nota: "A severidade crítica representa risco operacional estimado, não uma decisão automática de segurança. O sistema permanece exclusivamente de monitoramento, sem comandos para máquinas."

**Validação** (`tests/test_op_alerts.py`, 34 testes)

| Caso | Resultado |
|---|---|
| Sequências à mão | confirmação, pico isolado, histerese na saída, escalada com confirmação própria (mesma id), crítico direto, desescalada, congelamento por dado velho, portões, regras puladas sem cadastro |
| Cenário B (replay a cada 5 min) | `autonomy_risk` crítico às 10:35: a ETA só existe após 30 min de operação, e a confirmação leva mais uma avaliação. São cerca de 3 h de antecedência sobre o retorno à base da verdade-terreno (13:33). `energy_level` em aviso às 12:10 e crítico às 13:25, como alerta separado. Sem regra de manutenção cadastrada (elétrico): pulada com o motivo |
| Cenário A | 3 h sem nenhum alerta (verde, nível normal). Com a regra cadastrada, `maintenance_due` abre quando o horímetro passa de 4.832 h (fim da operação) |
| API e ao vivo | demo abre B com crítico + aviso e A/C sem alertas; prioridade; compatibilidade dos alertas antigos; replay idempotente; vigia sem duplicar; caminho ao vivo respeita o intervalo; `DELETE /data` limpa o estado |
| Isolamento do replay (revisão do PR #2) | troca de operação encerra o alerta da anterior; auditoria de janela passada não grava; reconstruir outra operação da mesma máquina preserva o estado e os alertas da corrente, e a avaliação seguinte não duplica; operação corrente = ativa mais recente |

### 7.11 Cálculo incremental e cache (`analytics/incremental.py`, `backend/app/ops_cache.py`)

Sem cache, cada chamada de status refaz a cobertura da operação inteira: 2,3 s com 5 h do cenário A e 6,5 s com 12 h. Medido com 12 h, 43% desse tempo vai na região ≥2× (interseções entre faixas), cerca de 45% na união acumulada da série por intervalo e 5% na união principal. Tudo isso só **acrescenta** no tempo: uma faixa, um par de faixas sobrepostas ou um intervalo de 10 min que dependem só de amostras anteriores a um corte não mudam com dado posterior.

**O que congela.** Corte = início + K intervalos, com K = ⌊(última amostra − 120 s − início) / 10 min⌋. A margem de 120 s absorve o atraso e o fora de ordem comuns do enlace.
- Intervalos 0..K−1 da série: as entradas e a união acumulada (∩ talhão).
- Trechos contínuos cuja quebra já está decidida antes do corte (a amostra que decide se o trecho continua é anterior ao corte), unidos em `U`, com a área de `U ∩ talhão` acumulada.
- Faixas por segmento com as duas pontas antes do corte, com a árvore espacial e a união das sobreposições entre elas.

**A cada chamada** só a cauda é calculada: trechos não congelados, faixas novas contra a árvore congelada e entre si, e intervalos a partir de K. As áreas somam partes que não se sobrepõem:
- única = |U| + |T − U|;
- em talhão = |U ∩ F| + |(T − U) ∩ F|;
- região ≥2× = |ov| + |N − ov|, em que N são as peças novas.

A união para o mapa (`/geometry`) só é montada quando pedida.

**Verificação, não confiança.** A cada chamada, o cache confere o hash das colunas que entram no cálculo (`ts`, posição, estado, precisão, largura, velocidade, vazão, combustível, potência e qualidades) nas amostras até a primeira depois do corte. Se algo mudou (chegada tardia, reentrega diferente), descarta e recalcula do zero. Também recomeça se mudarem o cadastro da operação, o talhão, a configuração, a zona UTM, o limite de lacuna ou a vizinhança de trajetória (estes dois dependem do dt mediano). O motivo fica em `stats.last_reset`. Com amostragem irregular, o dt mediano pode oscilar e o cache recomeçar mais vezes: o resultado continua correto e só a chamada fica mais lenta.

**Igualdade com o cálculo completo** (`tests/test_incremental.py`, `tests/test_streaming.py`, `pytest -m infra`): campo a campo, com tolerância de 1e-6 relativo. Unir as mesmas peças em outra ordem já muda a área no GEOS: na região ≥2× de 5 h do cenário A (333 m², 17.744 peças), o próprio cálculo completo varia 1e-5 m² conforme a ordem, e o incremental fica nessa mesma ordem. Casos testados:
- 13 instantes ao longo do cenário A;
- cenário C montado pela ordem de chegada (perda, 4 min guardados e reenviados, latência de 25 s fora de ordem, reentregas), sem descartar o cache;
- chegada tardia antes do corte (recalcula);
- cadastro alterado e `as_of` anterior ao corte (recalcula);
- operação com 4 meses de lacuna (17 mil intervalos, custo linear);
- teste de carga de 5 h (8.7.1).

**Backend** (`ops_cache.py`, por processo, LRU de `AGROFLEET_OPS_CACHE_SIZE` operações):
- **quadro**: lê do banco só as linhas com `ingested_at` desde a última leitura (folga de 5 s) e confere pela contagem de linhas da operação. Se não bater, relê tudo. As linhas de telemetria nunca mudam depois de gravadas (`ON CONFLICT DO NOTHING`).
- **missão**: um cache para o presente e outro para `as_of`, para que uma reprodução ou auditoria não descarte o cache ao vivo. O de `as_of` é liberado ao fim de cada replay de alertas.
- **geometria**: a resposta de `/geometry` fica guardada enquanto o dado e os parâmetros forem os mesmos.
- Contadores em `/health` (`ops_cache`).

Custo: a primeira chamada, ou a primeira depois de um descarte, custa o mesmo que o cálculo completo. A memória cresce com a operação, porque guarda as faixas por segmento (uma por segundo de trabalho) e as uniões. Medido pelo RSS marginal de cada operação adicional em cache: cerca de 40 MB com 5 h e 65 MB com 12 h do cenário A. Com o padrão de 8 operações, até cerca de 0,5 GB.

### 7.12 Clima (`agrofleet/weather/`, `backend/app/weather_api.py`)

O clima vem de provedores externos atrás de uma interface mínima (`Provider`: `observation`, `forecast`, `gate`, `attribution`). A ordem é `AGROFLEET_WEATHER_PROVIDERS` (padrão `inmet`): para a observação vale o primeiro provedor da lista que devolver dado, e o mesmo para a previsão. Nada é buscado na subida da API nem nos testes existentes; a busca só acontece quando um endpoint de clima é chamado.

**Três tipos de valor, sempre identificados.**

| `kind` | Bloco | O que é | Provedor |
|---|---|---|---|
| `observation` | `observation` | leitura horária REAL de uma estação automática, com código, nome, UF, coordenadas e distância até o ponto | INMET |
| `model` | `observation` | estimativa de modelo numérico para o ponto (grade de alguns km), não é sensor; rótulo "Open-Meteo (modelo)" | Open-Meteo (só desenvolvimento) |
| `forecast` | `forecast` | previsão do período atual do dia local (manhã, tarde, noite ou dia) | INMET (por município) ou MET Norway (modelo, por ponto) |

**Provedores.**
- **INMET** (padrão). Três rotas, nenhuma com documentação oficial publicada (verificadas em 2026-11-20; podem mudar sem aviso):
  - observação sem token: `apiprevmet3.inmet.gov.br/estacao/proxima/{ibge}`, a estação automática mais próxima do município e a última leitura horária;
  - observação com token (`AGROFLEET_INMET_TOKEN`): `apitempo.inmet.gov.br/token/estacao/{d0}/{d1}/{estação}/{token}`, com a estação automática operante mais próxima do PONTO no catálogo público `/estacoes/T`, até `AGROFLEET_INMET_MAX_STATION_KM`. Não precisa do código IBGE. Token inválido ou cancelado volta como HTTP 200 com o texto `CHAVE INVÁLIDA!`, tratado como `no_token`, e a busca cai na rota pública;
  - previsão: `apiprevmet3.inmet.gov.br/previsao/{ibge}`. Cinco dias; hoje e amanhã têm manhã, tarde e noite, os demais um bloco único. A resposta tem cerca de 270 KB, quase tudo ícones em base64; os ícones são descartados na leitura (lista branca de campos) e nunca ficam em cache.
- **MET Norway** (opcional, desligado por padrão): `api.met.no/weatherapi/locationforecast/2.0/compact`. Previsão de modelo para qualquer ponto. Respeita os termos: User-Agent identificador, coordenadas com 2 casas, nenhuma repetição antes do `Expires` (validade calculada como `Expires − Date` sobre o nosso relógio) e revalidação com `If-Modified-Since`. O resumo em português traduz o `symbol_code` do próprio provedor.
- **Open-Meteo** (só desenvolvimento): condições "atuais", que são saída de modelo a cada 15 min. Só roda com `AGROFLEET_ENV=dev` e `AGROFLEET_WEATHER_ALLOW_NONCOMMERCIAL=1`, porque a API gratuita é exclusivamente não comercial; a resposta sai com `non_commercial: true`. Uma implantação que contrate o plano pago define `OPENMETEO_BASE_URL=https://customer-api.open-meteo.com` e `OPENMETEO_API_KEY`; com a chave, o provedor vale em qualquer ambiente e `non_commercial` fica `false`. Fora disso, aparece só como motivo `not_allowed`.

**O que cada valor significa (INMET).**
- `observed_at`: `DT_MEDICAO` + `HR_MEDICAO`, ambos em UTC (13:00 UTC = 10:00 em Brasília). A leitura de uma hora costuma ser publicada em até ~50 min.
- `temperature_c` (`TEM_INS`), `humidity_pct` (`UMD_INS`), `wind_dir_deg` (`VEN_DIR`, de onde sopra), `precipitation_mm` (`CHUVA`, total da hora), `pressure_hpa` (`PRE_INS`, no nível da estação, não do mar), `dew_point_c` (`PTO_INS`), `temp_max_c`/`temp_min_c` (extremos da hora).
- `wind_speed_kmh` e `wind_gust_kmh`: `VEN_VEL` e `VEN_RAJ` chegam em m/s e são convertidos (× 3,6).
- `radiation_kj_m2` (`RAD_GLO`, total da hora) e `radiation_w_m2` (o mesmo ÷ 3,6, média da hora).
- `distance_km`: calculada por nós (haversine) do ponto consultado até a estação (`distance_basis: "point"`). Sem coordenadas do ponto, usa a distância que o INMET informa a partir do município (`"inmet_municipality"`). Estação mais longe que `AGROFLEET_INMET_MAX_STATION_KM` não é usada (`no_station_in_range`).
- Dado de estação automática é **bruto e não validado** pelo INMET (o próprio instituto avisa); a observação traz essa nota em `note`.
- Ausência: `9999`, `-9999`, `"Null"`, `null`, `""` e `"*"` viram `null`, nunca 0. Nebulosidade e "condição do céu" só existem no `forecast.summary`; a observação automática não mede céu e o sistema não deriva.
- Previsão INMET por município é textual ou por faixa: `temp_min_c`/`temp_max_c`, `humidity_min_pct`/`humidity_max_pct`, `wind_dir` (ex.: "N-NW") e `wind_intensity` (ex.: "Fracos"). Os campos pontuais `temperature_c`, `humidity_pct` e `wind_speed_kmh` ficam `null` (só o MET Norway os preenche). `days` lista os dias restantes.

**Período da previsão.** Data e hora locais no fuso da propriedade: antes das 12 h, manhã (de madrugada, a manhã que vem); de 12 h a 17 h, tarde; a partir das 18 h, noite. Do terceiro dia em diante o INMET não divide o dia (`period: "dia"`). Se a previsão em cache não cobrir a data local de hoje, o bloco fica vazio (`no_data`).

**Ponto e município da operação.** Ponto: centroide da geometria do talhão (mesma origem da operação); senão, a última posição de telemetria da máquina dentro da janela da operação (`location.source`: `field_centroid` ou `last_position`). Município: `ibge_municipality` da propriedade da operação (ou da máquina), senão `AGROFLEET_WEATHER_IBGE`; sem nenhum dos dois, as rotas INMET por município devolvem `no_municipality`. A propriedade simulada `PROP-SIM-SC` usa `5206206` (Cristalina-GO): a origem fictícia do simulador (-16,70; -47,60) cai nesse município pela malha municipal do IBGE. Em operação simulada, `location.fictitious: true` e a nota "Talhão simulado em local fictício: o clima mostrado é o real deste ponto, não do talhão."

**Idade e `stale`.** A idade vem do horário da medição contra o relógio do servidor AgroFleet, nunca do cabeçalho `Date` do provedor (o do INMET já foi visto ~18 min atrasado). Acima de `AGROFLEET_WEATHER_MAX_AGE_S` (2 h), a observação continua na resposta com `stale: true` e um motivo `stale`. A idade é recalculada a cada resposta, mesmo com o dado em cache.

**Cache e frequência.** Cache em memória por provedor e recurso: observação por município ou estação (30 min), previsão por município ou ponto arredondado (3 h), catálogo de estações (24 h). Falhas ficam em cache por 120 s, para não insistir num provedor fora do ar. Uma busca por chave de cada vez; as requisições simultâneas esperam o mesmo resultado. Observação e previsão são buscadas em paralelo. A frequência é baixa de propósito: em 2026 o INMET passou a se opor à cópia automatizada em massa da sua base, e este cliente faz no máximo uma busca por município ou estação a cada TTL, sem varredura. Contadores em `/health` (`weather.cache`), sem nenhuma busca.

**Resposta e falhas.** `status`: `ok` com observação e previsão, `partial` com só uma, `unavailable` sem nenhuma (com `message: "Sem dados meteorológicos"`). `reasons[]` traz `{provider, code, message}` com os códigos `no_token`, `no_station_in_range`, `http_error`, `timeout`, `stale`, `no_data`, `disabled`, `no_municipality` e `not_allowed`. Falha do provedor nunca vira erro HTTP. `attribution` junta os créditos dos provedores usados ("Fonte: INMET", "Data from MET Norway", "Weather data by Open-Meteo.com") e `attributions[]` traz texto, link e licença de cada um, para exibir junto do dado. `cached` é `true` quando nada foi buscado nesta resposta; `fetched_at` é a hora da busca mais antiga usada.

**HTTP.** Só `urllib` da biblioteca padrão (nenhuma dependência nova), tempo limite `AGROFLEET_WEATHER_TIMEOUT_S`, User-Agent descritivo e genérico, sem dado pessoal. O INMET fecha a conexão sem resposta para o User-Agent padrão do curl. A função de busca é injetável; os testes (`tests/test_weather.py`) nunca acessam a rede.

---

## 8. Validação, métricas e testes

Todos os números abaixo vêm de saídas de notebooks, testes ou de `experiments/geodesy_check.json` e valem **apenas para dados simulados** (semente 7).

### 8.1 Camadas de validação

| Camada | Garante | Onde |
|---|---|---|
| Unitários e propriedades | soma de tempo fecha, bruta >= única, nulo continua nulo, comando rejeitado | `tests/test_geometry.py`, `test_schema_adapters.py`, `test_analytics.py` |
| Exemplos de payload | cada exemplo se comporta como documentado | `tests/test_payload_examples.py` |
| API (SQLite) | autenticação/papéis, upload com qualidade, idempotência, relatórios, WebSocket | `tests/test_api.py` |
| Infra real | PostGIS (geografia, `ST_Within`, `ON CONFLICT`) e Mosquitto (auth, ACL, ingestão) | `tests/test_infra.py` (`-m infra`) |
| E2E | Playwright/Chromium contra a pilha no ar | `tests/e2e/test_ui.py` (`-m e2e`) |
| Frontend | funções puras (reprodução sem extrapolar, trilha) e tipos | `frontend/src/lib.test.ts` (vitest), `tsc --noEmit` |
| Verdade do simulador | erro contra áreas analíticas e eventos injetados | notebooks 02 e 03 |
| Cruzamento geodésico | UTM x `pyproj.Geod` x PostGIS `geography` | `scripts/validate_geodesy.py` |

### 8.2 Geometria contra a verdade analítica

Caso mínimo (2 passadas, 495 m, barra 24 m, espaçamento 22 m): esperado bruta 23 760 m², sobreposição 990 m², única 22 770 m²; obtido 23 760 / 980 / 22 780.

Frota simulada, medida com `coverage()` sobre `simulate()` depois da faixa por trecho contínuo (Etapa 4). As saídas do notebook 02 foram regeneradas na Etapa 9 e coincidem com esta tabela:

| Máquina | Bruta est. (ha) | Bruta real | Única est. | Única real | Sobrep. est. | Sobrep. real | Erro bruta | Erro única |
|---|---|---|---|---|---|---|---|---|
| SIM-01 | 22,48 | 22,8 | 20,74 | 21,00 | 7,76% | 7,89% | -1,39% | -1,25% |
| SIM-02 | 21,29 | 21,6 | 20,47 | 20,75 | 3,88% | 3,94% | -1,41% | -1,36% |
| SIM-03 | 17,36 | 20,4 | 17,34 | 20,40 | 0,07% | 0,00% | -14,92% | -14,98% |
| SIM-04 | 22,48 | 22,8 | 20,73 | 21,00 | 7,79% | 7,89% | -1,39% | -1,28% |

- Sem eventos de posição, o erro é -1,25% a -1,4% (viés de subestimação). Antes da Etapa 4 a área única ficava em -1,6% a -1,8%: a diferença eram os micro-buracos entre retângulos (7.5). O viés da área bruta não mudou; a hipótese, ainda não testada, é que a discretização a 1 Hz perca as pontas das passadas (o último passo de cada passada não gera segmento).
- SIM-03 tem -15%: a perda de GNSS de 4 min (241 amostras sem posição) remove cobertura que o método não interpola.
- A sobreposição do SIM-03 (real 0%) caiu de 1,08% para 0,07%: o falso excesso do jitter de RTK-float (0,35 m) vinha quase todo de retângulos consecutivos que se cruzavam.
- Cenário A completo (Talhão 03, 85 passadas): trabalhado no talhão 119,38 ha contra 119,58 ha planejados (-0,16%); antes, 118,14 ha (-1,2%).

### 8.3 Sensibilidades medidas

| Perturbação | Efeito |
|---|---|
| Largura efetiva x0,95 / x1,05 | sobreposição 3,67% / 12,15% (de 8,12%); única muda < 1%; bruta +-5% |
| Largura x0,90 | sobreposição 0,43% (barra menor que o espaçamento) |
| Ruído de posição sigma = 0,1 / 0,5 / 1 / 2 / 3 m | sobreposição 9,49 / 16,01 / 22,44 / 31,12 / 40,26% (verdade ~8%) |
| Filtro de precisão <= 0,10 m | descarta 114 segmentos; única do SIM-03 cai de 17,17 para 15,91 ha |

### 8.4 Tempo e volume

- Para as quatro máquinas, soma dos estados = intervalo total (`soma_h` = `span_h`), com `sem_dado_h = 0`.
- Falha do SIM-02: 0,117 h (~7 min), igual ao evento injetado (10:10:30-10:17:30); soma = 0,899 h = intervalo.
- Divergência vazão x contador: -0,05% a -0,06% sem lacunas de sensor; -8,64% no SIM-03 (dados faltantes).
- Taxa realizada: 105-111 L/ha.
- Produtividade do SIM-01: 39,4 ha/h (trabalho) e 28,3 ha/h (engajado).

### 8.5 Detecção de anomalias

- Eventos injetados detectados: 4/4 (`stop`, `gnss_loss`, `missing_data`, `abnormal_flow`), atraso de início 0,0 s. Ressalvas: regras escritas conhecendo o simulador (recall otimista); o mapa `ACCEPT` aceita `pressure_outlier` como detecção de `abnormal_flow`; a emissão do alerta só ocorre após `min_duration_s` (>= 20 s).
- Alertas sem evento correspondente: 2 (`gnss_accuracy_low` e `gnss_degraded` do SIM-03, na reconvergência RTK-float).
- A falha de vazão do SIM-04 gerou 3 alertas `flow_deviation` (a persistência é interrompida entre passadas).
- Varredura de tolerância (sensor de vazão com sigma = 8%), por hora de trabalho:

| Tolerância | Persistência | Alertas | Recall por amostra | Falsos/h |
|---|---|---|---|---|
| 5% | 10 s / 20 s | 182 / 99 | 1,00 | 2 214 / 1 597 |
| 10% | 10 s / 20 s | 23 / 3 | 1,00 | 133 / 5 |
| 15-25% | 10 s / 20 s | 3 / 3 | 1,00 | 0 |
| 35% | 10 s / 20 s | 8 / 2 | 0,52 / 0,16 | 0 |
| 45% | n/d | 0 | 0,00 | 0 |

- IsolationForest: `contamination` 0,01/0,02 gera 0 alertas; 0,05 gera 2; 0,10 gera 14; 0,15 gera 30 (a regra física gera 3, todos no SIM-04). Uma máquina saudável também gera alertas (SIM-01: 2 com c=0,10; 9 com c=0,15).

### 8.6 Validação geodésica (PostGIS real)

De `experiments/geodesy_check.json` (PostgreSQL 16 + PostGIS 3.4, contêiner amd64 emulado):

| Máquina | PostGIS `geography` (m) | `pyproj.Geod` (m) | Métrica UTM do projeto (m)* |
|---|---|---|---|
| SIM-01 | 12 089,70 | 12 089,70 | 12 096,34 |
| SIM-02 | 13 784,19 | 13 784,19 | 13 791,73 |
| SIM-03 | 10 852,50 | 10 852,50 | 10 682,79 |
| SIM-04 | 15 792,47 | 15 792,47 | 15 801,11 |

\* A métrica do projeto exclui saltos e lacunas por definição; no SIM-03 é menor porque os 241 s sem posição não entram. Nas outras máquinas a diferença para a geodésica é ~0,05%.

- PostGIS x `pyproj.Geod`: diferença relativa ~1e-12.
- Área do talhão: UTM 200 000,0 m² contra `geography` 199 780,4 m² (+0,11% de viés de projeção neste local).
- `ST_Within` conta 1 990 / 2 293 / 1 615 / 1 984 pontos dentro do talhão (SIM-01 a SIM-04).

### 8.7 Ingestão sob rajada

Teste ponta a ponta com 3 600 mensagens QoS 1 publicadas de uma vez: com o padrão do Mosquitto (`max_queued_messages` 1000), o broker descartou mensagens e 3 465 chegaram ao banco; com `max_queued_messages 100000` e `max_inflight_messages 100`, chegaram 3 600 sem descartes. Foi uma mudança de configuração observada em um ambiente (Podman emulado); não é prova de capacidade. O simulador publica com ritmo (`--speedup`).

### 8.7.1 Carga de uma operação (Etapa 6)

`scripts/bench_operations.py`: cenário A (talhão de 120 ha, trator diesel, 1 Hz), 5 h de dado (18.001 amostras) ingeridas por `Live.process` em lotes de 30 s de dado. Em cada lote: gravação, regras de evento, avaliação dos alertas operacionais (`eval_interval_s = 0`) e montagem de `operation_status` para um cliente conectado. É a cadência do tempo real (uma avaliação a cada 30 s). Resultados em `experiments/bench_operations*.json`.

| Medida | SQLite | PostGIS 16 (emulado) |
|---|---|---|
| `process` por lote de 30 s (p50 / p95 / máx.) | 0,13 / 0,26 / 0,88 s | 0,22 / 0,39 / 1,06 s |
| `GET /status` com cache quente, ao longo da operação (p50 / p95) | 0,13 / 0,21 s | 0,19 / 0,27 s |
| Status sem cache com 5 h de dado (cálculo completo) | 2,33 s | 2,70 s |
| Status no fim: frio (cache vazio) / quente | 2,31 / 0,16 s | 2,66 / 0,22 s |
| `GET /geometry` no fim: frio / quente | 0,26 / 0,02 s | 0,39 / 0,08 s |
| Mensagem `operation_status` (p50 / máx.) | 11,5 / 16,9 KB | 11,5 / 16,9 KB |

`POST /demo/operations/load` com A, B e C em 3 h caiu de 15,3 s para 12,2 s; o tempo que resta é a gravação e a primeira avaliação de cada operação.

O resultado com cache é igual ao cálculo completo no fim da operação, campo a campo. Ressalvas:
- uma máquina, um computador de desenvolvimento (Apple Silicon) e o PostGIS emulado em amd64;
- não mede muitas máquinas simultâneas nem a rede.

Como ordem de grandeza: com cerca de 0,15 a 0,3 s por avaliação, o orçamento de 30 s comporta dezenas de máquinas por processo. Não foi medido.

### 8.8 O que não foi validado

Telemetria real; dispositivos reais; ISOXML; API de fabricante; CAN/ISOBUS; Docker Desktop (apenas Podman); desempenho sob carga; teste de intrusão; usabilidade com operadores.

### 8.9 Plano para validar com dados reais

1. Escolher um talhão pequeno e medir a verdade-terreno (área com GNSS geodésico ou levantamento) e a largura efetiva calibrada da barra.
2. Importar um dia de telemetria (`origin="real"`), rodar `assess` e ler o relatório de qualidade antes de qualquer métrica.
3. Comparar área única estimada com a de referência e reportar o erro com a distribuição (várias máquinas/dias).
4. Para anomalias, rotular eventos reais com a manutenção e medir precisão/sensibilidade por tipo, em validação temporal (treinar em dias anteriores, avaliar em posteriores).
5. Calibrar a tolerância de vazão pela variabilidade do fluxômetro real (ver 8.5).

---

## 9. Interpretação dos resultados

### 9.1 Origem e qualidade

Antes de ler qualquer número: verificar a origem (faixa e selo), o veredito de qualidade (`ok`/`warnings`/`blocking`) e as rejeições (`/ingest/rejects`). Se uma fração grande das linhas foi rejeitada, a análise do restante pode estar enviesada.

### 9.2 Áreas

| Medida | Significa | Não significa |
|---|---|---|
| Bruta | quanto a barra varreu, contando repetição | área tratada |
| Única | terra tocada ao menos uma vez (estimada) | área corretamente aplicada (a dose pode variar) |
| Excesso (bruta - única) | área-equivalente varrida de novo | volume desperdiçado |
| Região >= 2x | onde >= 2 faixas se sobrepõem | medida confiável com GNSS ruidoso |
| `outside_field_ha` | tratado fora do limite do talhão | prova de aplicação indevida (limite impreciso ou deriva de posição) |

- A largura é premissa: variar 5% altera a sobreposição de 8,1% para 3,7% ou 12,2%, enquanto a área única quase não muda. Com largura de cadastro genérico, não confie na sobreposição.
- Ruído de posição infla a sobreposição (sigma = 1 m leva de 8% a 22%): o caminho cresce com o jitter e retângulos levemente girados deixam frestas. Use sobreposição apenas com RTK/centimétrico.
- Perda de GNSS reduz a cobertura estimada e é visível (SIM-03: -15%); é o método se recusando a interpolar.

### 9.3 Tempo, distância e produtividade

- `no_data` (lacuna acima do limite) não é parada; `sem posição` (sem lat/lon, com telemetria) conta tempo mas não distância.
- A distância em linha reta sobre perda de posição é um piso, não uma estimativa do percurso.
- ha/h de trabalho e ha/h engajado não são comparáveis entre si nem com especificação de fabricante.
- Discrepância vazão x contador próxima de 0 é bom; valor grande indica dado faltante ou calibração, mas não qual fonte está correta.

### 9.4 Alertas

- Alerta indica onde olhar, não a causa (`flow_deviation`) nem o componente (`pressure_*`).
- `critical` (só energia e autonomia) é risco estimado por extrapolação, não decisão de segurança. Antes de agir, confira a evidência: taxa, fonte da taxa, margem e idade do dado.
- Alerta operacional congelado (`indeterminate`) não está resolvido: o sistema perdeu a visão da máquina.
- Um evento pode gerar vários alertas; contar alertas não é contar eventos.
- A co-ocorrência de `pressure_outlier` e `flow_deviation` no simulador é propriedade do simulador.
- Tolerância muito apertada inunda de falsos alarmes; muito frouxa perde o evento. Use a variabilidade do sensor real.
- IsolationForest é triagem, nunca diagnóstico.

### 9.5 ROI

- O cenário de exemplo é ilustrativo e mostra benefício líquido negativo (-105 600/ano, sem payback, VPL ~ -3,8 milhões); é consequência de números fictícios.
- O tornado mostra quais premissas importam (capex, número de máquinas, área tratada, custo horário base); os pontos de equilíbrio mostram quanto precisam mudar (por exemplo área ~6 974 ha/ano, +16%; capex ~774 mil por máquina, -14%).
- Se uma premissa vier de telemetria simulada, o resultado carrega essa origem. No exemplo, usar 39,2 ha/h e 5,4% de sobreposição do simulador levou o benefício a -170 878; o ponto é a rastreabilidade da premissa, não evidência sobre máquinas reais.

### 9.6 Reprodução temporal

A posição no instante t parte da última amostra <= t; só há interpolação linear entre duas amostras consecutivas válidas separadas por <= 15 s. Antes da primeira amostra, depois da última, ou sem posição, nada é desenhado (`positionAt`, testada em `frontend/src/lib.test.ts`). A trilha é quebrada onde falta posição.

### 9.7 Checklist antes de apresentar um resultado

Origem declarada e visível; veredito de qualidade lido; largura efetiva calibrada (se houver análise de sobreposição); GNSS compatível com a medida usada; perda de posição e lacunas citadas junto com a área; alertas tratados como indícios; premissas de ROI com proveniência.

---

## 10. Limitações conhecidas

**Escopo.** Não controla máquinas; não é segurança funcional (ISO 13849/25119); não diagnostica bico entupido.

**Dados e fontes.**
- Nenhuma telemetria de máquina real foi usada; o desempenho em campo é desconhecido.
- ISOXML/TASKDATA e API de fabricante não implementados; CAN/ISOBUS bruto não usado.
- `origin = real` é declaração do remetente; o sistema não prova procedência.
- GeoJSON/GPX sem tempo por ponto são recusados.
- Unidade só é deduzida do nome da coluna ou declarada; CSV com unidade ambígua exige conferência humana.

**Simulador.** Idealizado (sem vento, relevo, deriva, falha real de bico, resposta real de bomba/válvula, controle de seção, multipath, operadores); local fictício. O recall de 4/4 é otimista, e a pressão modelada como função da vazão não é evidência sobre máquinas reais.

**Geometria e métricas.**
- Viés de subestimação de área de -1,25% a -1,4% (bruta e única) sem eventos de posição.
- Perda de GNSS não é interpolada (SIM-03: -15% de área única).
- Largura efetiva é entrada, não observação (+-5% leva a sobreposição de 3,7% a 12,2%).
- Ruído de posição infla a sobreposição (sigma = 1 m: 22% contra ~8%).
- Viés de projeção UTM em área: +0,11% neste local.
- Sem modelo de jato, sobreposição de bicos, controle de seção ou deriva: a área tratada é geométrica, não de dose.
- Sem suavização de trajetória (Kalman/Savitzky-Golay).

**Anomalias.** Limiares dependem do ruído do sensor real (o padrão de 15% foi escolhido em dados simulados); `contamination` define o número de alarmes; sem rótulos reais não há estimativa de precisão em campo; a persistência mínima atrasa a emissão (>= 20 s).

**Operação e infraestrutura.**
- Fila de ingestão em memória: mensagens ainda não gravadas se perdem se o processo cair; QoS 1 só protege até o ACK do cliente.
- Rajada sem ritmo perde mensagens com o Mosquitto padrão (ver 8.7).
- Pipeline ao vivo em um único processo; sem escala horizontal.
- Teste de carga só com uma operação, uma máquina e um computador (8.7.1); capacidade com muitas máquinas simultâneas não medida.
- Cache incremental por processo (7.11): a primeira chamada de cada operação, e a primeira depois de um descarte, custa o cálculo completo. A memória cresce com a duração da operação (~65 MB com 12 h); acima de `AGROFLEET_OPS_CACHE_SIZE` operações, as menos usadas voltam ao custo do cálculo completo.
- Imagem PostGIS amd64 emulada em Apple Silicon.
- Docker Desktop não testado (apenas Podman).
- Após recriar o `backend`, reiniciar o `frontend` (nginx resolve o upstream na subida).
- TimescaleDB não usado; séries muito longas podem exigir particionamento.

**Clima (7.12).**
- As rotas do INMET não têm documentação oficial e são o backend do próprio portal: podem mudar ou ser bloqueadas sem aviso. A rota com token só existe para quem tem Acordo de Cooperação Técnica com o INMET, e em 2026 os tokens de empresas privadas foram cancelados.
- A estação mais próxima pode estar a dezenas de km e em outro relevo; a leitura vale para a estação, não para o talhão. Dado de estação automática é bruto, sem validação do INMET.
- A previsão do INMET é por município, textual e por faixa, sem valores pontuais. A do MET Norway e as condições do Open-Meteo são modelo, não medição.
- Os campos do catálogo `/estacoes/T` e da rota com token foram implementados pelos nomes já vistos na rota pública e no catálogo, sem um token válido para conferir a resposta real.
- Cache por processo: reiniciar a API esvazia o cache; vários processos fazem buscas independentes.
- A conversão ponto → município não é automática: o código IBGE vem do cadastro da propriedade ou de `AGROFLEET_WEATHER_IBGE`.

**Reprodutibilidade.** `playwright`, `matplotlib`, `nbconvert`, `ipykernel` e `openpyxl` estão sem versão fixada em `requirements-dev.txt`; não há `pyproject.toml`; os notebooks foram executados com a semente 7 e não foram verificados com outras sementes.

**Uso responsável.** Não usar alertas ou estimativas como única base para decisão de segurança, cobrança entre terceiros, conformidade regulatória ou laudo.

---

## 11. Segurança

**Existe:** autenticação JWT HS256 (TTL padrão 8 h); se `AGROFLEET_AUTH_SECRET` não existir, o backend gera um segredo aleatório em memória (tokens não sobrevivem a reinício; `GET /health` mostra `auth_secret_generated: true`) e o compose nem sobe sem o segredo; três papéis; senhas com scrypt, sem senha padrão (usuário só é criado se a senha vier do ambiente); WebSocket autenticado na primeira mensagem; MQTT sem acesso anônimo, com ACL e senhas geradas em runtime; contêiner do backend sem root; verificação de certificado nunca desabilitada.

**Não existe:** TLS ligado no compose local (cliente MQTT suporta; listener 8883 é recomendação), MFA/SSO, revogação de token, limitação de tentativas de login, trilha de auditoria de acesso, rotação de segredos, teste de intrusão, endurecimento do nginx (CSP/HSTS). Os testes E2E têm senhas locais de teste como padrão; não use esses valores fora de máquina de desenvolvimento.

---

## 12. Licenças e atribuições

- **Código:** GPL-3.0 (arquivo `LICENSE`).
- **Dados:** todos os dados do repositório (`sample_data/`, `payload_examples/`) são sintéticos ou fictícios, gerados pelo simulador ou escritos à mão; nenhum dado de terceiros está incluído.
- **Dependências:** bibliotecas de terceiros (FastAPI, SQLAlchemy, pandas, numpy, scikit-learn, shapely, pyproj, React, MapLibre GL JS, entre outras) mantêm suas próprias licenças; ver `requirements*.txt` e `frontend/package.json`.
- **Imagens PostGIS e Mosquitto:** imagens oficiais, com licenças próprias.
- **Clima (consultado em tempo de execução, não versionado no repositório):**
  - **INMET:** dados públicos de órgão federal (MAPA). O Decreto 8.777/2016 (Política de Dados Abertos, Art. 4 com redação do Decreto 9.903/2019) prevê livre utilização com crédito da fonte; o INMET não publica licença explícita nem termo de uso. Exibir sempre "Fonte: INMET" e nunca apresentar o dado como validado. O acesso automatizado por token exige Acordo de Cooperação Técnica, e em 2026 o INMET declarou oposição à cópia automatizada e não descartou cobrança. Antes de uso comercial, pedir confirmação por escrito ao INMET.
  - **Open-Meteo:** dados sob CC BY 4.0, com crédito "Weather data by Open-Meteo.com" e link junto do dado. A API gratuita é só para uso não comercial; uso comercial exige plano pago (endpoint `customer-api.open-meteo.com` com chave). O código do servidor é AGPLv3.
  - **MET Norway:** dados sob CC BY 4.0 e NLOD 2.0, que permitem uso comercial, com crédito "Data from MET Norway"; os termos de serviço exigem User-Agent identificador e respeito ao `Expires`.

---

## 13. Solução de problemas

| Sintoma | Causa provável | Ação |
|---|---|---|
| Tela inteira com erro 502 após atualizar o servidor | nginx não reconectou ao backend recriado | `podman compose restart frontend` |
| "Nenhuma máquina ao vivo" | simulador/dispositivo não publica, ou credenciais MQTT erradas | verificar `GET /health` (`mqtt.connected`) |
| Upload rejeita todas as linhas | falta fuso, unidade ou coluna obrigatória (`machine_id`, `ts`) | ler o motivo nas rejeições; informar `assume_tz`/`units` |
| Cobertura menor que o esperado | perda de GNSS, filtro de precisão ou largura ausente | ver "excluídos" na aba Cobertura |
| Sobreposição "impossível" | largura mal calibrada ou GNSS ruidoso | ver 9.2 |
| Mapa sem fundo | o fundo é em branco por padrão | opcional: `VITE_BASEMAP_TILES` na construção |
| O compose não sobe | variáveis obrigatórias ausentes no `.env` | trocar todos os `CHANGE_ME` |
| `ModuleNotFoundError: agrofleet` | `PYTHONPATH` não definido | `export PYTHONPATH=.` na raiz |
| `compose restart mosquitto` falha | arquivo de senhas pré-existente (corrigido em `entrypoint.sh` com `rm -f` antes de gerar) | atualizar o repositório e recriar o contêiner |
| Mensagens perdidas em rajada | `max_queued_messages` padrão do Mosquitto | ver 8.7; publicar com ritmo (`--speedup`) |
| Clima com "Sem dados meteorológicos" | propriedade sem `ibge_municipality`, provedor fora do ar ou bloqueado | ler `reasons` da resposta; cadastrar o município ou definir `AGROFLEET_WEATHER_IBGE`; falhas ficam em cache por 120 s |

---

## 14. Referências

ISO 11783 (ISOBUS), partes 10 e 11. Snyder, *Map Projections: A Working Manual* (USGS PP 1395, 1987). Karney, "Algorithms for geodesics", *J. Geodesy* 87 (2013). Liu, Ting & Zhou, "Isolation Forest", IEEE ICDM (2008). RFC 7946 (GeoJSON). RFC 7519 (JWT). MQTT 3.1.1/5.0 (OASIS). GPX 1.1. Documentação de PostGIS, shapely, pyproj, FastAPI e MapLibre GL JS.
