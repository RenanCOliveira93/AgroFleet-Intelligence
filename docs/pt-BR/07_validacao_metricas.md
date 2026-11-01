# 07 — Validação e métricas

Princípio: **só afirmamos o que foi executado**. Cada número abaixo aparece na saída de um notebook, de um teste ou de `experiments/geodesy_check.json`. Tudo vale **para dados simulados**; nada aqui valida desempenho em máquina real.

## 7.1 Camadas de validação

| Camada | O que garante | Onde |
|---|---|---|
| Testes unitários e de propriedade | invariantes (soma de tempo fecha, bruta ≥ única, nulo continua nulo, comando rejeitado) | `tests/test_geometry.py`, `test_schema_adapters.py`, `test_analytics.py` |
| Exemplos de payload | cada exemplo documentado se comporta como descrito | `tests/test_payload_examples.py` |
| API (SQLite) | autenticação/papéis, upload com qualidade, idempotência, relatórios, WebSocket | `tests/test_api.py` |
| Infra real | PostGIS (geografia, `ST_Within`, `ON CONFLICT`) e Mosquitto (auth, ACL, ingestão ao vivo) | `tests/test_infra.py` (`-m infra`) |
| E2E em navegador | Playwright/Chromium contra a pilha no ar | `tests/e2e/test_ui.py` (`-m e2e`) |
| Frontend | funções puras (reprodução sem extrapolar, trilha) e checagem de tipos | `frontend/src/lib.test.ts` (vitest), `tsc --noEmit` |
| Verdade do simulador | erro das estimativas contra áreas analíticas e eventos injetados | notebooks 02 e 03 |
| Cruzamento geodésico | UTM × `pyproj.Geod` × PostGIS `geography` | `scripts/validate_geodesy.py` |

A contagem de testes executados nesta entrega está no [README](../../README.md) (seção "Resultados comprovados").

## 7.2 Geometria: erro contra a verdade analítica

**Caso mínimo** (2 passadas, 495 m × 24 m, espaçamento 22 m): esperado bruta 23 760 m², sobreposição 990 m², única 22 770 m². Obtido 23 760 / 980 / 22 780.

**Frota simulada** (notebook 02, saída real; "real" = verdade analítica do simulador):

| Máquina | Bruta est. (ha) | Bruta real | Única est. | Única real | Sobrep. est. | Sobrep. real | Erro bruta | Erro única |
|---|---|---|---|---|---|---|---|---|
| SIM-01 | 22,48 | 22,8 | 20,66 | 21,00 | 8,12% | 7,89% | −1,39% | −1,64% |
| SIM-02 | 21,29 | 21,6 | 20,38 | 20,75 | 4,28% | 3,94% | −1,41% | −1,77% |
| SIM-03 | 17,36 | 20,4 | 17,17 | 20,40 | 1,08% | 0,00% | −14,92% | −15,84% |
| SIM-04 | 22,48 | 22,8 | 20,66 | 21,00 | 8,12% | 7,89% | −1,39% | −1,64% |

- Em máquinas sem eventos de posição o erro é **−1,4 a −1,8%** (viés sistemático de subestimação). A **causa não foi investigada**; é uma hipótese não testada que a discretização a 1 Hz perca parte das pontas e transições das passadas. Trate o viés como medido, e a explicação como pendente.
- SIM-03 tem erro de **−15%**: a perda de GNSS de 4 min (241 amostras sem posição) remove cobertura que o método **recusa-se a interpolar**. É o comportamento desejado, e é por isso que a perda de posição aparece em coluna própria (`perda_pos`).
- A sobreposição de SIM-03 (1,08% vs 0% real) é um falso excesso causado pelo jitter dos 2 min de RTK-float (0,35 m).

## 7.3 Sensibilidades medidas (notebook 02)

| Perturbação | Efeito medido |
|---|---|
| Largura efetiva ×0,95 / ×1,05 | sobreposição 3,67% / 12,15% (de 8,12%); única muda < 1%; bruta ±5% |
| Largura ×0,90 | sobreposição 0,43% (barra menor que o espaçamento: haveria fresta) |
| Ruído de posição σ = 0,1 / 0,5 / 1 / 2 / 3 m | sobreposição estimada 9,49 / 16,01 / 22,44 / 31,12 / 40,26% (verdade ≈ 8%) |
| Filtro de precisão ≤ 0,10 m | descarta 114 segmentos; única do SIM-03 cai de 17,17 para 15,91 ha |

A leitura prática está em [08](08_interpretacao_resultados.md).

## 7.4 Contabilidade de tempo e volume

