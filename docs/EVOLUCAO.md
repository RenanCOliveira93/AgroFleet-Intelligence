# Evolução para plataforma de telemetria e inteligência operacional

Registro por etapa do plano aprovado em 2026-11-18. Cada etapa termina com testes executados e resultados relatados antes da etapa seguinte.

## Decisões aprovadas

| Tema | Decisão |
|---|---|
| A. Mapa-base | MapLibre GL. Provedores configuráveis por variável de ambiente, sem dependência rígida de fornecedor comercial. Prioridade para fontes gratuitas e legalmente compatíveis; alternativa funcional quando não houver fonte gratuita adequada. |
| B. Clima | Integração desacoplada. INMET prioritário; Open-Meteo só em desenvolvimento não comercial. Sem API paga. Quando não houver dados, exibir "Sem dados meteorológicos". |
| C. Alertas | Severidade `critical` permitida só para autonomia insuficiente e para combustível ou bateria em nível perigosamente baixo, com limites por máquina. Nível baixo e risco previsto de não conclusão são alertas distintos. Deduplicação, histerese e critérios de qualidade dos dados. Continua sendo só monitoramento. |
| D. Tema | Sistema global claro, escuro ou automático, em todas as telas, com preferência persistida. O escuro reproduz a referência. |

Diretrizes: nada pago; open source compatível com uso comercial futuro; integrações desacopladas; compatibilidade com a telemetria e os testes existentes; nenhum dado de sensor inventado; separação entre real e simulado; cálculos no backend; reformulação visual só depois das etapas funcionais.

## Linha de base (antes da Etapa 1)

| Suíte | Resultado |
|---|---|
| `pytest` (unitários + API) | 72/72 |
| `pytest -m infra` (PostGIS + Mosquitto reais) | 3/3 |
| `tsc --noEmit` / `vitest` | OK / 11/11 |
| `pytest -m e2e` | não executado (exige a pilha compose completa) |

## Etapa 1 — Contrato de telemetria 1.1 ✅

**Objetivo.** Dar ao modelo canônico campos para motor, combustível, bateria e rumo, sem quebrar a telemetria 1.0 e sem inventar valores.

**Alterações**

| Arquivo | Mudança |
|---|---|
| `agrofleet/schema.py` | `SCHEMA_VERSION` 1.0 → **1.1**; tópico inalterado (`agrofleet/v1`). Nove campos opcionais com validação de faixa e finitude (`ENGINE_ENERGY_FIELDS`, fonte única com unidade e referência J1939/BMS/GNSS). Cinco chaves novas em `sensor_quality`. |
| `agrofleet/analytics/frame.py`, `metrics.py`, `quality.py` | Colunas novas no DataFrame e em `null_pct`; suspeitas de unidade (fração enviada como %, rpm, L/h e kWh implausíveis), só sinalizadas. |
| `agrofleet/adapters/tabular.py` | Aliases PT/EN e unidades (gal/h, L/min, Wh, W, rad, fraction, min, s); tabela única `NUMERIC_TARGETS`. |
| `backend/app/db.py` | Colunas 1.1 e `ingested_at` (horário de gravação no servidor). `add_missing_columns`: migração aditiva e idempotente chamada por `init_db`. `load_frame` usa `SENSORS`. |
| `backend/app/live.py`, `main.py` | WebSocket `telemetry` e `GET /machines` com os campos novos (aditivo); `/machines` também expõe `ingested_at`. |
| `payload_examples/` | Envelopes 1.1 (diesel, elétrico, inválido) e CSV com unidades; README atualizado. Os exemplos 1.0 não foram alterados e provam a compatibilidade. |
| `tests/test_schema_v11.py` | 35 testes novos. |
| `tests/test_infra.py` | Teste de migração 1.0 → 1.1 no PostGIS real (banco descartável próprio). |
| `tests/test_schema_adapters.py` | **Única alteração em teste existente:** a asserção `SCHEMA_VERSION == "1.0"` passou a `"1.1"`. |
| `docs/GUIA_TECNICO.md` | Seções 3.2, 3.3 e 3.5. |

**Fora do escopo desta etapa (de propósito).** O simulador não gera os campos novos (Etapa 3); a interface não os exibe (Etapa 8); não há nenhum cálculo de autonomia (Etapa 4).

**Decisões técnicas**
- **Sem `fuel_level_l`.** Em J1939, o nível de combustível vem em %. Litros = % × capacidade do tanque, que é cadastro (Etapa 2). Dois campos para a mesma grandeza abririam espaço para inconsistência.
- **`battery_power_kw` com sinal.** Positivo é descarga; negativo é recarga ou regeneração. Rejeitar valores negativos descartaria leituras legítimas.
- **Minor informativo.** Um envelope `1.0` com campo 1.1 válido é aceito. Só o major é contrato de compatibilidade, como já era.
- **`ingested_at` no banco, não no `TelemetryPoint`.** O modelo descreve o que a máquina reportou, e o horário de gravação é do servidor. Ele separa o tempo do dado do tempo de chegada, o que a detecção de desconexão (Etapa 5) e o relógio acelerado do simulador exigem.
- **Migração sem dependência nova.** O projeto não usa Alembic; a função só adiciona colunas anuláveis e falha explicitamente em qualquer outro caso.

**Testes**

| Suíte | Resultado |
|---|---|
| `pytest` (unitários + API) | **107/107** (72 existentes + 35 novos) |
| `pytest -m infra` | **4/4** (3 existentes + migração no PostGIS) |
| `tsc --noEmit` / `vitest` | OK / 11/11 (frontend não alterado) |

