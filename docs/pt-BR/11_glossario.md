# 11 — Glossário PT/EN

Termos ordenados alfabeticamente pelo português. "No projeto" aponta onde o termo aparece.

| Português | English | Definição | No projeto |
|---|---|---|---|
| Alerta informativo | Informational alert | Aviso sem poder de decisão; indica onde olhar | `Alert`, nota `INFO_NOTE` |
| Área bruta | Gross area | Σ comprimento × largura das faixas, contando repetição | `coverage.gross_m2` |
| Área única | Unique area | Área da união das faixas (terra tocada ≥ 1 vez) | `coverage.unique_m2` |
| Atraso de início | Onset delay | Diferença entre início do evento e do alerta | `evaluate.delay_s` |
| Autenticação | Authentication | Verificar quem é o usuário | JWT, `/auth/login` |
| Barra (de pulverização) | Boom | Estrutura com bicos que define a largura de trabalho | `working_width_m` |
| Bico | Nozzle | Ponto de saída da calda | [06](06_algoritmos_modelos.md) |
| Bissecção | Bisection | Busca de raiz por divisão de intervalo | `roi.break_even` |
| Cabeceira | Headland | Faixa nas extremidades do talhão para manobras | segmentos exigem ambos os extremos em `working` |
| CAN | Controller Area Network | Barramento serial de veículos | base do ISOBUS; não usado |
| Contador acumulado | Cumulative counter | Total que só cresce e pode zerar | `consumption_l`, `counter_resets` |
| Contaminação | Contamination | Fração esperada de atípicos no IsolationForest (orçamento de alarmes) | `iforest_contamination` |
| Cobertura | Coverage | Parte do talhão tocada pela aplicação | `coverage_pct_of_field` |
| Dado simulado | Simulated data | Gerado por modelo; nunca apresentado como real | `origin="simulated"` |
| DDI | Data Dictionary Identifier | Identificador de grandeza na ISO 11783-11 | [02](02_fundamentos.md) |
| Deriva | Drift | Deslocamento da calda pelo vento | não modelada |
| Dose / taxa de aplicação | Application rate | Volume por área (L/ha) | `application_rate_lha` |
| Envelope (MQTT) | Envelope | Estrutura versionada que embrulha o payload | `schema.Envelope` |
| Estado operacional | Operational state | working, transport, idle, refilling, fault, off | `State` |
| Excesso | Excess (overlap area-equivalent) | bruta − única | `excess_m2` |
| Falso positivo | False positive (FP) | Alarme sem evento real | varredura de tolerância |
| Fix GNSS | GNSS fix | Tipo de solução de posição | `Gnss` |
| Fluxômetro | Flow meter | Sensor de vazão | `flow_lpm` |
| Frota | Fleet | Conjunto de máquinas | `/machines` |
| Geodésia | Geodesy | Medidas no elipsoide terrestre | `pyproj.Geod`, `geography` |
| GNSS | Global Navigation Satellite System | GPS, Galileo, GLONASS, BeiDou… | [02](02_fundamentos.md) |
| Hectare-hora | Hectare per hour (ha/h) | Produtividade de área | `productivity` |
| Horas engajadas | Engaged hours | Tempo com a máquina ligada e em operação (inclui deslocamento, parada, reabastecimento) | `unique_ha_per_engaged_h` |
| Idempotência | Idempotency | Repetir a operação não muda o resultado | `ON CONFLICT DO NOTHING` |
| Interpolação | Interpolation | Preencher valores entre observações | **evitada** sobre perda de GNSS/lacunas |
| ISOBUS | ISOBUS (ISO 11783) | Normas de comunicação trator–implemento | não lido diretamente |
| ISOXML | ISOXML (ISO 11783-10) | XML de tarefas e *time logs* | **não implementado** |
| JWT | JSON Web Token | Token assinado de acesso | HS256 |
| Lacuna | Gap | Intervalo sem dados maior que o limite | `no_data`, `data_gap` |
| Largura efetiva | Effective working width | Largura realmente tratada pela barra | `working_width_m`; calibrar |
| Limiar | Threshold | Valor de corte | `flow_tol_pct` |
| Persistência | Persistence | Tempo mínimo de condição para gerar alerta | `min_duration_s` |
| MAD | Median Absolute Deviation | Dispersão robusta | z robusto da pressão |
| Máquina autônoma | Autonomous machine | Opera com guiagem automática | escopo: monitoramento apenas |
| MQTT | MQTT | Protocolo publish/subscribe leve | Mosquitto |
| Nulo | Null | Valor ausente; **nunca** zero | `None`/`null` |
| Origem | Origin | simulated / real / unknown | `origin` |
| Ponto de equilíbrio | Break-even | Valor de uma premissa com benefício zero | `break_even` |
| Pressão global | Global pressure | Pressão medida no circuito, não por bico | `pressure_bar` |
| Proveniência | Provenance | De onde veio um dado ou premissa | `RoiInputs.provenance` |
| PostGIS | PostGIS | Extensão geoespacial do PostgreSQL | db no compose |
| Região ≥ 2× | Overlap region | Onde ≥ 2 faixas se sobrepõem | `overlap_region_m2` |
| Reprodução temporal | Playback | Reexibir o histórico no tempo | `/playback` |
| Reabastecimento | Refilling | Reposição de calda | `State.refilling` |
| Retângulo de faixa / swath | Swath | Área varrida por segmento | `swath_polygons` |
| ROI / retorno | Return on investment | Benefício relativo ao investimento | `analytics/roi.py` |
| RTK | Real-Time Kinematic | Correção GNSS de nível centimétrico | `rtk_fixed`, `rtk_float` |
| Salto de posição | Position jump | Velocidade implícita irreal entre posições | `position_jumps` |
| Sensibilidade (análise) | Sensitivity analysis | Quanto o resultado varia com as premissas | tornado, grade |
| Sensibilidade (métrica) | Recall / sensitivity | Fração de eventos detectados | `evaluate.recall` |
| Série temporal | Time series | Observações ordenadas no tempo | telemetria |
| Telemetria | Telemetry | Dados medidos e transmitidos | `TelemetryPoint` |
| Time log | Time log | Registro temporal binário (ISOXML TLG) | não implementado |
| Tornado | Tornado diagram | Barras de impacto de cada premissa ±x% | `sensitivity` |
| UTM | Universal Transverse Mercator | Projeção plana em metros por fuso | `Projector` |
| Valor presente líquido | Net present value (NPV) | Fluxo descontado menos investimento | `npv_over_useful_life` |
| Vazão | Flow rate | Volume por tempo (L/min) | `flow_lpm` |
| Vazão esperada | Expected flow | R·v·w/600 | `expected_flow_lpm` |
| Verdade-terreno | Ground truth | Valor de referência conhecido | `Truth`, `sim_truth.json` |
| WebSocket | WebSocket | Canal bidirecional sobre HTTP (aqui só servidor→cliente) | `/ws` |
| Z robusto | Robust z-score | (x − mediana)/(1,4826·MAD) | `pressure_outlier` |
