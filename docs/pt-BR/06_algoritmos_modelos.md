# 06 — Algoritmos e modelos

## 6.1 Projeção e vazão esperada (`agrofleet/geo.py`)

- `Projector(epsg)` converte (lon, lat) → (x, y) em metros (UTM do fuso do centróide). Toda a geometria de cobertura roda nesse plano.
- `transform_geom` reprojeta geometrias shapely com `shapely.transform` (a função antiga `shapely.ops.transform` gerava aviso de depreciação, e o projeto roda `pytest -W error::DeprecationWarning`).
- `expected_flow_lpm(R, v, w) = R · (v·3,6) · w / 600` (derivação em [02](02_fundamentos.md)).

## 6.2 Cobertura (`analytics/coverage.py`)

**Segmentos.** Para amostras consecutivas *i*, *i+1* da mesma máquina, o segmento só gera faixa se **todas** as condições valem (a razão de cada exclusão é *contada* em `excluded`):

1. ambos têm posição (`no_position`);
2. ambos estão em estado de trabalho (`not_working`);
3. `dt ≤ max_gap_s` (`gap`);
4. precisão reportada ≤ `max_accuracy_m`, se informado (`low_accuracy`);
5. há largura de trabalho (`no_width`);
6. velocidade ≥ `min_speed_ms` (`low_speed`);
7. se `require_flow_on`, vazão > 0 e sensor presente (`no_flow`).

**Geometria.** Para um segmento de (x₀,y₀) a (x₁,y₁), comprimento *L* e largura *w*, o retângulo é o segmento "engordado" lateralmente *w/2* para cada lado (extremidades retas).

```
bruta   = Σ L_s · w_s
única   = área( ⋃ retângulos_s )                  # shapely.unary_union
excesso = max(bruta − única, 0)
região≥2× = ⋃_{s<t, não adjacentes no tempo} (ret_s ∩ ret_t)
```

**Por que a "região ≥2×" ignora segmentos adjacentes no tempo:** dois retângulos consecutivos da *mesma* passada sempre se tocam (compartilham a ponta); contá-los como sobreposição inflaria a medida. Descartamos pares da mesma máquina com |Δt| ≤ 4× o dt mediano (`skip_s`).

**Cobertura do talhão.** Dado o polígono de um talhão: `coberta_no_talhão = união ∩ talhão`, `não_coberta = talhão − união`, `fora_do_talhão = união − talhão` (área tratada fora do limite, que pode ser deriva do GNSS, erro de limite ou aplicação indevida — o sistema não distingue).

**Complexidade.** `unary_union` sobre N retângulos é aproximadamente O(N log N); a 1 Hz, uma máquina em 1 h tem ~3 600 segmentos. Para 4 máquinas × 3 h (11 331 amostras) o cálculo roda em segundos. Para frotas e períodos muito maiores, a alternativa é rasterizar em grade (contagem de passagens por célula) ou usar `ST_Union` no banco.

**Limitações do modelo geométrico** (reais): largura efetiva é *dado de entrada*, não observação; sem modelo de jato, sobreposição de bicos, controle de seção ou deriva; ruído de posição distorce (ver [08](08_interpretacao_resultados.md)).

## 6.3 Métricas operacionais (`analytics/metrics.py`)

**Tempo por estado.** Seja *dtᵢ* = tᵢ₊₁ − tᵢ, e `thr = max(max_gap_s, 3·mediana(dt))`. Se 0 < dtᵢ ≤ thr, o intervalo soma ao estado da amostra *i*; senão soma a `no_data`. Invariante testado: Σ estados = intervalo total.

**Distância.** Soma de trechos consecutivos com posição nos **dois** extremos, `dt ≤ thr`, e velocidade implícita ≤ `max_jump_ms` (25 m/s). Trechos descartados por salto são contados (`position_jumps_discarded`). A distância em linha reta *sobre* perda de posição vai para `distance_across_gaps_km_straight_line` — **não é somada**, porque é uma subestimativa de um percurso desconhecido.

**Volumes.** Por integração da vazão (`Σ Q·dt/60`) e pelo contador `consumption_l` (resets contados). `volume_source_discrepancy_pct` compara os dois: no SIM-03 foi −8,64%, porque a integração perde o volume dos 2 min de dados faltantes enquanto o contador continua somando — exatamente o tipo de informação que a divergência deve revelar.

