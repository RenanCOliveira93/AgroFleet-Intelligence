# 10 — Exercícios graduados

Dificuldade: ★ básico · ★★ intermediário · ★★★ avançado · ★★★★ desafio · ★★★★★ projeto aberto.

**Como usar:** tente resolver *antes* de abrir a solução. Rode da raiz do projeto com `export PYTHONPATH=.`. As soluções (blocos precedidos por `<!-- run -->`) são **executadas pelo teste** `tests/test_docs_exercises.py`: se o código ou os números do projeto mudarem, o teste falha e este capítulo precisa ser atualizado. Os valores esperados foram obtidos executando o código (semente 7).

Dados comuns aos exercícios com simulador:

```python
from agrofleet.simulator.fleet import simulate
from agrofleet.analytics.frame import to_frame
pts, truth = simulate(seed=7, max_seconds=3 * 3600)    # 11 331 amostras SIMULADAS, 4 máquinas
df = to_frame(pts)
```

---

## Exercício 1 ★ — Vazão esperada e a armadilha da unidade

Um pulverizador deve aplicar 100 L/ha com barra de 24 m a 5,0 m/s. (a) Qual a vazão esperada em L/min? (b) Se alguém registrar a velocidade em km/h (18) mas o sistema a ler como m/s, qual vazão "esperada" sai e por que isso geraria alertas falsos de `flow_deviation`?

**Solução comentada**

<!-- run -->
```python
from agrofleet.geo import expected_flow_lpm

# (a) L/min = R * (km/h) * w / 600; a função recebe a velocidade em m/s e converte.
q = expected_flow_lpm(100, 5.0, 24)
assert abs(q - 72.0) < 1e-9

# (b) 18 lido como 18 m/s equivale a 64,8 km/h: a vazão esperada fica 3,6x maior.
q_errado = expected_flow_lpm(100, 18.0, 24)
assert abs(q_errado - 259.2) < 1e-9
# A vazão medida (~72) ficaria ~72% abaixo do "esperado" (259,2): estouraria a tolerância de 15%
# o tempo todo. Moral: unidade errada vira alarme sistemático, por isso o adaptador exige
# unidade explícita (ou deduz só pelo NOME da coluna, com aviso) e o quality.assess tem 'unit_suspect'.
print(q, q_errado, 1 - q / q_errado)
```

---

## Exercício 2 ★ — Ingestão defensiva

Leia `payload_examples/telemetry_rejected_rows.csv` como telemetria **real**. (a) Quantas linhas entram e por que as demais são rejeitadas? (b) O que muda se você declarar `assume_tz="UTC"`? (c) A linha com vazão vazia entra? Com que valor?

**Solução comentada**

<!-- run -->
```python
from collections import Counter
from agrofleet.adapters import read_csv

raw = open("payload_examples/telemetry_rejected_rows.csv", "rb").read()
r = read_csv(raw, origin="real")
assert len(r.points) == 2 and r.n_rejected_total == 4          # (a)
motivos = Counter(x["reason"].split(":")[0][:40] for x in r.rejected)
print(motivos)   # sem fuso | (0,0) | vazão negativa | lat sem lon: cada um com a linha de origem

r2 = read_csv(raw, origin="real", assume_tz="UTC")             # (b) a linha sem fuso passa a ser aceita
assert len(r2.points) == 3
# ATENÇÃO: assume_tz transfere a responsabilidade para você. Se o equipamento gravava em horário local,
# você acabou de deslocar todos os timestamps em silêncio. Só declare o fuso se conhecer a fonte.

assert r.points[1].flow_lpm is None                            # (c) vazio vira None, NUNCA 0
```

---

## Exercício 3 ★★ — Sobreposição analítica (sem usar o projeto)

Duas passadas retas de 495 m, barra de 24 m, eixos espaçados 22 m. Usando só `shapely`, calcule área bruta, única, excesso e região de sobreposição. Confira com a fórmula fechada.

