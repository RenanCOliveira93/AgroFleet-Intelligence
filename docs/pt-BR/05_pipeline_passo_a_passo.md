# 05 — Pipeline passo a passo

Cada passo diz **o que faz, por que, alternativas e como verificar**. Todos os comandos partem da raiz `06-AgroFleet-Intelligence/`.

## Passo 0 — Ambiente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=.                # o pacote `agrofleet` está na raiz (sem setup.py/pyproject)
```

*Verificar:* `python -c "import agrofleet.schema as s; print(s.SCHEMA_VERSION)"` → `1.0`.

*Alternativa:* instalar como pacote editável (`pip install -e .`) exigiria um `pyproject.toml`, que não existe nesta entrega; usamos `PYTHONPATH` por simplicidade.

## Passo 1 — Gerar dados simulados reprodutíveis

```bash
python simulator/generate.py --seed 7 --out sample_data
```

Gera `sim_fleet.csv`, `sim_fleet.ndjson` (um envelope MQTT por linha), `sim_fields.geojson` (limites dos talhões) e `sim_truth.json` (eventos injetados e áreas planejadas).

*Por quê:* para medir erro precisamos de **verdade-terreno**. *Alternativa:* dados reais — mas sem rótulos você mede plausibilidade, não erro. *Verificar:* rodar duas vezes com a mesma semente e comparar (o teste `determinístico` do notebook 01 faz isso: `True`).

## Passo 2 — Ingestão e validação

```python
from agrofleet.adapters import read_csv
from agrofleet.quality import assess

res = read_csv(open("payload_examples/telemetry_rejected_rows.csv", "rb").read(), origin="real")
print(len(res.points), res.n_rejected_total)          # 2 4
for r in res.rejected: print(r["row"], r["reason"])
print(assess(res)["verdict"])
```

Saída esperada (coberta por `tests/test_payload_examples.py`): 2 válidas, 4 rejeitadas — sem fuso, `(0,0)`, vazão negativa, `lat` sem `lon`. A linha com vazão vazia **entra** com `flow_lpm = None`. Com `assume_tz="UTC"` a linha sem fuso passa a ser aceita (3 válidas).

*Verificar:* `assess` devolve `flags` e `verdict` (`ok`/`warnings`/`blocking`). *Por que antes de analisar:* métricas calculadas sobre arquivo com duplicatas ou saltos parecem plausíveis e estão erradas.

## Passo 3 — Armazenar (idempotente)

Via API (`POST /uploads`, papel `analyst`) ou `insert_points(session, pontos)` (`backend/app/db.py`). A restrição `(machine_id, ts, origin)` garante que reenviar o mesmo arquivo não duplica. Em PostgreSQL a coluna `geom` (PostGIS) é **gerada** a partir de `lat/lon`, com índice GIST.

*Verificar:* enviar o mesmo arquivo duas vezes; o segundo envio deve reportar `stored: 0` e `duplicates_ignored` igual ao total (`test_upload_validacao_commit_e_idempotencia`).

## Passo 4 — Contabilidade de tempo e distância

```python
from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.metrics import machine_metrics
from agrofleet.simulator.fleet import simulate

