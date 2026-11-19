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

## Etapa 4 — Missão e autonomia ✅

**Objetivo.** Calcular no backend, com funções puras e testadas, o que o painel da referência mostra em "Missão da Operação", "Previsão de conclusão" e "Autonomia e Conclusão". Interface fora do escopo (Etapa 8).

**Alterações**

| Arquivo | Mudança |
|---|---|
| `agrofleet/analytics/coverage.py` | **Correção dos micro-buracos** (achado das Etapas 2–3): para a área única, cada trecho contínuo vira uma faixa (buffer da polilinha, ponta reta, junção arredondada). Bruta, região ≥ 2x e exclusões não mudam. `CoverageResult` passa a carregar as geometrias em metros (campos novos, opcionais). |
| `agrofleet/analytics/mission.py` | Área planejada (com fonte), trabalhada = união ∩ talhão, progresso, horas por estado e sem dado, série de 10 min com área nova, ha/h efetivo e consumo, ritmo da última hora, faixa Q1–Q3, ETA com faixa, motivos de indisponibilidade e avisos. |
| `agrofleet/analytics/autonomy.py` | Diesel e elétrico: nível (mediana de 5 min), reserva, utilizável, taxa ponderada pelo tempo (ou inclinação do nível, sinalizada), verificação cruzada entre as duas taxas, autonomia, necessidade, margem central e pior caso; estados verde, amarelo, vermelho e cinza com motivo; classe de nível separada. |
| `backend/app/operations_api.py` | `GET /operations/{id}/status` e `GET /operations/{id}/geometry`, com `as_of` para reprodução; conexão por `ingested_at` e `ts`; "MODO SIMULAÇÃO" para origem simulada. |
| `backend/app/main.py` | Monta o roteador (uma linha). |
| `tests/test_mission_autonomy.py` | 39 testes: casos à mão, geometria analítica, cenários A/B/C contra a verdade e API. |
| `tests/test_infra.py` | Status e geometria no PostgreSQL real (TIMESTAMPTZ de `ingested_at`). |
| `docs/GUIA_TECNICO.md` | 5.4 (rotas), 7.5 (faixa por trecho), 7.9 (missão e autonomia), 8.2 e 10 (viés remedido). |

**Cobertura antes e depois**

| Medida | Antes | Depois |
|---|---|---|
| Anéis internos, talhão antigo (SIM-01) | 1.713 | 2 |
| Anéis internos, Talhão 03 em 3 h / completo | 9.157 / 39.807 | 2.494 / 10.807 (frestas reais de ~1,4 cm, 191 m² em 3 h) |
| Erro da área única sem eventos de posição | −1,6% a −1,8% | −1,25% a −1,4% (igual ao viés da bruta) |
| Falso excesso por jitter de RTK-float (SIM-03) | 1,08% | 0,07% |
| Talhão 03 completo, trabalhado vs planejado | −1,2% | −0,16% |
| Tempo de cobertura, Talhão 03 completo | 7,3 s | 3,9 s |

**Decisões técnicas**
- **Ritmo efetivo em tempo de relógio.** O ha/h que gera o tempo restante inclui manobras e paradas, e a taxa de consumo usa a mesma base, de modo que necessidade = taxa × restante fica coerente. O ha/h por hora em trabalho também é informado, mas não entra na ETA.
- **Faixa empírica, não intervalo de confiança.** Q1–Q3 do ha/h por intervalo desta operação. Não há modelo estatístico validado que justifique chamar isso de probabilidade.
- **Cinza em vez de valor presumido.** Sem reserva, sem margem exigida, sem capacidade, sem taxa positiva ou com dado velho, o estado é cinza com o motivo; os valores que ainda são calculáveis (nível, área) continuam na resposta.
- **Idade do dado = max(enlace, dado).** Carga histórica recém-importada não parece ao vivo, e relógio do dispositivo adiantado não esconde um enlace parado. Consequência na demo: minutos depois de `POST /demo/operations/load`, a operação aparece como "Atrasado" e depois "Sem conexão", e a autonomia fica cinza. É o comportamento certo para monitoramento; a reprodução usa `as_of`.
- **Frestas reais não são apagadas.** As frestas de ~1 cm entre passadas planejadas lado a lado ficam no cálculo de área; só o desenho aplica fechamento de 5 cm e simplificação de 0,5 m, e a resposta avisa isso.

**Testes**

| Suíte | Resultado |
|---|---|
| `pytest` (unitários + API) | **184/184** (145 anteriores, sem nenhuma alteração, + 39 novos) |
| `pytest -m infra` | **5/5** (com status e geometria no PostGIS) |
| `tsc --noEmit` / `vitest` | OK / 12/12 (frontend não alterado) |
| Demo local (A, B, C, 3 h, terminando agora) | A Viável (margem de 36%), B Insuficiente (−72%, nível Baixo), C Viável; 1,1 s por chamada de status |

**Riscos remanescentes**
- O consumo e o ritmo futuros podem mudar com solo, relevo, carga e operador; a estimativa só extrapola a última hora. Nada foi validado com máquina real.
- A ETA mira 100% da área planejada; quando a cabeceira não é trabalhada, a operação termina um pouco antes (A: 99,6%).
- O status recalcula tudo a cada chamada (1,2 s em 3 h; 7,6 s em 12 h). O cache incremental é da Etapa 6.
- As saídas gravadas no notebook 02 ainda mostram os números de cobertura anteriores; serão regeneradas na Etapa 9.