**Solução comentada**

<!-- run -->
```python
from shapely.geometry import box
from shapely.ops import unary_union

L, w, s = 495.0, 24.0, 22.0
r1 = box(0, 0, w, L)            # retângulo da 1ª passada (x de 0 a w)
r2 = box(s, 0, s + w, L)        # 2ª passada deslocada de 's' no eixo x

bruta = r1.area + r2.area                       # soma dos retângulos = 2*L*w
unica = unary_union([r1, r2]).area               # união
excesso = bruta - unica
regiao = r1.intersection(r2).area                # onde as duas cobrem

assert abs(bruta - 2 * L * w) < 1e-6             # 23 760
assert abs(regiao - (w - s) * L) < 1e-6          # 990
assert abs(unica - (L * (s + w))) < 1e-6         # 22 770
assert abs(excesso - regiao) < 1e-6              # com 2 faixas, excesso == região de 2x
# Com 3 ou mais faixas, excesso != região >=2x (uma região coberta 3x contribui 2x a área ao excesso).
# É por isso que o projeto reporta as duas medidas separadamente.
print(bruta, unica, excesso, regiao)
```

---

## Exercício 4 ★★ — Contabilidade de tempo do SIM-02

(a) Quanto tempo o SIM-02 ficou em falha? (b) Prove que a soma dos tempos por estado fecha com o intervalo total. (c) O que aconteceria com `no_data` se você apagasse 60 s de amostras no meio do trabalho?

**Solução comentada**

<!-- run -->
```python
from agrofleet.analytics.metrics import machine_metrics
from agrofleet.simulator.fleet import simulate
from agrofleet.analytics.frame import to_frame

df = to_frame(simulate(seed=7, max_seconds=3 * 3600)[0])
g = df[df.machine_id == "SIM-02"].sort_values("ts").reset_index(drop=True)
m = machine_metrics(g)

assert abs(m["time_h"]["fault"] - 7 / 60) < 0.01           # (a) ~0,117 h = 7 min, como injetado
assert abs(sum(m["time_h"].values()) - m["span_h"]) < 1e-9  # (b) invariante: nada se perde nem se inventa

# (c) removemos 60 s no meio do trabalho: o intervalo entre as amostras vizinhas (~61 s) excede o limite,
# então esse intervalo inteiro vai para no_data (não assumimos o que a máquina fez).
t0 = g.loc[len(g) // 3, "ts"]
g2 = g[~((g.ts > t0) & (g.ts <= t0 + __import__("pandas").Timedelta(seconds=60)))]
m2 = machine_metrics(g2)
assert m2["time_h"]["no_data"] > 0.015                      # ~60 s = 0,0167 h
assert abs(sum(m2["time_h"].values()) - m2["span_h"]) < 1e-9  # a soma continua fechando
print(m["time_h"]["fault"], m2["time_h"]["no_data"])
```

---

## Exercício 5 ★★ — Quanto a largura errada engana a sobreposição?

Para o SIM-01, reduza `working_width_m` em 5% e recalcule. Compare bruta, única e sobreposição % com o caso base. Qual das três é mais sensível e por quê?

**Solução comentada**

<!-- run -->
```python
from agrofleet.analytics.coverage import coverage
from agrofleet.simulator.fleet import simulate
from agrofleet.analytics.frame import to_frame

df = to_frame(simulate(seed=7, max_seconds=3 * 3600)[0])
s1 = df[df.machine_id == "SIM-01"]

def cov(fator):
    d = s1.copy()
    d["working_width_m"] = d["working_width_m"] * fator
    c = coverage(d, with_geojson=False)
    return c.gross_m2 / 1e4, c.unique_m2 / 1e4, 100 * c.excess_m2 / c.gross_m2

base, menos5 = cov(1.0), cov(0.95)
# bruta cai ~5% (proporcional à largura); única muda < 1% (a terra tocada é quase a mesma);
# sobreposição % despenca (de ~8,1% para ~3,7%) porque depende da RAZÃO largura/espaçamento.
assert abs(menos5[0] / base[0] - 0.95) < 0.01
assert abs(menos5[1] / base[1] - 1) < 0.01
assert 3.3 < menos5[2] < 4.0 and 7.8 < base[2] < 8.4
print(base, menos5)
```

