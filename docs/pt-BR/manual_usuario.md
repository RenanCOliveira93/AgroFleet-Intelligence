# Manual do usuário

Para quem **usa a interface** (operação, agronomia, gestão de frota). Leia primeiro a faixa no topo da tela: ela diz se os dados exibidos são **simulados**, **reais** ou **desconhecidos**.

> O sistema **monitora e audita**. Ele **não controla** a máquina e **não envia comandos**. Os alertas são **informativos**: decisões críticas permanecem com sistemas certificados e pessoas.

## 1. Entrar

Acesse `http://<servidor>:<HTTP_PORT>` (padrão do compose: 8080) e entre com usuário e senha fornecidos pelo administrador.

| Papel | Pode |
|---|---|
| `viewer` | consultar frota, reprodução, cobertura, alertas, relatórios e ROI |
| `analyst` | tudo do viewer + enviar arquivos (Upload), enviar limites de talhão, recomputar alertas |
| `admin` | tudo do analyst + alterar a configuração de alertas, carregar/apagar a demonstração simulada |

Não existe usuário ou senha padrão: quem instalou os definiu no arquivo `.env`.

## 2. Faixa de proveniência e filtro de origem

No topo, a faixa informa a origem dos dados presentes (por exemplo, "apenas dados SIMULADOS"). O seletor de origem filtra todas as abas (`todas`, `simulado`, `real`, `desconhecido`). Cada máquina tem um **selo** de origem. **`real` é uma declaração de quem enviou os dados**; o sistema não consegue provar a procedência.

## 3. Abas

### Frota
Cartões por máquina: estado, velocidade, GNSS (aparece "(sem posição)" quando falta posição), vazão, pressão, nível do tanque e se está **ao vivo** (recebeu telemetria recentemente). O mapa mostra a posição atual e a trilha. Campo ausente aparece como "—", **nunca como 0**.

### Reprodução
Controle deslizante e botões de reprodução reexibem o histórico. A posição vem da última amostra até o instante escolhido; **não há extrapolação**: antes do primeiro ponto, depois do último ou durante a perda de GNSS, a máquina **não é desenhada**. A trilha é quebrada onde falta posição.

### Cobertura
Marque as máquinas (uma ou várias) e escolha o talhão. A tela mostra:
- **Área bruta** (o que a barra varreu, contando repetição), **área única** (terra tocada), **excesso** e **região ≥ 2×**;
- cobertura do talhão (coberta, não coberta, fora do limite);
- o que foi **excluído** e por quê (sem posição, fora de trabalho, lacuna, precisão baixa…).
Como ler: [08 — Interpretação](08_interpretacao_resultados.md). Atenção: **a largura de trabalho informada pela máquina precisa estar correta**; um erro de 5% altera muito a sobreposição.

### Alertas
Lista de alertas com tipo, severidade (`info`/`warning`), intervalo, regra e evidências. Atualiza ao vivo. **Analyst** pode "Recalcular alertas" (opcionalmente marcando *incluir ML*, o modelo experimental IsolationForest, rotulado como tal). Os limiares são alterados pelo **admin** na API (`PUT /config/alerts`; a interface não tem formulário para isso). Lembretes:
- a mensagem de vazão diz que a **causa não é identificada**;
- a **pressão é global**: não aponta bico, filtro ou mangueira;
- um evento pode gerar mais de um alerta.

### Relatórios
Agrupe por **máquina, propriedade, talhão ou operador** e filtre pelo **período** (De/Até). Exporte em **CSV**. O custo por hectare **não** aparece nesta aba: ele é calculado pela API `GET /metrics/{machine_id}?hourly_cost=...&product_cost_per_l=...` com premissas suas (sem premissa, não há custo). Amostras sem a chave do grupo (por exemplo, sem `field_id`) aparecem em "sem valor" em vez de serem atribuídas a um grupo inventado.

### Upload
1. Escolha o **tipo** (`csv`, `json`, `geojson`, `gpx`), a **origem** (declare com honestidade), e, se necessário, o **fuso** (`assume_tz`), as **unidades** e o `machine_id` (obrigatório para GeoJSON/GPX).
2. Envie **sem gravar** primeiro: a tela mostra o **relatório de qualidade** (linhas válidas/rejeitadas com motivo, duplicatas, fora de ordem, lacunas, saltos, unidades suspeitas) e o veredito (`ok`, `warnings`, `blocking`).
3. Se estiver satisfeito, envie com **gravar**. `blocking` impede a gravação. Reenviar o mesmo arquivo não duplica os dados.
`ISOXML/TASKDATA` **não é suportado**; a tela devolve o motivo.

### ROI
Informe suas premissas (máquinas, investimento, vida útil, juros, área, aplicações, custo horário da operação convencional, produto…). O resultado mostra custo por hectare, benefício anual, payback, VPL, **tornado** (±20%), **pontos de equilíbrio** e uma grade. Há um cenário de **exemplo ILUSTRATIVO** — não é referência de mercado. O botão *Preencher produtividade e sobreposição com a telemetria* substitui duas premissas por valores da frota e **mostra a origem** (se for simulada, o resultado diz).

### Sobre / limites
Escopo, limites e o estado de cada adaptador (implementado/não implementado/testado). **Admin**: carregar ou apagar a demonstração simulada.

## 4. Demonstração rápida (admin)
Aba *Sobre / limites* → **Carregar demonstração SIMULADA**. Em seguida explore Frota, Reprodução (veja a SIM-03 perder GNSS), Cobertura, Alertas e ROI. Para encerrar: **Apagar dados simulados**.

## 5. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Tela inteira com erro 502 depois de atualizar o servidor | o servidor web não reconectou ao backend recriado | reiniciar o contêiner `frontend` |
| "Nenhuma máquina ao vivo" | simulador/dispositivo não está publicando, ou credenciais MQTT erradas | ver `GET /health` (`mqtt.connected`) |
| Upload rejeita todas as linhas | falta fuso, unidade ou coluna obrigatória (`machine_id`, `ts`) | ler o motivo na lista de rejeições; informar `assume_tz`/`units` |
| Cobertura menor que o esperado | perda de GNSS, filtro de precisão ou largura ausente | ver "excluídos" na aba Cobertura |
| Sobreposição "impossível" | largura mal calibrada ou GNSS ruidoso | ver [08](08_interpretacao_resultados.md) §8.2 |
| Mapa sem fundo | por padrão o fundo é em branco (sem serviço externo) | opcional: configurar `VITE_BASEMAP_TILES` na construção |
