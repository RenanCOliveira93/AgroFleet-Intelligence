from _build import build

C = []
md = lambda t: C.append(("md", t))
code = lambda t: C.append(("code", t))

md("""
# 01 — Telemetria, simulador e qualidade dos dados

**Objetivo.** Entender o modelo canônico de telemetria do AgroFleet, gerar a frota **simulada** (4 pulverizadores) e passar dados "sujos" pelos adaptadores e pelo relatório de qualidade.

> ⚠️ **Tudo neste notebook é SIMULADO** (`origin="simulated"`). Local, talhões, códigos de erro e números são fictícios. Nenhum resultado aqui descreve uma máquina real.

**O que você vai conseguir verificar ao final**
1. Campos opcionais ausentes aparecem como `None`/`NaN`, nunca como `0` inventado.
2. O simulador é determinístico (mesma semente → mesmos bytes) e registra uma *verdade-terreno* dos eventos injetados.
3. Os adaptadores contam e explicam cada linha rejeitada; o relatório de qualidade aponta duplicatas, lacunas, saltos e suspeita de unidade.
""")
code("""
import sys, json, warnings
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT))
import numpy as np, pandas as pd, matplotlib.pyplot as plt
from agrofleet.schema import TelemetryPoint, SCHEMA_VERSION
from agrofleet.simulator.fleet import simulate
from agrofleet.analytics.frame import to_frame
from agrofleet import adapters as AD
from agrofleet.quality import assess
plt.rcParams.update({"figure.dpi": 110, "axes.grid": True, "grid.alpha": .3})
print("schema", SCHEMA_VERSION)
""")
md("""
## 1. O modelo canônico e as regras que ele impõe

**O quê.** `TelemetryPoint` (pydantic) descreve *o que a máquina reportou* — não o que é verdade. Campos: `machine_id`, `ts` (UTC com fuso), `lat/lon`, `speed_ms`, `state`, `gnss_fix`, `accuracy_m`, `flow_lpm`, `pressure_bar`, `tank_level_l`, `consumption_l`, `working_width_m`, `application_rate_lha`, `error_codes`, `sensor_quality` por sensor, e metadados opcionais (`property_id`, `field_id`, `operator_id`).

**Por quê.** A regra central é *ausente ≠ zero*. Uma vazão `0` diz "o sensor mediu zero"; `None` diz "não sei". Misturar os dois produz médias erradas e alertas falsos. Por isso a validação rejeita `NaN/Inf`, coordenada `(0,0)` (valor-padrão típico de receptor sem fix), `lat` sem `lon`, e *timestamp sem fuso*.

**Alternativas.** (a) Preencher com último valor conhecido (*forward fill*) — esconde perda de sinal; (b) usar `0` — confunde medição e ausência; (c) esquema livre (dict) — empurra a validação para cada consumidor. Escolhemos validar na borda.

**Como verificar.** As células abaixo tentam criar pontos inválidos e mostram o erro.
""")
code("""
tests = {
  "sem fuso": dict(ts="2026-03-10T10:00:00"),
  "(0,0)": dict(ts="2026-03-10T10:00:00Z", lat=0.0, lon=0.0),
  "lat sem lon": dict(ts="2026-03-10T10:00:00Z", lat=-16.7),
  "NaN": dict(ts="2026-03-10T10:00:00Z", flow_lpm=float("nan")),
  "campo extra": dict(ts="2026-03-10T10:00:00Z", comando="parar"),
}
for nome, kw in tests.items():
    try:
        TelemetryPoint(machine_id="M1", **kw); print(f"{nome:12s} -> ACEITO (inesperado)")
    except ValueError as e:
        err = e.errors()[0]
        print(f"{nome:12s} -> rejeitado ({'.'.join(map(str, err['loc'])) or 'modelo'}): {err['msg'][:80]}")
p = TelemetryPoint(machine_id="M1", ts="2026-03-10T07:00:00-03:00")
print("\\nUTC normalizado:", p.ts, "| flow:", p.flow_lpm, "| lat:", p.lat, "| state:", p.state.value)
""")
md("""
**Como ler a saída.** Todos os casos inválidos devem aparecer como *rejeitado*. O último print mostra o horário `-03:00` convertido para UTC (10:00) e os opcionais em `None`. O caso `campo extra` (`comando`) é rejeitado de propósito: o esquema **não tem** campo de comando — `extra="forbid"` impede que alguém "contrabandeie" instruções de controle num payload de telemetria.
""")
md("""
## 2. O simulador de 4 pulverizadores

**O quê.** Quatro máquinas, cada uma num talhão retangular de 20 ha, com passadas paralelas (*boustrophedon*), barra de 24 m e taxa nominal de 100 L/ha. Cenários injetados:

| Máquina | Cenário | O que acontece |
|---|---|---|
| SIM-01 | normal | passadas a 22 m (sobreposição nominal de 8,3%) |
| SIM-02 | `stop` | parada por falha simulada de 7 min (código fictício `SIM-E042`) |
| SIM-03 | `gnss_loss` + `missing_data` | 4 min sem GNSS, depois 2 min de reconvergência RTK-float; 2,5 min sem vazão/pressão (em outro momento) |
| SIM-04 | `abnormal_flow` | vazão 35% abaixo do esperado por 5 min (causa **não** modelada) |

**Por quê.** Sem verdade conhecida não dá para testar nada de forma honesta. Aqui a verdade é analítica: áreas planejadas, sobreposição nominal e janelas dos eventos.

**Limites.** É um modelo simples (velocidade suavizada, ruído gaussiano, vazão = taxa × velocidade × largura). Não tem vento, relevo, terraços, curvas de nível, falhas realistas de bico ou latência de rede.
""")
code("""
pts, truth = simulate(seed=7)
df = to_frame(pts)
pts2, _ = simulate(seed=7)
print("pontos:", len(pts), "| determinístico:", [p.model_dump_json() for p in pts] == [p.model_dump_json() for p in pts2])
print("origens:", list(df.origin.unique()), "| máquinas:", sorted(df.machine_id.unique()))
print(df.groupby("machine_id").agg(amostras=("ts","size"), inicio=("ts","min"), fim=("ts","max")).to_string())
print("\\nEventos injetados (verdade-terreno):")
print(pd.DataFrame(truth.events)[["machine_id","type","start","end"]].to_string(index=False))
""")
md("""
**Como ler a saída.** `determinístico: True` confirma reprodutibilidade (importante para testes). As quatro máquinas terminam o talhão em tempos diferentes porque o número de reabastecimentos, paradas e deslocamentos difere. A tabela de eventos é a *verdade* que usaremos nos notebooks seguintes para medir detecção — e que o sistema de detecção **não enxerga**.
""")
code("""
fig, ax = plt.subplots(1, 2, figsize=(13, 5))
cores = {"working":"#2e7d32","transport":"#1565c0","refilling":"#00838f","fault":"#c62828"}
for mid, g in df.groupby("machine_id"):
    gg = g.dropna(subset=["lat"])
    for st, c in cores.items():
        s = gg[gg.state == st]
        ax[0].scatter(s.lon, s.lat, s=1, c=c, label=st if mid == "SIM-01" else None)
ax[0].set_title("Posições simuladas por estado (pontos sem GNSS não aparecem)"); ax[0].set_xlabel("lon"); ax[0].set_ylabel("lat"); ax[0].legend(markerscale=8)
g = df[df.machine_id == "SIM-03"].copy(); g["min"] = (g.ts - g.ts.min()).dt.total_seconds() / 60
ax[1].plot(g["min"], g.flow_lpm, lw=.8, label="vazão (L/min)"); ax[1].plot(g["min"], g.pressure_bar * 20, lw=.8, label="pressão × 20 (bar)")
perda = g[g.lat.isna()]; ax[1].axvspan(perda["min"].min(), perda["min"].max(), color="red", alpha=.12, label="sem GNSS")
nulo = g[g.flow_lpm.isna()]; ax[1].axvspan(nulo["min"].min(), nulo["min"].max(), color="orange", alpha=.2, label="sensores sem dado")
ax[1].set_title("SIM-03: perda de GNSS e sensores sem dado"); ax[1].set_xlabel("minutos desde o início"); ax[1].legend()
plt.tight_layout(); plt.show()
""")
md("""
**Como ler os gráficos.** À esquerda, quatro talhões (A–D) com passadas verdes (trabalho) e as linhas azuis longas de deslocamento até o depósito (canto inferior esquerdo; os tracinhos azuis nas pontas das passadas são as manobras de cabeceira). No talhão do alto à esquerda (SIM-03) **faltam passadas inteiras**: são os pontos sem GNSS, que simplesmente não existem no gráfico — o sistema não "completa" a trajetória.

À direita, o SIM-03. A faixa vermelha é o intervalo **sem posição** (min 8,5–12,5); a laranja, o intervalo **sem vazão/pressão** (min ~3,4–5,9) — nesses trechos as curvas *quebram*, não há linha ligando os pontos. Três coisas para não confundir com defeito: (1) cada passada começa com um **pico de vazão** (~100 L/min contra ~72 nominais) porque o simulador entra na passada mais rápido do que a velocidade de trabalho e a vazão de um controlador de taxa acompanha a velocidade (vazão = taxa × velocidade × largura); (2) o trecho plano entre os minutos ~17 e ~25 é a viagem ao depósito e o reabastecimento (vazão zero, pressão baixa); (3) a máquina continua trabalhando durante a perda de GNSS, mas o sistema não sabe *onde* — situação que a análise de cobertura precisa tratar com honestidade (notebook 02).
""")
md("""
## 3. Adaptadores: CSV "sujo" e relatório de qualidade

**O quê.** O adaptador CSV mapeia colunas por *aliases* explícitos e converte unidades (ex.: `km/h→m/s`, `kPa→bar`). Cada linha rejeitada é contada com o motivo. O relatório de qualidade (`assess`) acusa duplicatas, ordem, lacunas, saltos de posição e suspeita de unidade.

**Por quê.** Arquivos reais chegam com colunas em português, unidades misturadas, horários sem fuso e linhas quebradas. Em vez de "consertar em silêncio", o sistema **mostra o que fez** (`meta.columns_mapped`, `units`, `columns_ignored`).

**Alternativas.** Inferir unidade pela magnitude (arriscado: 18 pode ser km/h ou m/s) — só aceitamos inferência pelo *nome* da coluna (`speed_kmh`) e registramos o aviso.
""")
code("""
sujo = (
 "machine,time,lat,lon,speed_kmh,pressure,flow,state\\n"
 "M1,2026-03-10T10:00:00Z,-16.7,-47.6,18,300,70,spraying\\n"
 "M1,2026-03-10T10:00:00Z,-16.7,-47.6,18,300,70,spraying\\n"      # duplicada
 "M1,2026-03-10T10:05:00Z,-16.7,-47.6,60,300,70,spraying\\n"      # lacuna de 5 min + velocidade alta
 "M1,2026-03-10T10:00:30Z,-16.7,-47.6,18,300,70,spraying\\n"      # fora de ordem
 "M1,2026-03-10T10:05:01Z,-16.6,-47.6,18,300,70,spraying\\n"      # salto de ~11 km em 1 s
 "M1,2026-03-10 10:05:02,-16.6,-47.6,18,300,70,spraying\\n"       # sem fuso
 "M1,2026-03-10T10:05:03Z,0,0,18,300,70,spraying\\n")             # (0,0)
r = AD.read_csv(sujo, units={"pressure": "kPa"}, origin="simulated")
print("válidas:", len(r.points), "| rejeitadas:", r.n_rejected_total)
for x in r.rejected: print("  linha", x["row"], "->", x["reason"][:100])
print("avisos:", r.warnings)
print("mapeamento:", r.meta["columns_mapped"])
q = assess(r)
print("\\nveredito:", q["verdict"])
for f in q["flags"]: print(f"  [{f['level']}] {f['code']}: {f['msg']}")
""")
md("""
**Como ler a saída.** Esperamos: 2 linhas rejeitadas (*sem fuso* e *(0,0)*), com motivo; o aviso de que `speed_kmh` teve a unidade deduzida do **nome**; e no relatório os avisos `duplicates`, `out_of_order`, `position_jumps` e `gaps`. O aviso `unit_suspect` **não** dispara aqui, e é o esperado: 60 km/h (16,7 m/s) fica abaixo do limite de 25 m/s e 300 kPa = 3 bar é plausível; ele só aparece quando a magnitude é implausível para a unidade declarada. O veredito `warnings` significa "dá para usar, mas leia os avisos"; `blocking` significa "nada foi aproveitável".

**O que isso NÃO garante.** O relatório detecta inconsistências *internas*. Não consegue saber se um dado "real" foi de fato coletado por uma máquina — por isso `origin="real"` é só uma **declaração** de quem enviou.
""")
code("""
# O formato JSON (envelope MQTT versionado) também passa pelo mesmo caminho de validação
from agrofleet.schema import wrap
env = [wrap(pts[0], "a"), {**wrap(pts[1], "b"), "schema_version": "2.0"}]
r2 = AD.read_json(json.dumps(env))
print("envelopes aceitos:", len(r2.points), "| rejeitados:", r2.n_rejected_total, "->", r2.rejected[0]["reason"][:80])
print("ISOXML:", AD.REGISTRY["isoxml"]); print("REST de fabricante:", AD.REGISTRY["manufacturer_rest"])
try: AD.read_isoxml(b"<x/>")
except AD.NotImplementedAdapter as e: print("\\nisoxml ->", e)
""")
md("""
**Como ler a saída.** O envelope com `schema_version: 2.0` é recusado — não adivinhamos o significado de uma versão futura. ISOXML e API de fabricante aparecem como **não implementados**, com o motivo: não havia amostra válida de `TASKDATA`/`TLG` nem documentação de API para validar. Preferimos não entregar um parser que *parece* funcionar sem teste real.

## Resumo e próximos passos
- O modelo canônico transforma "ausente" em `None` e rejeita inconsistências na borda.
- O simulador entrega verdade conhecida; os adaptadores e o relatório de qualidade explicam cada rejeição.
- **Próximo:** notebook 02 — geometria de cobertura (área bruta × única × sobreposição) e métricas operacionais.
""")
build("01_telemetria_simulador_e_qualidade.ipynb", C)