---

## Exercício 6 ★★★ — Tolerância, falsos alarmes e a armadilha do `evaluate`

No SIM-04 (falha de vazão), rode `detect` com `flow_tol_pct` = 15 e 45, sem ML. (a) Quantos alertas `flow_deviation` em cada caso? (b) O `evaluate` diz que a falha foi detectada com 45%. Isso significa que a regra de vazão a enxergou? Como medir a regra **isolada**?

**Solução comentada**

<!-- run -->
```python
from agrofleet.analytics import anomalies as A
from agrofleet.simulator.fleet import simulate
from agrofleet.analytics.frame import to_frame

pts, truth = simulate(seed=7, max_seconds=3 * 3600)
df = to_frame(pts)
s4 = df[df.machine_id == "SIM-04"]
ev = [e for e in truth.events if e["machine_id"] == "SIM-04"]

def rodar(tol):
    res = A.detect(s4, A.AnomalyConfig(flow_tol_pct=tol, use_iforest=False))
    so_vazao = [a for a in res.alerts if a.type == "flow_deviation"]
    return res.alerts, so_vazao

todos15, vazao15 = rodar(15)
todos45, vazao45 = rodar(45)
assert len(vazao15) == 3 and len(vazao45) == 0              # (a)

# (b) 'evaluate' aceita pressure_outlier como detecção de abnormal_flow (mapa ACCEPT). Com 45% a regra de
# vazão está cega, mas o evento ainda conta como detectado pela pressão -> recall 1.0 ENGANOSO.
assert A.evaluate(todos45, ev)["recall"] == 1.0
# Avalie a regra isolada passando SÓ os alertas dela:
assert A.evaluate(vazao45, ev)["recall"] == 0.0
assert A.evaluate(vazao15, ev)["recall"] == 1.0
# Lição: métrica de detecção depende de o que você considera 'acerto'. No simulador a pressão é função da
# vazão; em máquina real essa relação não existe nessa forma e o atalho desapareceria.
print(len(vazao15), len(vazao45))
```

---

## Exercício 7 ★★★ — ROI: onde está a alavanca?

Para o cenário **ilustrativo** `EXAMPLE_INPUTS`: (a) qual a área anual de equilíbrio (benefício = 0)? (b) Qual a produtividade autônoma de equilíbrio? (c) Qual das duas é plausível alcançar e por que isso importa para a decisão?

**Solução comentada**

<!-- run -->
```python
from agrofleet.analytics import roi as R

I0 = R.EXAMPLE_INPUTS
assert I0.is_example                                          # cenário ILUSTRATIVO, não benchmark
area_eq = R.break_even(I0, "area_ha_per_year", 1_000, 20_000)
prod_eq = R.break_even(I0, "auto_productivity_ha_per_h", 5, 200)
assert abs(area_eq - 6974.17) < 0.5 and abs(prod_eq - 66.67) < 0.1

# (a) 6 974 ha/ano = +16% sobre os 6 000 ha do cenário: uma mudança modesta de UTILIZAÇÃO.
# (b) 66,7 ha/h = 2,2x os 30 ha/h assumidos: exigiria outra máquina, não um ajuste.
# (c) Em ambos os casos o ponto é qual premissa você controla. Aqui, utilização da frota e preço da
# máquina (capex) pesam mais que a produtividade (veja o tornado no notebook 03). Os valores são do
# cenário FICTÍCIO: refaça com SEUS números antes de concluir qualquer coisa.
print(area_eq, prod_eq)
```

---

## Exercício 8 ★★★★ — Ruído de GNSS e sobreposição fantasma

