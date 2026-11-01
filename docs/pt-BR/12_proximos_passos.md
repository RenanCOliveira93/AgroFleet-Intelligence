# 12 — Próximos passos e roteiro de estudo

## 12.1 O que fazer com dados reais (pendências técnicas, em ordem de valor)

Tudo abaixo exige **dados, credenciais, hardware ou execuções longas** que não estavam disponíveis; o código-base está pronto para receber, e cada item diz como validar depois.

| # | Pendência | Pré-requisito | Como implementar | Como validar |
|---|---|---|---|---|
| 1 | **Telemetria real via CSV/MQTT** | exportação ou gateway de uma máquina real | `read_csv(..., origin="real", units=..., assume_tz=...)` ou publicar `agrofleet/v1` | `assess()` limpo; protocolo de [07 §7.8](07_validacao_metricas.md) |
| 2 | **Calibração de limiares** (tolerância de vazão, banda de pressão) | ≥ alguns dias de operação normal | usar a varredura do notebook 03 com o ruído do **seu** fluxômetro | FP/h e recall por tipo em período posterior ao de calibração |
| 3 | **ISOXML / time logs** | amostras `TASKDATA.XML` + `TLG*.XML/BIN` **válidas** de terminal real, e um parser avaliado | avaliar bibliotecas existentes de ISOXML/time log (confira manutenção e licença), mapear DDI → campos canônicos, escrever adaptador com `AdapterResult` | comparar série lida com a exportação CSV do mesmo terminal; testes com amostras reais anonimizadas |
| 4 | **API de fabricante** | documentação oficial e credenciais do fabricante | adaptador novo seguindo `AdapterResult`; segredos só em ambiente | testes contra sandbox do fabricante |
| 5 | **Validação de área** | referência geodésica de um talhão | comparar `unique_ha` estimada; calibrar largura efetiva | erro com intervalo por dia/máquina |
| 6 | **Suavização de trajetória** | dados com ruído de posição real | Kalman/Savitzky–Golay antes de `build_segments`, ou comprimento por velocidade×Δt | reexecutar sensibilidade a ruído (notebook 02 §3c, exercício 8) |
| 7 | **Durabilidade da ingestão** | decisão de arquitetura | fila persistente / consumidor separado (por exemplo, broker com *shared subscription* ou escrita direta pelo gateway) | teste de queda do processo com contagem de mensagens |
| 8 | **TLS e endurecimento** | certificados | listener MQTT 8883 com `tls_cafile`; HTTPS no nginx; revogação de token, rate limiting, MFA/SSO | revisão de segurança / teste de intrusão |
| 9 | **Carga e escala** | volume-alvo definido | benchmark de ingestão e consultas; avaliar TimescaleDB/particionamento | relatório de latência e vazão **medidos** |
| 10 | **Docker Desktop** | máquina com Docker Desktop | `docker compose up --build` com o mesmo `.env` | repetir E2E e testes de infra |
| 11 | **Imagem PostGIS nativa arm64** | — | trocar a imagem do `db` por uma multi-arquitetura | tempo de subida e testes de infra |
| 12 | **ML supervisionado** | eventos reais rotulados | classificador por tipo ou resíduo de modelo físico; validação temporal | precisão/sensibilidade por tipo, comparadas às regras |

## 12.2 Ideias de pesquisa (se o objetivo é aprender/publicar)

- Estimador de largura efetiva a partir de dados (identificabilidade com GNSS ruidoso).
- Modelo hierárquico de ruído do fluxômetro por máquina para limiar bayesiano.
- Fusão visão + telemetria: câmeras na barra (seu conhecimento de CV) para verificar a pulverização que a telemetria só infere. **Exige hardware e dados que não existem aqui.**
- Detecção de mudança (CUSUM, Page–Hinkley) comparada a limiar fixo, avaliada em dados rotulados.

## 12.3 Roteiro de estudo recomendado (para engenheiro de ML/CV migrando para geoespacial/agro)

**Semana 1 — Fundamentos de geodésia e GNSS.** Ler [02](02_fundamentos.md). Reproduzir à mão a conversão lat/lon → UTM com `pyproj` e comparar com `Geod` (distância e área). Entregável: tabela de erro UTM × geodésia em 3 latitudes. *Verificação:* seu resultado reproduz o +0,11% do `geodesy_check.json` para este local.

**Semana 2 — Geometria de cobertura.** Notebook 02 inteiro; exercícios 3, 5 e 8. Entregável: gráfico de sobreposição estimada × σ de ruído com a **sua** semente. *Verificação:* monotonicidade e ordem de grandeza como em [07 §7.3](07_validacao_metricas.md).

**Semana 3 — Séries temporais operacionais e qualidade de dados.** Notebook 01, exercícios 2 e 4, `quality.py`. Entregável: um relatório de qualidade para um CSV seu com defeitos plantados.

**Semana 4 — Anomalias com honestidade.** Notebook 03 (seções de regra, tolerância e IsolationForest); exercícios 6 e 9. Entregável: curva FP/h × recall por tolerância para um ruído que você escolher, e discussão do "orçamento de alarmes".

**Semana 5 — Sistemas.** Ler [04](04_arquitetura.md) e [09](09_limitacoes.md); subir a pilha ([05](05_pipeline_passo_a_passo.md) passo 9); rodar o simulador e observar o WebSocket. Entregável: um diagrama do que acontece se o backend cair no meio de um lote (e a proposta de correção).

**Semana 6 — Decisão e ROI.** [06 §6.6](06_algoritmos_modelos.md), exercício 7, notebook 03 (ROI). Entregável: ROI com **suas** premissas e proveniência explícita, e um parágrafo dizendo o que você **não** concluiria.

**Depois:** exercício 10 com dados reais assim que obtiver acesso (item 1 da tabela acima).

## 12.4 Leitura complementar (verificável)

- ISO 11783 (partes 10 e 11) — consulta paga; para um panorama, a documentação pública de bibliotecas de ISOXML.
- Snyder (1987), *Map Projections — A Working Manual*; Karney (2013), *Algorithms for geodesics*.
- Liu, Ting & Zhou (2008), *Isolation Forest*; Chandola, Banerjee & Kumar (2009), *Anomaly Detection: A Survey*, ACM Computing Surveys.
- Documentação do PostGIS (`geography`, índices GIST), do `shapely` 2 e do `pyproj`.
- MQTT 3.1.1/5.0 (OASIS); RFC 7519 (JWT); RFC 7946 (GeoJSON).