pts, truth = simulate(seed=7, max_seconds=3 * 3600)
df = to_frame(pts)
m = machine_metrics(df[df.machine_id == "SIM-02"])
print(m["time_h"]["fault"], m["span_h"], sum(m["time_h"].values()))
```

Cada intervalo [tᵢ, tᵢ₊₁] pertence ao **estado da amostra i**, se `dt` ≤ limite (`max(10 s, 3× dt mediano)`); se não, vai inteiro para `no_data`. A soma por estado **tem de fechar** com o intervalo total (`soma_h`). No SIM-02: falha = 0,117 h (7 min, como injetado), soma = 0,899 h = intervalo.

*Alternativa:* interpolar/reamostrar — descartada, pois inventa o que a máquina fez durante a lacuna.

## Passo 5 — Cobertura (bruta × única × sobreposição)

```python
from agrofleet.analytics.coverage import coverage
r = coverage(df[df.machine_id == "SIM-01"], with_geojson=False)
print(r.gross_m2 / 1e4, r.unique_m2 / 1e4, 100 * r.excess_m2 / r.gross_m2)   # 22.48 20.66 8.12
```

Pipeline interno: filtra posição, estados e precisão → `build_segments` (UTM; ambos extremos `working`; sem lacuna) → retângulos → `unary_union`. *Verificar:* contra a verdade do simulador (erro de −1,4% na bruta e −1,6% na única no SIM-01, ver [07](07_validacao_metricas.md)) e contra o caso analítico de 2 passadas.

## Passo 6 — Métricas operacionais e relatórios

`machine_metrics` devolve distância (sem saltos nem lacunas), horas por estado, volumes por vazão e por contador (com divergência entre eles), taxa realizada (L/ha sobre área única), produtividade (ha/h de trabalho e de horas engajadas) e custo/ha quando você informa `hourly_cost` e `product_cost_per_l`. `report(df, by="machine"|"property"|"field"|"operator")` agrega; para grupos que não são "máquina" a área única é a **união entre máquinas** (duas máquinas sobrepostas contam uma vez). Amostras sem a chave do grupo vão para `sem_valor` — reportadas, nunca atribuídas a um grupo inventado.

## Passo 7 — Anomalias

```python
from agrofleet.analytics import anomalies as A
res = A.detect(df, A.AnomalyConfig(use_iforest=False))
ev = A.evaluate(res.alerts, [e for e in truth.events])
print(ev["recall"], [(a.machine_id, a.type) for a in res.alerts][:4])
```

Regras (ver [06](06_algoritmos_modelos.md)); cada alerta tem `rule`, `evidence`, `severity` (`info`/`warning`, nunca "crítico") e a nota informativa. `evaluate` compara com os eventos **injetados** — só vale para dado simulado.

## Passo 8 — ROI com premissas explícitas

```python
from agrofleet.analytics import roi as R
I0 = R.EXAMPLE_INPUTS                  # ILUSTRATIVO (is_example=True)
out = R.compute(I0)
print(out["net_benefit_per_year"], out["payback_years"])     # -105600 None
print(R.break_even(I0, "area_ha_per_year", 1000, 20000))     # 6974.17
```

*Verificar:* o `provenance` de cada premissa (`assumed`, `user`, `measured_telemetry` + origem da telemetria) aparece na saída. O botão "usar telemetria medida" substitui premissas por métricas da frota e **carrega a origem** (se a telemetria for simulada, o resultado diz isso).

## Passo 9 — Subir a pilha e ver ao vivo

```bash
cp .env.example .env               # troque TODOS os CHANGE_ME
podman compose up -d --build       # ou: docker compose up -d --build
podman compose --profile sim up -d simulator
# abra http://localhost:${HTTP_PORT:-8080}
```

Faça login (usuários/senhas definidos no `.env`). Na aba *Sobre* (papel `admin`), o botão *Carregar demonstração SIMULADA* carrega os dados simulados, ou deixe o `simulator` publicar. No painel **Frota** cada máquina mostra o selo de origem; o mapa mostra a trilha e as passadas.

*Verificar:* `GET /health` → `mqtt.connected: true`; `ingest.stored` cresce; 4 máquinas "ao vivo".

## Passo 10 — Validar tudo

```bash
pytest                              # unitários e API (sem Docker)
cd frontend && npm ci && npx tsc --noEmit && npx vitest run && cd ..
# Infra real (PostGIS + Mosquitto) e E2E: ver manual_integracao.md
python scripts/validate_geodesy.py  # requer PostGIS no ar; escreve experiments/geodesy_check.json
```

Os resultados executados estão no README e em [07](07_validacao_metricas.md).

## Passo 11 — Notebooks

```bash
cd notebooks
python build_01.py && python build_02.py && python build_03.py       # geram os .ipynb a partir dos fontes
jupyter nbconvert --to notebook --execute --inplace 0*.ipynb       # executa e grava as saídas
```

*Verificar:* zero células com erro e zero ocorrências de "preencher" (os textos de interpretação foram escritos **depois** de ver as saídas reais).