- Para as quatro máquinas, Σ estados = intervalo total (`soma_h` = `span_h`), com `sem_dado_h = 0` (sem lacunas na frota exceto a perda de posição, que é "sem posição" mas com telemetria).
- Falha do SIM-02: **0,117 h** ≈ 7 min, igual ao evento injetado (10:10:30–10:17:30).
- Divergência vazão×contador: −0,05% a −0,06% nas máquinas sem lacunas de sensor; **−8,64%** no SIM-03 (dados faltantes) — a divergência cumpriu o papel de acusar o problema.
- Taxa realizada: 105–111 L/ha (a taxa-alvo do simulador está em `fleet.py`; a variação reflete velocidade/ruído e a área única ser menor que a bruta).

## 7.5 Detecção de anomalias (notebook 03)

- **Eventos injetados detectados:** 4/4 (`stop`, `gnss_loss`, `missing_data`, `abnormal_flow`), atraso de início 0,0 s. **Ressalvas:** (1) as regras foram escritas conhecendo o simulador, então é recall **otimista**; (2) o mapa `ACCEPT` aceita `pressure_outlier` como detecção de `abnormal_flow` (ver [06](06_algoritmos_modelos.md)); (3) "atraso 0 s" é o início do evento, a **emissão** só ocorre depois de `min_duration_s` (≥ 20 s).
- **Alertas sem evento correspondente:** 2 (`gnss_accuracy_low` e `gnss_degraded` do SIM-03, na reconvergência RTK-float). São consequência legítima da perda de GNSS, não injetada como evento próprio.
- **Um evento ≠ um alerta:** a falha de vazão do SIM-04 gerou 3 alertas `flow_deviation` (a persistência é interrompida entre passadas).
- **Varredura de tolerância** (sensor de vazão ruidoso, σ = 8%), por hora de trabalho:

| Tolerância | Persistência | Alertas | Recall por amostra | FP/h |
|---|---|---|---|---|
| 5% | 10 s / 20 s | 182 / 99 | 1,00 | 2 214 / 1 597 |
| 10% | 10 s / 20 s | 23 / 3 | 1,00 | 133 / 5 |
| 15–25% | 10 s / 20 s | 3 / 3 | 1,00 | 0 |
| 35% | 10 s / 20 s | 8 / 2 | 0,52 / 0,16 | 0 |
| 45% | — | 0 | 0,00 | 0 |

- **IsolationForest:** c = 0,01/0,02 → 0 alertas (não detecta); 0,05 → 2; 0,10 → 14; 0,15 → 30. A regra física gera 3, todos no SIM-04.

## 7.6 Validação cruzada geodésica (PostGIS real)

De `experiments/geodesy_check.json` (PostgreSQL 16 + PostGIS 3.4 em contêiner amd64 emulado):

| Máquina | Trilha PostGIS `geography` (m) | `pyproj.Geod` (m) | Métrica UTM do projeto (m)* |
|---|---|---|---|
| SIM-01 | 12 089,70 | 12 089,70 | 12 096,34 |
| SIM-02 | 13 784,19 | 13 784,19 | 13 791,73 |
| SIM-03 | 10 852,50 | 10 852,50 | 10 682,79 |
| SIM-04 | 15 792,47 | 15 792,47 | 15 801,11 |

\* A métrica do projeto **exclui saltos e lacunas por definição**; no SIM-03 ela é menor porque os 241 s sem posição não entram (as duas primeiras colunas incluem a reta sobre a perda). Nas outras 3 máquinas a diferença para a geodésica é ~0,05%.

- PostGIS × `pyproj.Geod`: diferença relativa ~1e-12 (concordância numérica).
- **Área do talhão:** UTM 200 000,0 m² vs. `geography` 199 780,4 m²: **+0,11%** de viés da projeção neste local.
- `ST_Within` conta 1 990 / 2 293 / 1 615 / 1 984 pontos dentro do talhão (SIM-01…04).

## 7.7 O que **não** foi validado

Telemetria real; dispositivos reais; ISOXML; API de fabricante; CAN/ISOBUS; Docker Desktop (só Podman); desempenho com volume grande (sem benchmark de carga); segurança por teste de intrusão; usabilidade com operadores. Ver [09](09_limitacoes.md).

## 7.8 Como validar com dados reais (plano)

1. Escolha um talhão pequeno e **meça a verdade-terreno** (área com GNSS geodésico ou levantamento) e a largura efetiva da barra (calibrada).
2. Importe um dia de telemetria (`origin="real"`); rode `assess` e **leia o relatório de qualidade antes** de qualquer métrica.
3. Compare área única estimada × área de referência; reporte o erro **com a distribuição** (várias máquinas/dias), não só a média.
4. Para anomalias: peça à equipe de manutenção para **rotular** eventos reais (falha de bico, sensor travado, parada) e meça precisão/sensibilidade **por tipo**, em validação **temporal** (treinar em dias anteriores, avaliar em posteriores — não embaralhar amostras de uma mesma passada).
5. Calibre a tolerância pela variabilidade do **seu** fluxômetro (ver varredura em 7.5), não pelo valor padrão.