Adicione ruído gaussiano (σ = 0,5 m e 2 m, semente 0) às posições do SIM-01 e recalcule bruta, única e sobreposição %. (a) O que acontece com cada uma? (b) Por que a sobreposição *estimada* sobe se a máquina não mudou? (c) Proponha e justifique uma mitigação.

**Solução comentada**

<!-- run -->
```python
import numpy as np
from agrofleet.analytics.coverage import coverage
from agrofleet.simulator.fleet import simulate
from agrofleet.analytics.frame import to_frame

df = to_frame(simulate(seed=7, max_seconds=3 * 3600)[0])
s1 = df[df.machine_id == "SIM-01"]

def com_ruido(sigma_m, seed=0):
    d = s1.copy()
    rng = np.random.default_rng(seed)
    pos = d.lat.notna().to_numpy()
    lat0 = float(d.lat.dropna().iloc[0])
    d.loc[pos, "lat"] = d.loc[pos, "lat"] + rng.normal(0, sigma_m, pos.sum()) / 111_320.0
    d.loc[pos, "lon"] = d.loc[pos, "lon"] + rng.normal(0, sigma_m, pos.sum()) / (111_320.0 * np.cos(np.radians(lat0)))
    c = coverage(d, with_geojson=False)
    return c.gross_m2 / 1e4, c.unique_m2 / 1e4, 100 * c.excess_m2 / c.gross_m2

b0, b05, b2 = com_ruido(0.0), com_ruido(0.5), com_ruido(2.0)
# (a) bruta SOBE e única DESCE; sobreposição % dispara (≈8% -> ≈16% -> ≈31%)
assert b05[0] > b0[0] and b2[0] > b05[0]
assert b05[1] < b0[1] and b2[1] < b0[1]
assert b05[2] > 12 and b2[2] > 25
# (b) Comprimento do caminho = soma de passos entre posições ruidosas a 1 Hz (passos de ~5 m): o jitter
# soma caminho que não existe (bruta sobe). Retângulos levemente girados deixam frestas entre passadas
# (única desce). Logo, excesso = bruta - única sobe sem a máquina ter sobreposto mais.
# (c) Mitigações (não implementadas): suavizar a trajetória (Kalman / Savitzky-Golay) antes de gerar os
# segmentos; ou usar velocidade reportada x dt para o COMPRIMENTO e a posição só para a união; ou
# recusar a métrica de sobreposição quando a precisão reportada for pior que o limite.
print(b0, b05, b2)
```

---

## Exercício 9 ★★★★ — Escreva uma regra nova: "ociosidade prolongada"

Implemente `idle_runs(df, min_s)` que devolve os intervalos (início, fim) em que uma máquina ficou em `idle` por pelo menos `min_s` segundos contínuos. Requisitos: não considerar lacunas de dados como ociosidade; devolver lista vazia se não houver coluna de estado. Teste em um quadro sintético.

**Solução comentada**