## Etapa 5 — Alertas operacionais ✅

**Objetivo.** Alertas de energia, risco de não conclusão, manutenção e perda de conexão com ciclo de vida, deduplicação, histerese e critérios de qualidade dos dados, conforme a decisão C. As regras de evento existentes (`anomalies.py`) não mudam.

**Alterações**

| Arquivo | Mudança |
|---|---|
| `agrofleet/analytics/op_alerts.py` | Máquina de estados pura: quatro famílias, uma ativa por máquina, confirmação por severidade, histerese de saída, congelamento com dado ruim, portões do `critical`; `snapshot_from_data` monta as entradas com os cálculos da Etapa 4. |
| `backend/app/op_alerts_service.py` | Estado persistido em `settings`; transições gravadas em `alerts`; `evaluate`, `replay` e `watchdog`; formato único de alerta (eventos antigos ganham `category` e `status` derivados). |
| `backend/app/live.py` | Avaliação operacional no laço ao vivo (no máximo a cada 30 s por máquina) e vigia de conexão a cada 30 s, mesmo sem mensagens. |
| `backend/app/main.py` | `/alerts` com filtros e `sort=priority` (aditivo; ordem padrão mantida); `GET\|PUT /config/alerts/operational`; `POST /operations/{id}/alerts/replay`; recálculo e demo reabrem os alertas sustentados; recálculo de eventos não apaga operacionais; `DELETE /data` limpa o estado. |
| `backend/app/operations_api.py` | `load_context` extraído para reúso; `/status` inclui os alertas ativos da máquina por prioridade. |
| `backend/app/db.py` | Colunas aditivas em `alerts`: `category`, `status`, `operation_id`, `resolved_at`, `history` (migração automática; linhas antigas continuam válidas). |
| `frontend/src/Alerts.tsx`, `styles.css` | Adição mínima: rótulos das famílias novas, coluna Situação (severidade + ativo/resolvido), marca vermelha e nota do `critical`, ordem por prioridade. |
| `tests/test_op_alerts.py` | 30 testes. |
| `tests/test_infra.py` | Migração das colunas novas de `alerts` e alertas operacionais + vigia no PostgreSQL real. |
| `docs/GUIA_TECNICO.md` | 2.2, 5.4, 5.5, 7.6, 7.10 (novo) e 9.4. |

**Decisões técnicas**
- **Camada nova em vez de mudar as regras de evento.** Eventos (início e fim numa janela) e condições com estado (ativo até resolver) têm semânticas diferentes; misturá-las mudaria alertas e testes existentes. Ambas usam a mesma tabela e a mesma rota, com `category`.
- **Uma família, várias severidades.** Nível de energia é um alerta só, que sobe de aviso para crítico e desce com histerese, em vez de dois alertas simultâneos para a mesma causa. O mesmo vale para a autonomia.
- **Confirmação por severidade.** Um `critical` precisa da própria confirmação: não nasce de um instante nem herda o tempo de um aviso anterior.
- **Dado ruim congela, não resolve.** Sem leitura confiável, o estado anterior fica como está e é marcado como indeterminado; perder a visão da máquina não é o mesmo que o problema ter passado.
- **`critical` de autonomia com portões.** Exige taxa de sensor, fontes concordantes e janela sem lacunas; sem isso o alerta fica em aviso e diz por quê.
- **Manutenção só por regra cadastrada.** Sem intervalo e horímetro de referência no cadastro, a regra é pulada e relatada. O aviso prévio é opcional e desligado por padrão.
- **Horímetro pela última leitura válida.** É contador acumulado; a mediana de 5 min, usada para o nível, atrasaria o vencimento em ~2,5 min.
- **Vigia separado.** Uma máquina que para de enviar não dispara nada pela ingestão; o vigia só olha a idade do dado (barato), sem recalcular a missão.

**Testes**

| Suíte | Resultado |
|---|---|
| `pytest` (unitários + API) | **214/214** (184 anteriores, sem nenhuma alteração, + 30 novos) |
| `pytest -m infra` | **5/5** (com migração de `alerts` e alertas operacionais no PostGIS) |
| `tsc --noEmit` / `vitest` | OK / 12/12 |
| Demo local (A, B, C, 3 h) | B: risco de não conclusão crítico + nível de bateria em aviso; A e C sem alertas; aba Alertas conferida com Playwright |

**Comportamento esperado na demo.** Cinco minutos depois da carga, o vigia abre `connection_lost` para as três operações, porque nenhuma máquina está enviando. Energia e autonomia ficam como estavam: sem dado novo não há reavaliação, e uma reavaliação com dado velho congela o estado em vez de resolver o crítico. Para ver alertas mudando ao vivo, publique com `simulator/publish.py --scenario A,B,C` (mesmo `start`).

**Riscos remanescentes**
- Os parâmetros anti-ruído (60 s, 120 s, 2 pontos, 5 de margem) não foram calibrados com máquina real; estão expostos na configuração.
- Cada avaliação ao vivo recalcula a missão (1 a 7 s conforme a duração da operação). Com muitas máquinas, o intervalo de 30 s pode não ser cumprido; o cache da Etapa 6 resolve.
- O alerta exibido em `/status` com `as_of` é o estado atual, não o daquele instante (o histórico completo exige replay com `window_s`).
- O estado ao vivo fica em um processo; com várias réplicas da API, cada uma avaliaria a mesma máquina (implantação atual: uma réplica).