**Taxa realizada** (L/ha) = volume / área **única**. **Produtividade:** `unique_ha_per_working_h` (ha por hora em `working`) e `unique_ha_per_engaged_h` (ha por hora com a máquina ligada e engajada, incluindo deslocamento/parada/reabastecimento). **Custo/ha** = horas engajadas × custo horário / área (+ produto, se informado o preço por litro). Os custos são **premissas suas**, nunca valores padrão.

## 6.4 Anomalias (`analytics/anomalies.py`)

Todas as regras exigem **persistência** (`min_duration_s`, 20 s) e só avaliam amostras em que o sensor existe. Cada alerta é `info`/`warning` (o sistema não decide criticidade) e traz a nota informativa.

| Regra (`type`) | Condição | Observações |
|---|---|---|
| `flow_deviation` | em `working`, `|Q − Q_esp| / Q_esp > tol` (padrão 15%), com `Q_esp = R·v·w/600` | `R` = alvo configurado ou taxa reportada; precisa de largura e taxa |
| `pressure_out_of_band` / `pressure_outlier` | pressão fora de banda configurada, ou |z robusto (MAD)| > 6 em trabalho | mensagem lembra que a pressão é **global** |
| `speed_out_of_range` | fora da banda configurada, ou desvio > 35% da mediana de trabalho da própria máquina | |
| `unexpected_stop` | `state == working` com velocidade < 0,2 m/s | |
| `flow_while_stopped` | vazão > 0,5 L/min com velocidade < 0,2 m/s (≥ 10 s) | possível aplicação concentrada |
| `fault_stop` | `state == fault` ou `error_codes` não vazio | persistência 0 s: uma amostra basta |
| `gnss_loss` | sem posição / `fix == none` por ≥ `gnss_min_s` (10 s) | |
| `gnss_degraded`, `gnss_accuracy_low` | `fix` ≠ esperado (`rtk_fixed`), `accuracy_m` > 0,10 m | `expected_fix=None` desliga |
| `data_gap` | intervalo entre amostras > `max(30 s, 3× dt mediano)` | |
| `sensor_missing` / `sensor_flatline` | nulo durante o trabalho; leitura idêntica por ≥ 60 s com a máquina em movimento | só vazão e pressão; a regra leva `[coluna]` no nome |
| `ml_multivariate_outlier` | IsolationForest (abaixo) | `experimental=True` |

**Identidade do alerta:** `id = sha1(machine|type|rule|start)[:16]`. No banco, alertas da mesma máquina/tipo/regra que **se sobrepõem** (±15 s) são **fundidos** (`upsert_alert`), para um evento em andamento não virar vários alertas. Incluir `rule` na identidade corrigiu um bug real: `sensor_missing` de vazão e de pressão tinham o mesmo `id`.

**Por que não diagnosticamos bico entupido.** Entupimento reduz a vazão *local* de um bico. Um fluxômetro e um manômetro globais veem a soma. A pressão pode subir, cair ou não mudar conforme o controle da bomba/válvula. Logo, uma queda de vazão em relação ao esperado é indício de **problema na aplicação**, não de **qual** componente. Distinguir bico exige sensor por seção/bico, o que este sistema não assume.

**Armadilha de avaliação (importante).** `evaluate` considera um evento `abnormal_flow` **detectado** se QUALQUER um destes tipos o cobrir: `flow_deviation`, `pressure_outlier` ou `ml_multivariate_outlier` (mapa `ACCEPT`). Com tolerância de vazão de 45% a regra de vazão não dispara no SIM-04, mas o evento ainda conta como detectado, porque `pressure_outlier` o cobre (verificado: `by=['pressure_outlier']`). Ou seja, o "recall 1,0" do simulador **não prova** que a regra de vazão enxerga a falha; no simulador a pressão é função da vazão. Para avaliar uma regra isolada, filtre os alertas por tipo antes de chamar `evaluate`.

### IsolationForest (experimental)

Para o trabalho de cada máquina, usa-se um vetor de **taxa realizada (L/ha = Q·600/(v·3,6·w)), pressão, velocidade**, padronizado (média/desvio da própria máquina) e `IsolationForest(n_estimators=200, contamination=c, random_state=seed)`. Pontos marcados são agrupados em eventos sustentados e viram alertas `ml_multivariate_outlier`, `experimental=True`, `info`.