<!-- run -->
```python
import numpy as np
import pandas as pd

def idle_runs(df, min_s, max_gap_s=10.0):
    """Intervalos de `idle` contínuo >= min_s. Uma lacuna (dt > max_gap_s) QUEBRA o intervalo:
    não sabemos o que a máquina fez durante ela, então não contamos como ociosidade."""
    if "state" not in df.columns or df.empty:
        return []
    g = df.sort_values("ts").reset_index(drop=True)
    # segundos desde a 1ª amostra; NÃO use astype("int64")/1e9: a resolução do datetime (s, us, ns) varia entre versões do pandas
    t = (g["ts"] - g["ts"].iloc[0]).dt.total_seconds().to_numpy()
    idle = (g["state"] == "idle").to_numpy()
    out, start = [], None
    for i in range(len(g)):
        gap = i > 0 and (t[i] - t[i - 1]) > max_gap_s
        if start is not None and (not idle[i] or gap):
            end = i - 1
            if t[end] - t[start] >= min_s:
                out.append((g["ts"].iloc[start], g["ts"].iloc[end]))
            start = None
        if idle[i] and start is None:
            start = i
    if start is not None and t[len(g) - 1] - t[start] >= min_s:
        out.append((g["ts"].iloc[start], g["ts"].iloc[len(g) - 1]))
    return out

ts = pd.date_range("2026-01-01", periods=400, freq="s", tz="UTC")
estado = np.array(["working"] * 400, dtype=object)
estado[50:200] = "idle"          # 150 s de ociosidade contínua -> deve ser detectada
estado[300:330] = "idle"         # 30 s -> abaixo do mínimo
df = pd.DataFrame({"ts": ts, "state": estado})

r = idle_runs(df, min_s=60)
assert len(r) == 1 and (r[0][1] - r[0][0]).total_seconds() == 149

# Uma lacuna (dt > 10 s) no meio da ociosidade deve QUEBRAR o intervalo: removemos 50 s (linhas 100..149)
# de dentro dos 150 s ociosos. Sobram dois trechos de 49 s, e NÃO um trecho de 150 s.
df_gap = df.drop(index=range(100, 150)).reset_index(drop=True)
assert idle_runs(df_gap, min_s=60) == []            # nenhum trecho contínuo chega a 60 s
r2 = idle_runs(df_gap, min_s=40)
assert len(r2) == 2                                  # dois trechos de 49 s, não um de ~150 s
print(r, r2)
assert idle_runs(df.drop(columns="state"), 60) == []
```

*Pergunta de acompanhamento:* por que não basta `(df.state == "idle").sum()`? (Resposta: isso soma tempo disperso, sem continuidade, e conta lacunas como ociosidade se a lacuna cair entre duas amostras `idle`.)

---

## Exercício 10 ★★★★★ — Projeto aberto: validar com dados reais

Você recebeu um dia de telemetria CSV de um pulverizador real, mais o limite do talhão em GeoJSON e uma planilha de ocorrências de manutenção. Elabore (por escrito, com os comandos do projeto) o protocolo para decidir se o sistema é confiável para essa máquina. Entregue: (1) checagens de qualidade e critérios de bloqueio; (2) como obter e conferir a largura efetiva; (3) como estimar o erro de área contra uma referência; (4) como avaliar as regras de anomalia e o IsolationForest com rótulos de manutenção; (5) o que você **recusaria** concluir mesmo se os números fossem bons.

**Gabarito comentado (critérios de correção, não código)**

1. *Qualidade primeiro:* `read_csv(..., origin="real", units={...}, assume_tz=<confirmado com o fornecedor>)`; `assess()`; bloqueie se `blocking`, ou se `unit_suspect`/`position_jumps`/duplicatas relevantes não forem explicados. Registre a taxa de rejeição por motivo — rejeições concentradas em um estado ou horário enviesam tudo adiante.
2. *Largura:* medir a barra com bicos ativos e a sobreposição de jato; comparar com `working_width_m` reportado; rodar a sensibilidade de largura (Exercício 5) para mostrar quanto a sobreposição muda por ±5%.
3. *Área:* referência independente (levantamento do talhão ou área de aplicação confirmada). Reportar erro com intervalo por dia/máquina, **incluindo** os trechos com perda de GNSS (área não interpolada).
4. *Anomalias:* rótulos de manutenção com horário; separar período de calibração (limiar pelo ruído do fluxômetro, como no notebook 03) e período de avaliação **posterior no tempo**; medir precisão e sensibilidade **por tipo**; avaliar cada regra isolada (Exercício 6); tratar IsolationForest como triagem e relatar o número de alarmes por hora de trabalho.
5. *Recusar:* diagnóstico de bico a partir de pressão global; afirmações de economia de insumo sem medição de dose; qualquer conclusão de segurança; generalizar para outras máquinas/operações com um único dia.

Critério de qualidade da resposta: cada conclusão aparece ligada à evidência que a sustenta e à ressalva correspondente.
