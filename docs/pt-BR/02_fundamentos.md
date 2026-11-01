# 02 — Fundamentos do domínio

Este capítulo dá o vocabulário mínimo para ler o código e os resultados. Onde um número é uma ordem de grandeza típica (e não algo que medimos aqui), isso está dito.

## 2.1 Pulverizador autônomo: o que há de relevante para telemetria

Um pulverizador terrestre autoguiado combina: (a) receptor **GNSS** com correção (normalmente RTK) para posicionar a máquina; (b) **controlador de taxa** que ajusta a vazão à velocidade para manter uma taxa-alvo em L/ha; (c) **barra** com largura de trabalho *w* e, em geral, controle de seções; (d) sensores de **vazão**, **pressão** (global) e **nível do tanque**; (e) um estado operacional (aplicando, deslocando, parada, reabastecendo, falha).

A telemetria é o que esse conjunto *reporta*. Dois pontos que um engenheiro de ML precisa internalizar:

- **O dado é o relato do sensor, não a verdade.** Um fluxômetro descalibrado reporta com confiança o valor errado. Por isso o schema chama `flow_lpm` de "o que a máquina reportou" e traz `sensor_quality` por sensor.
- **Há sensores que não existem.** Máquinas diferentes expõem conjuntos diferentes. As regras de anomalia só rodam quando o sensor existe (cobertura de não-nulos ≥ 20%, `min_sensor_coverage`) e, quando são puladas, o motivo é devolvido (`regras puladas`).

## 2.2 GNSS e RTK

GNSS (GPS, GLONASS, Galileo, BeiDou…) calcula posição por pseudodistâncias a satélites. Ordens de grandeza típicas de erro horizontal (variam com ambiente, receptor e correção):

| Modo (`gnss_fix`) | Ordem de grandeza | Observação |
|---|---|---|
| `single` (autônomo) | metros | sem correção |
| `dgps` (SBAS/DGPS) | decímetros a ~1 m | correção diferencial |
| `rtk_float` | decímetros | ambiguidade de fase ainda não fixada |
| `rtk_fixed` | centímetros | ambiguidade fixada; é o que a agricultura de precisão espera |
| `none` | — | sem posição |

**Por que isso importa para a geometria:** a área coberta é estimada a partir da trajetória; o ruído de posição se propaga para a sobreposição de forma **não óbvia** (infla a área bruta e reduz a única — ver [08](08_interpretacao_resultados.md) e o notebook 02, seção 3c). Com RTK (cm) os números são utilizáveis; com GNSS de código (1–3 m) a sobreposição estimada por esta técnica deixa de ser confiável.

Um receptor pode reportar `accuracy_m` (precisão horizontal estimada por ele). Usamos esse campo como **filtro opcional** (`max_accuracy_m`) e *contamos* o que foi descartado; não o tratamos como erro verdadeiro.

**Pegadinha clássica:** receptores sem fix frequentemente emitem `(0, 0)`. O schema rejeita essa coordenada exatamente por isso.

## 2.3 Coordenadas, projeção e geodésia

Latitude/longitude estão sobre o elipsoide WGS-84; áreas e comprimentos em metros precisam de uma métrica. Usamos três ferramentas, cada uma com um papel:

1. **UTM (projeção transversa de Mercator, fuso escolhido pela posição)** para toda a geometria de faixas (`Projector`, `agrofleet/geo.py`). É plana, rápida e boa em áreas pequenas, mas **não preserva áreas exatamente**: neste local a área de talhão em UTM difere da geodésica em **+0,11%** (medido, `experiments/geodesy_check.json`).
2. **Haversine** (esfera de raio 6 371 008,8 m) para verificações rápidas. Difere da distância elipsoidal em torno de 0,5% neste local — por isso o teste de referência usa `pyproj.Geod`, não haversine.
3. **Geodésia no elipsoide** (`pyproj.Geod`, `PostGIS geography`) como **referência**. Validamos que PostGIS e `pyproj.Geod` concordam (diferença relativa ~1e-12 no comprimento de trilha) e quantificamos o desvio do cálculo em UTM.

Regra prática: *projete para calcular geometria; use geodésia para auditar.*

## 2.4 Geometria de cobertura (a ideia central do projeto)

Cada par de pontos consecutivos, com a máquina **aplicando** nos dois extremos, define um segmento de comprimento *L*. A barra de largura *w* varre um **retângulo** de área *L·w* ao redor do segmento.

Três áreas, com significados diferentes:

- **Bruta** = Σ *L·w* — "o que a barra varreu", contando repetições.
- **Única** = área da **união** dos retângulos — "quanta terra foi tocada".
- **Excesso** = bruta − única — a área varrida mais de uma vez (em valor).

E a **região ≥2×**, a interseção geométrica onde ≥2 passadas se sobrepõem (no cálculo ignoram-se segmentos adjacentes *no tempo* da mesma passada, que sempre se tocam nas pontas).