- **Atípico ≠ defeituoso.** O modelo mede raridade relativa *dentro da máquina*; uma máquina sem falhas também terá 100·c% de amostras "mais raras".
- **`contamination`** é um **orçamento de alarmes**. O notebook 03 mostra: c = 0,01/0,02 não detecta a falha de vazão; c = 0,05 detecta, mas já alarma em máquina sem falha; c = 0,10/0,15 alarma em todas (14 e 30 alertas para uma única falha real, contra 3 da regra física).
- **Requisitos:** vazão, pressão, velocidade e largura presentes; ≥ 300 amostras de trabalho. Caso contrário, é pulado com o motivo.
- **Não roda no streaming** (só regras); roda sob demanda (`POST /alerts/recompute?with_ml=true`).
- *Alternativas não implementadas:* resíduo de um modelo físico/regressão (taxa realizada vs. esperada), detecção de mudança (CUSUM/Page-Hinkley), autoencoders temporais — todos exigem dados reais com rótulos para calibrar.

## 6.5 Qualidade de dados (`agrofleet/quality.py`)

Detecta duplicatas por `(machine_id, ts)`, fora de ordem, lacunas > 30 s, saltos de posição (> 40 m/s implícitos), unidades suspeitas (velocidade > 25 m/s, pressão mediana > 15 "bar", vazão mediana > 1500 L/min, largura > 60 m), reset de contador e relógios absurdos (> 5 min no futuro, antes de 2000). **Não corrige**; sinaliza. O veredito `blocking` aparece quando não há linhas válidas.

## 6.6 ROI (`analytics/roi.py`)

Entradas (`RoiInputs`) e **proveniência** de cada uma (`assumed`/`user`/`measured_telemetry`). O cenário `EXAMPLE_INPUTS` é **ilustrativo** (`is_example=True` em toda saída).

```
fixo_máquina/ano = (capex − residual)/vida + juros·(capex + residual)/2 + manutenção%·capex
horas/ano        = (área_ano · aplicações) / produtividade_autônoma
custo_autônomo/ha = [n·fixo_máquina + (energia + supervisão)·horas] / área_tratada + produto/ha·(1 + sobrep_auto)
custo_base/ha     = custo_horário_base / produtividade_base + produto/ha·(1 + sobrep_base)
benefício/ano     = (custo_base − custo_autônomo)/ha · área_tratada
payback (anos)    = capex_total / benefício/ano                    (indefinido se benefício ≤ 0)
VPL               = −capex_total + Σ_{t=1..vida} (benefício + residual·n em t=vida)/(1+r)^t
```

**Sensibilidade.** *Tornado*: cada premissa ±20%, uma por vez, ordenadas por amplitude (`sensitivity`). *Grade 2-D* de duas premissas (`grid`). *Ponto de equilíbrio* por **bissecção** do benefício=0 (`break_even`), `None` se não houver troca de sinal no intervalo.

**Limites:** não inclui impostos, financiamento, custo de falha/parada, ganho agronômico, risco regulatório. Resultado depende **integralmente** das premissas — o valor do módulo é tornar as premissas explícitas e mostrar quais mexem no número, não "provar" retorno.

## 6.7 Serviço ao vivo (`backend/app/live.py`)

Thread MQTT → `queue.Queue` (200 000) → laço assíncrono a cada 0,5 s drena até 5 000 pontos → `insert_points` (idempotente) → para cada máquina presente no lote, carrega uma janela (padrão 600 s) e roda `detect` **sem ML** → `upsert_alert` → broadcast WebSocket (`telemetry`, `alert`). Exceções no lote são registradas em `stats.last_error` e **não derrubam o laço**. Fila cheia incrementa `dropped_queue_full` (visível em `/health`).

## 6.8 Autenticação (`backend/app/auth.py`)

JWT HS256 (`sub`, `role`, `iat`, `exp`), três papéis ordenados, senhas com **scrypt** (`hashlib.scrypt`, n=2¹⁴, r=8, p=1, salt aleatório de 16 bytes por senha, comparação em tempo constante). Detalhes de segurança e do que falta: [04](04_arquitetura.md) e [09](09_limitacoes.md).
