# 09 — Limitações

Lista honesta do que o projeto **não** faz, **não** validou ou faz com ressalvas. Cada item diz o impacto e a mitigação possível.

## 9.1 Escopo funcional

| Limitação | Impacto | Mitigação / próximo passo |
|---|---|---|
| **Não controla** direção, freio nem aplicação; não gera comandos | por projeto | — (garantias em [01](01_visao_geral.md)) |
| **Não é** segurança funcional (ISO 13849 / ISO 25119) | alertas não substituem sistemas certificados | manter decisões críticas com sistemas e pessoas |
| **Não diagnostica bico entupido** a partir de pressão global | só indica problema de aplicação | sensores por seção/bico |

## 9.2 Dados e fontes

| Limitação | Impacto |
|---|---|
| **Nenhuma telemetria de máquina real foi usada.** Todos os resultados são do simulador | desempenho em campo é **desconhecido** |
| **ISOXML/TASKDATA não implementado** (sem amostra válida nem parser validado) | não lê time logs de terminais ISOBUS |
| **API de fabricante não implementada** (nenhuma documentação/credencial usada) | integrações comerciais ficam para depois |
| **CAN/ISOBUS** bruto não usado | exigiria hardware |
| `origin = real` é declaração do remetente | o sistema não prova procedência |
| GeoJSON/GPX sem tempo por ponto são recusados | trilhas "só geometria" não viram telemetria |
| Unidade só é deduzida do nome da coluna ou declarada | CSV com unidade ambígua exige conferência humana |

## 9.3 O simulador

Idealizado: sem vento, relevo, deriva, falha real de bico, resposta real de bomba/válvula, falha de controle de seção, multipath, operadores. Local **fictício**. Como as regras de anomalia foram escritas conhecendo o simulador, o recall medido (4/4) é **otimista**; além disso, no mapa de avaliação `pressure_outlier` conta como detecção de vazão anormal. A pressão é modelada como função da vazão, então sua co-ocorrência com a falha de vazão **não é evidência** sobre máquinas reais.

## 9.4 Geometria e métricas

| Limitação | Medida |
|---|---|
| Viés de subestimação da área em máquinas sem eventos de posição | −1,4% (bruta) a −1,8% (única) |
| Perda de GNSS: cobertura não é interpolada | SIM-03: −15% de área única |
| Largura efetiva é **entrada**, não observação | ±5% na largura ⇒ sobreposição 3,7%–12,2% (de 8,1%) |
| Ruído de posição infla sobreposição e distorce a região ≥ 2× | σ = 1 m ⇒ 22% (verdade ≈ 8%) |
| Viés de projeção UTM em área | +0,11% neste local |
| Sem modelo de jato/sobreposição de bicos/controle de seção/deriva | área "tratada" é geométrica, não de dose |
| Sem suavização de trajetória (Kalman/Savitzky–Golay) | mitigação futura para o ruído |

## 9.5 Anomalias

- Limiares dependem do ruído do sensor real; o padrão (15%) foi escolhido em dados **simulados**.
- IsolationForest: `contamination` define o número de alarmes; atípico ≠ defeituoso; não roda no streaming.
- Sem rótulos reais não há como estimar precisão em campo.
- Persistência mínima atrasa a **emissão** do alerta (≥ 20 s).

## 9.6 Operação e infraestrutura

| Limitação | Impacto |
|---|---|
| **Fila de ingestão em memória** | se o processo cair, mensagens ainda não gravadas se perdem; o QoS 1 só protege até o ACK do cliente, não até o commit no banco |
| **Rajada sem ritmo perde mensagens com o Mosquitto padrão** | o teste ponta a ponta publica 3 600 mensagens QoS 1 de uma vez; com `max_queued_messages` padrão (1000) o broker descartou mensagens ("Outgoing messages are being dropped") e só 3 465 chegaram ao banco. Subimos para `max_queued_messages 100000` / `max_inflight_messages 100` e o teste passou 3 vezes seguidas, sem descartes. Foi **uma** mudança de configuração observada em **um** ambiente (Podman emulado); não é prova de capacidade. O simulador real publica com ritmo (`--speedup`) |
| Pipeline ao vivo roda em um processo | sem escala horizontal nem consumidor separado |
| Nenhum teste de carga/benchmark | não afirmamos taxa máxima de mensagens |
| Imagem PostGIS **amd64** emulada em Apple Silicon | mais lenta; troque por imagem multi-arquitetura nativa |
| **Docker Desktop não testado** (só Podman) | compose escrito para ser portátil, mas sem execução comprovada |
| `compose restart mosquitto` falhava (arquivo de senhas já existia) | **corrigido** em `entrypoint.sh` (`rm -f` antes de gerar); verificado com `podman restart` |
| Após recriar o `backend`, é preciso reiniciar o `frontend` | nginx resolve o upstream na subida; senão 502 |
| TimescaleDB não usado | séries muito longas podem exigir particionamento |

## 9.7 Segurança

Existe: JWT HS256, três papéis, scrypt, WebSocket autenticado, MQTT com autenticação e ACL, contêiner sem root, compose sem senhas padrão. **Não existe:** TLS ligado no compose local (cliente MQTT suporta; listener 8883 é recomendação), MFA/SSO, revogação de token, limitação de tentativas de login, trilha de auditoria de acesso, rotação de segredos, teste de intrusão, endurecimento do nginx (cabeçalhos CSP/HSTS). **Nunca** desabilitamos verificação de certificado. Os testes E2E têm senhas locais de teste como padrão; não use esses valores fora de máquina de desenvolvimento.

## 9.8 Reprodutibilidade

- Dependências fixadas em `requirements*.txt`, mas `playwright`, `matplotlib`, `nbconvert`, `ipykernel`, `openpyxl` estão sem versão em `requirements-dev.txt`.
- Não há `pyproject.toml`; use `PYTHONPATH=.`.
- Os notebooks foram executados com a semente 7; recalcular com outra semente muda os números (a conclusão qualitativa deve se manter, mas não verificamos para outras sementes).

## 9.9 Uso responsável

Não use alertas ou estimativas deste sistema como única base para: decisão de segurança, cobrança de serviço entre terceiros, conformidade regulatória ou laudo. Para qualquer um desses usos, valide com dados reais, referência geodésica e as partes interessadas.
