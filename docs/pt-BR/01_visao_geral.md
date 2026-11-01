# 01 — Visão geral

> **Público:** engenheiro(a) de ML/visão computacional que está migrando para ciência de dados geoespaciais aplicada ao agro. Assumimos que você domina Python, pandas, scikit-learn e avaliação de modelos; **não** assumimos que você conhece GNSS, ISOBUS ou o jargão de pulverização.

## 1.1 O problema

Um pulverizador autônomo (ou convencional com telemetria) produz, a cada segundo, um registro: onde está, a que velocidade, em que estado operacional, quanta calda sai, a que pressão, quanto sobra no tanque, qual a qualidade do sinal de posição. Quem opera uma frota precisa responder perguntas **a partir desses registros**:

| Pergunta | Onde é respondida |
|---|---|
| A máquina está viva? Onde está? Em que estado? | Dashboard (`Fleet.tsx`), `/machines`, WebSocket |
| O que aconteceu às 10:14? | Reprodução temporal (`Playback.tsx`), `/playback` |
| Quantos hectares realmente foram cobertos? Quanto foi repetido? Quanto ficou sem aplicar? | `agrofleet/analytics/coverage.py`, `/coverage` |
| Quanto tempo trabalhando × deslocando × parada × sem dado? Custo por hectare? | `agrofleet/analytics/metrics.py`, `/metrics`, `/reports` |
| A vazão está coerente com taxa × velocidade × largura? | `agrofleet/analytics/anomalies.py`, `/alerts` |
| Esses dados são confiáveis o bastante para eu tirar conclusões? | `agrofleet/quality.py`, `/uploads` |
| Vale a pena investir em autonomia **com as premissas que eu declarar**? | `agrofleet/analytics/roi.py`, `/roi` |

## 1.2 O que o sistema **é** e o que **não é**

**É:** telemetria, monitoramento e auditoria. Lê o que a máquina *reportou*, valida, armazena, calcula métricas geométricas e operacionais e emite **alertas informativos**.

**Não é:**

- **Não controla** direção, freio nem aplicação, e **não gera comandos** para máquinas. Isso é garantido por construção, em três camadas verificáveis:
  1. o schema `TelemetryPoint` tem `extra="forbid"`: um campo desconhecido (por exemplo, `set_speed_ms`) é rejeitado (teste `test_envelope_rejects_unknown_field_such_as_a_command`);
  2. a ACL do Mosquitto permite ao dispositivo apenas *escrever* em `agrofleet/v1/+/telemetry` e ao backend apenas *ler* o mesmo tópico: não existe tópico de comando (`infra/mosquitto/entrypoint.sh`);
  3. o WebSocket é unidirecional (servidor → cliente); o que o cliente enviar é ignorado.
- **Não é** um sistema de segurança funcional (ISO 13849 / ISO 25119 estão fora de escopo). Todo alerta carrega a nota *"Alerta informativo; decisões críticas permanecem com sistemas certificados e pessoas."*
- **Não diagnostica bico entupido** a partir da pressão global. A pressão medida na barra é uma grandeza agregada; ela não identifica bico, filtro ou mangueira (ver [06](06_algoritmos_modelos.md) e [09](09_limitacoes.md)).
- **Não foi validado com telemetria de máquina real.** Todos os resultados numéricos deste repositório vêm de um **simulador** declarado como tal (`origin = simulated`).

## 1.3 Princípios de projeto (e por que importam para quem vem de ML)

