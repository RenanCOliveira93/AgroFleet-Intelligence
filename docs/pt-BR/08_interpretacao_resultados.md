# 08 — Interpretação de resultados

Como ler cada saída do sistema sem se enganar. Os números de exemplo vêm do simulador (ver [07](07_validacao_metricas.md)).

## 8.1 Antes de ler qualquer número: origem e qualidade

1. **Origem.** A faixa no topo da interface e o selo por máquina dizem `simulado`, `real` ou `desconhecido`. `real` é uma **declaração de quem enviou**; o sistema não consegue provar procedência. Os relatórios trazem uma coluna com a(s) origem(ns) de cada grupo.
2. **Veredito de qualidade** do upload (`ok`/`warnings`/`blocking`) e os `flags`. Duplicatas, saltos e lacunas não corrigidos tornam as métricas suspeitas.
3. **Rejeições** (`/ingest/rejects`): se 30% das linhas foram rejeitadas, a análise do restante pode estar enviesada (por exemplo, rejeitando justamente as linhas sem fuso de um equipamento).

## 8.2 Áreas: bruta, única, excesso, região ≥ 2×

| Medida | Significa | Não significa |
|---|---|---|
| Bruta | quanto a barra varreu, contando repetição | área tratada |
| Única | terra tocada pelo menos uma vez (estimada) | área **corretamente** aplicada (dose pode variar) |
| Excesso (bruta − única) | área-equivalente varrida de novo | volume desperdiçado (depende da dose e do controle de seção) |
| Região ≥ 2× | onde ≥ 2 faixas se sobrepõem | medida confiável com GNSS ruidoso |
| `outside_field_ha` | tratado fora do limite do talhão | prova de aplicação indevida (pode ser limite impreciso ou deriva de posição) |

Regras de ouro:

- **Largura é premissa.** Mudar a largura em 5% muda a sobreposição de 8,1% para 3,7% (−5%) ou 12,2% (+5%) enquanto a área única quase não muda. Se a largura veio de um cadastro genérico, **não confie na sobreposição** mesmo com GNSS perfeito.
- **Ruído infla a sobreposição.** Com σ = 1 m a estimativa passa de 8% para 22%, sem a máquina ter sobreposto mais. O mecanismo: o comprimento do caminho cresce com o jitter (bruta ↑) e retângulos levemente girados deixam frestas (única ↓). Só use sobreposição com RTK/centimétrico.
- **Perda de GNSS reduz a cobertura estimada e é visível.** SIM-03: −15% de área única. Isso **não** é erro do método; é o método se recusando a inventar.

## 8.3 Tempo, distância e produtividade

- `no_data` aparece quando há lacuna acima do limite; **não** é "parada". `sem posição` (sem lat/lon, com telemetria) é outra coisa: o tempo é contado, a distância não.
- A distância em linha reta *sobre* perda de posição fica em `distance_across_gaps_km_straight_line`. Ela é um **piso**, não uma estimativa do percurso.
- **ha/h de trabalho × ha/h engajado:** o primeiro mede a máquina aplicando (SIM-01: 39,4 ha/h); o segundo inclui deslocamento, parada e reabastecimento (28,3 ha/h). Comparar um com o outro entre máquinas ou com a especificação de fabricante é um erro comum.
- **Discrepância vazão×contador**: perto de 0 é bom; valor grande (SIM-03: −8,6%) é um sinal de dado faltante ou de calibração, **não** diz qual dos dois está certo.
- **Custo/ha** é horas × custo horário que **você** informou. Sem premissa, não há custo.

## 8.4 Alertas

- Um alerta é **informativo** e indica *onde olhar*. A mensagem diz explicitamente que a causa não é identificada (`flow_deviation`) e que a pressão é global (`pressure_*`).
- **Um evento pode gerar vários alertas** (persistência interrompida entre passadas, ou múltiplos sensores). Contar alertas ≠ contar eventos.
- **Co-ocorrência de `pressure_outlier` com `flow_deviation`** no simulador é propriedade do simulador (pressão modelada como função da vazão), não evidência sobre máquinas reais.
- **Tolerância:** se muito apertada, o limite fica dentro do ruído do sensor e inunda de falsos alarmes (5% ⇒ ~1 600–2 200 amostras falsas por hora de trabalho com σ = 8%); se muito frouxa (≥ 35% com falha de ~35%), perde o evento. Use a variabilidade do **seu** sensor.
- **Recall de 4/4 do simulador** é otimista (regras escritas com conhecimento do simulador), e no mapa de avaliação `pressure_outlier` também conta como detecção de vazão anormal. Não extrapole para campo.
- **IsolationForest:** `contamination` é orçamento de alarmes. Uma máquina saudável também gera alertas (SIM-01: 2 com c = 0,10; 9 com c = 0,15). Trate como triagem, **nunca** como diagnóstico.

## 8.5 ROI

- O cenário de exemplo é **ilustrativo** e mostra benefício líquido **negativo** (−105 600/ano; sem payback). Isso não é conclusão sobre autonomia; é consequência de números fictícios.
- Leia o **tornado** para saber *quais premissas importam* (capex, número de máquinas, área tratada, custo horário base) e os **pontos de equilíbrio** para saber *quanto precisam mudar* (ex.: área ≈ 6 974 ha/ano, +16%).
- Se uma premissa vier de **telemetria simulada** (`measured_telemetry` com origem `simulated`), o resultado carrega isso. No exemplo, usar 39,2 ha/h e 5,4% de sobreposição *do simulador* piorou o benefício (−170 878): o ponto é a **rastreabilidade da premissa**, não evidência sobre máquinas reais.
- ROI não inclui impostos, financiamento, risco, ganho agronômico, custo de falha — um VPL negativo ou positivo aqui é **sensível a tudo isso**.

## 8.6 Mapa e reprodução temporal

- A posição de uma máquina no instante *t* parte da **última amostra ≤ t**; só há interpolação linear entre **duas amostras consecutivas válidas separadas por ≤ 15 s**. Antes da primeira ou depois da última amostra, ou quando a amostra não tem posição (perda de GNSS), a função devolve `null` e **nenhum ponto é desenhado** (`positionAt`, testada em `lib.test.ts`). O painel Frota escreve "(sem posição)" ao lado do GNSS.
- A trilha até *t* só inclui amostras já ocorridas e é **quebrada** onde falta posição (cada trecho vira uma linha separada).
- O mapa de fundo é opcional (`VITE_BASEMAP_TILES`); por padrão o estilo é em branco para não depender de serviço externo.

## 8.7 Checklist antes de apresentar um resultado

- [ ] Origem declarada e visível?
- [ ] Veredito de qualidade lido e flags resolvidos ou citados?
- [ ] Largura efetiva calibrada (se for falar em sobreposição)?
- [ ] GNSS compatível (RTK) com a medida usada?
- [ ] Perda de posição/lacunas citadas junto com a área?
- [ ] Alertas tratados como indícios, não como causa?
- [ ] Premissas de ROI listadas com proveniência?