Exemplo analítico verificável (teste e notebook 02): duas passadas de 495 m com barra de 24 m espaçadas 22 m → bruta = 2·495·24 = **23 760 m²**; sobreposição = 2 m × 495 m = 990 m²; única = **22 770 m²**. O código obtém 23 760 / 980 / 22 780 (diferença de ~10 m², cerca de 0,05% da área única, atribuída a efeitos de borda da discretização a 1 Hz; não investigamos além disso).

Por que **ambos** os extremos precisam estar em `working`: se só o primeiro estivesse, um segmento de cabeceira (parte aplicando, parte manobrando) geraria uma faixa espúria. Este foi um erro real encontrado e corrigido durante o desenvolvimento.

## 2.5 Vazão esperada: de onde vem a fórmula

Taxa-alvo *R* (L/ha), velocidade *v* (m/s), largura *w* (m). Área tratada por segundo = *v·w* m² = *v·w*/10⁴ ha. Vazão por segundo = *R·v·w*/10⁴ L/s; ×60 para L/min:

```
Q [L/min] = R · v · w · 60 / 10⁴ = R · v · w · 0,006        (v em m/s)
          = R · (km/h) · w / 600                           (v em km/h, porque 1 m/s = 3,6 km/h)
```

Exemplo: 100 L/ha, 18 km/h (5 m/s), 24 m → 100·18·24/600 = **72 L/min** — o mesmo valor do exemplo `payload_examples/telemetry_minimal.csv`. Esta é a relação que a regra `flow_deviation` verifica. Observe a armadilha de unidade: confundir 18 km/h com 18 m/s dá uma vazão esperada 3,6× maior.

## 2.6 Séries temporais de telemetria

Propriedades que quebram suposições habituais de ML:

- **Amostragem irregular e lacunas.** A taxa nominal pode ser 1 Hz, mas há quedas de rede e de sensor. Não reamostramos nem interpolamos: o intervalo *dt* acima do limite (`max(10 s, 3× dt mediano)`) é `no_data`.
- **Eventos duram segundos a minutos e são raros.** Persistência mínima (`min_duration_s`, 20 s) evita alarmes de uma amostra.
- **Contadores acumulados** (`consumption_l`) podem **zerar**; tratamos como reset e reportamos (`counter_resets`), nunca como consumo negativo.
- **Estados não são independentes.** Vazão só é comparável à esperada em `working`; pressão em transporte não entra nas estatísticas de trabalho.
- **Relógio.** Tudo em UTC com fuso explícito; timestamps no futuro ou muito antigos viram *flags* de qualidade.

## 2.7 ISOBUS e ISOXML (para saber o que **não** estamos lendo)

- **ISOBUS (ISO 11783)** é a família de normas para comunicação entre trator, implementos e terminais, sobre barramento **CAN**. Define, entre outras coisas, o *task controller* e um dicionário de dados.
- **ISO 11783-10** especifica o intercâmbio de dados de tarefa/gestão em **XML** (`TASKDATA.XML`), e os **time logs** (`TLG*.XML` + `TLG*.BIN`) registram séries temporais binárias cujos campos são identificados por **DDI** (*Data Dictionary Identifier*, dicionário da ISO 11783-11).
- **Status neste projeto: NÃO IMPLEMENTADO.** Não havia amostra TASKDATA/TLG válida de terminal real e nenhum parser foi avaliado e validado; escrever um sem amostra produziria código que parece funcionar sem estar validado. O adaptador (`agrofleet/adapters/isoxml.py`) **recusa** com o motivo, e o registro `/limits` o lista como `not_implemented`. O caminho para implementar está em [12](12_proximos_passos.md).
- **CAN/ISOBUS bruto** (leitura direta do barramento, hardware) também não foi usado nem testado.

## 2.8 Alertas, falsos positivos e o "orçamento de alarmes"

Um alerta é útil se um humano age sobre ele. Cada falso alarme consome atenção. Em detecção por limiar, a tolerância define um ponto numa curva precisão×sensibilidade. Em detecção não supervisionada (IsolationForest), `contamination` é, na prática, **a fração de amostras que você aceita marcar** — um orçamento de alarmes, não uma probabilidade de falha. O notebook 03 demonstra os dois efeitos com números reais do simulador.

## 2.9 Leituras recomendadas (verificáveis)

- ISO 11783 (ISOBUS), em particular as partes 10 e 11 (comprar/consultar a norma; não reproduzimos conteúdo normativo).
- Snyder, J. P. *Map Projections — A Working Manual*, USGS Professional Paper 1395 (1987) — projeção transversa de Mercator/UTM.
- Karney, C. F. F. "Algorithms for geodesics", *Journal of Geodesy* 87 (2013) — base do `GeographicLib`/`pyproj.Geod`.
- Liu, F. T.; Ting, K. M.; Zhou, Z.-H. "Isolation Forest", *IEEE ICDM* (2008).
- RFC 7946 (GeoJSON); GPX 1.1 (Topografix); documentação do PostGIS (`geography`, `ST_Within`, `ST_Area`).