1. **Nulo nunca vira zero.** Campo ausente é `None` (`NaN` → `null` na API). Em ML você já conhece o custo de imputar em silêncio; aqui o custo é maior porque um `flow_lpm = 0` preenchido por padrão é indistinguível de "bomba desligada".
2. **Origem é dado de primeira classe.** `origin ∈ {simulated, real, unknown}` percorre banco, API e interface (faixa de proveniência e selo por máquina). É a defesa contra o erro mais perigoso deste domínio: apresentar dado sintético como se fosse medição.
3. **Rejeitar com motivo, não corrigir em silêncio.** Linhas inválidas são contadas e explicadas (`/ingest/rejects`); unidade é deduzida só do *nome* da coluna e registrada; timestamp sem fuso exige `assume_tz` explícito.
4. **Não interpolar sobre o que não foi observado.** Perda de GNSS e lacunas de tempo **não** são preenchidas: o tempo vai para `no_data`, e a distância em linha reta sobre a perda é reportada *à parte* (`distance_across_gaps_km_straight_line`), nunca somada.
5. **Verdade-terreno conhecida onde possível.** O simulador injeta eventos com horário exato e áreas com valor analítico; isso permite medir erro, não só "parece certo".
6. **Honestidade sobre o que não existe.** ISOXML e API de fabricante **não estão implementados** (ver [03](03_fontes_dados.md)); o registro de adaptadores (`/limits`) diz isso explicitamente.

## 1.4 Mapa do repositório

```
06-AgroFleet-Intelligence/
├── agrofleet/              # pacote Python (núcleo, testável sem rede)
│   ├── schema.py           # TelemetryPoint + envelope MQTT versionado
│   ├── geo.py              # projeção UTM, haversine, vazão esperada
│   ├── quality.py          # relatório de qualidade de arquivo/lote
│   ├── adapters/           # CSV, JSON, GeoJSON, GPX, MQTT, ISOXML(recusa)
│   ├── analytics/          # coverage, metrics, anomalies, roi, frame
│   └── simulator/fleet.py  # frota simulada reprodutível (4 pulverizadores)
├── backend/app/            # FastAPI: API, auth, banco, pipeline ao vivo
├── frontend/src/           # React + TypeScript + MapLibre GL JS
├── simulator/              # CLIs: generate.py (arquivos) e publish.py (MQTT)
├── infra/                  # Dockerfiles, nginx, Mosquitto (ACL gerada)
├── notebooks/              # 3 notebooks executados com interpretação
├── tests/                  # pytest (unit, API, infra, E2E)
├── payload_examples/       # exemplos aceitos e rejeitados (testados)
├── scripts/                # validate_geodesy.py
├── experiments/            # saída real da validação geodésica
└── docs/pt-BR/             # este material
```

Os diretórios `adapters/`, `analytics/` e `simulator/` pedidos na especificação existem como subpacotes de `agrofleet/` (para serem importáveis por backend, notebooks e testes sem duplicação); o diretório `simulator/` da raiz contém só os programas de linha de comando.

## 1.5 Como estudar este material

| Se você quer… | Leia |
|---|---|
| Entender o domínio (GNSS, geometria de faixa, ISOBUS) | [02 Fundamentos](02_fundamentos.md) |
| Saber de onde vêm os dados e o que cada fonte garante | [03 Fontes de dados](03_fontes_dados.md) |
| Ver os componentes e o fluxo | [04 Arquitetura](04_arquitetura.md) |
| Reproduzir tudo passo a passo | [05 Pipeline](05_pipeline_passo_a_passo.md) |
| A matemática e os modelos | [06 Algoritmos](06_algoritmos_modelos.md) |
| Como sabemos que funciona (e onde não sabemos) | [07 Validação](07_validacao_metricas.md) |
| Ler resultados sem se enganar | [08 Interpretação](08_interpretacao_resultados.md) |
| Os limites reais | [09 Limitações](09_limitacoes.md) |
| Praticar | [10 Exercícios](10_exercicios.md) |
| Vocabulário PT/EN | [11 Glossário](11_glossario.md) |
| O que fazer depois | [12 Próximos passos](12_proximos_passos.md) |

Notebooks (em `notebooks/`, já executados): `01_telemetria_simulador_e_qualidade`, `02_geometria_cobertura_e_metricas`, `03_series_anomalias_e_roi`. Cada célula de código tem Markdown antes (o quê/por quê/alternativas) e depois (como ler/como verificar).