**Riscos remanescentes**
- A disponibilidade real de SPN 92/96/183/190/247 depende do fabricante e do gateway CAN; nada disso foi validado com máquina real.
- Um backend 1.0 rejeita payloads com campos 1.1: a ordem de implantação é backend primeiro, depois dispositivos.
- `ingested_at` marca a gravação em lote (fila de até ~0,5 s), não a chegada exata da mensagem MQTT.

## Etapas 2 e 3 — Cadastro e simulador de operações ✅

Executadas juntas, a pedido. A interface recebeu apenas uma adição mínima (nome do cadastro e campos de motor e energia no card da Frota, exibidos só quando reportados); a reformulação visual continua reservada à Etapa 8.

**Etapa 2 — Cadastro**

| Arquivo | Mudança |
|---|---|
| `agrofleet/registry.py` | Modelos `PropertyInfo`, `MachineProfile` e `OperationPlan`, com validação de coerência (capacidade só da fonte declarada; crítico < aviso; crítico ≥ reserva; manutenção exige intervalo e horímetro de referência; operação, máquina e talhão com a mesma origem). |
| `backend/app/db.py` | Tabelas `properties`, `machine_profiles` e `operations` (o modelo validado fica em `spec` JSON). |
| `backend/app/registry_api.py` | Rotas `/registry/properties`, `/registry/machines` e `/operations` (leitura: viewer; escrita: admin/analyst). |
| `backend/app/main.py` | Monta as rotas; `/machines` inclui `name`, `kind` e `energy_source` só de perfil com a mesma origem; `DELETE /data` também apaga o cadastro da origem e informa as contagens (aditivo). |

**Etapa 3 — Simulador v2**

| Arquivo | Mudança |
|---|---|
| `agrofleet/simulator/operations.py` | Talhão poligonal irregular (área exata), passadas recortadas em boustrophedon, modelos diesel, elétrico e de calda, rumo, quantização de sensores, verdade-terreno, falhas de enlace (`apply_link_faults`). Cenários A, B e C. O `fleet.py` original não foi alterado. |
| `backend/app/main.py` | `POST /demo/operations/load` (admin): cadastro + telemetria; no cenário C só entram as amostras entregues. |
| `simulator/publish.py` | `--scenario A,B,C` e `--start`; o C publica conforme a agenda de falhas. O padrão (`fleet`) mantém o comportamento original, verificado por teste. |
| `frontend/src/lib.ts`, `Fleet.tsx` | Campos 1.1 no tipo `MachineRow` e no WebSocket (só copia os presentes); o card mostra nome e tipo e exibe rotação, carga, combustível, consumo, bateria e potência só quando a máquina os reporta. "Tanque" virou "Tanque de calda". |

**Decisões técnicas**
- **Cadastro em `spec` JSON + colunas de filtro.** O cadastro muda mais que a telemetria; assim não exige migração a cada campo novo. A validação fica no modelo pydantic, não no banco.
- **Nenhum limite padrão escondido.** Sem `energy_warning_pct` ou sem regra de manutenção, o alerta correspondente não roda e isso será relatado (Etapa 5).
- **Números do simulador fisicamente coerentes, não copiados da imagem.** A referência mostra 24,3 ha/h, o que é implausível para preparo de solo a 7,5 km/h. O cenário A usa 12 m a 8,5 km/h (cerca de 10 ha/h teóricos). A regra de manutenção do A (intervalo de 250 h, última aos 4.582 h) é **configuração** do cadastro; o alerta só existirá por ela.
- **Retorno à base do autônomo** é comportamento da máquina simulada, registrado como evento na verdade-terreno, e não decisão do sistema, que continua só monitorando.

**Testes**

| Suíte | Resultado |
|---|---|
| `pytest` (unitários + API) | **145/145** (107 anteriores + 21 de cadastro + 17 do simulador) |
| `pytest -m infra` | **5/5** (+ cenários no PostGIS: área elipsoidal do Talhão 03 = 120 ha ±0,3%; trabalho dentro do talhão via `ST_Within`) |
| `tsc --noEmit` / `vitest` | OK / **12/12** |
| Execução local (SQLite + Vite) com A, B e C, 3 h | Frota, Reprodução e Cobertura conferidas com Playwright |

**Achados para as próximas etapas**
- **Cobertura com micro-buracos (backend, Etapa 4).** A união de faixas do Talhão 03 em 3 h tem cerca de 9.000 anéis internos: pequenas cunhas entre os retângulos de cada segmento de 1 s. Isso subestima um pouco a área única (viés já documentado de −1,8%), deixa o mapa hachurado e pesa na renderização. Proposta: faixas por trecho contínuo (buffer de linha com junções), validadas contra a verdade.
- **Desempenho da cobertura (Etapa 6).** Talhão de 120 ha completo: cerca de 7 s por cálculo. Exige cache incremental antes de qualquer atualização ao vivo.
- **Enquadramento do mapa (frontend, Etapa 8).** Na aba Cobertura, com cobertura parcial, o enquadramento automático não inclui o talhão inteiro, embora a API devolva o polígono completo. Não aparecia antes porque, nos talhões retangulares antigos, a área coberta ocupava o talhão quase inteiro.
- **Localização fictícia.** Os talhões simulados ficam perto de (−16,70; −47,60). Com uma camada de satélite real (Etapa 8), aparecerão sobre uma imagem que não corresponde a eles; isso deve ser sinalizado na interface.
