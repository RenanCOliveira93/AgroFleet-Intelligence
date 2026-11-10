"""ISOXML / TASKDATA (ISO 11783-10): NÃO IMPLEMENTADO de propósito.

Para ler time logs (TLG*.BIN + TLG*.XML) é preciso (1) um parser compatível com a versão do arquivo (existem a biblioteca
Python ``isoxml`` e o ``farmingpy.TimeLogData``; nenhum foi avaliado aqui), (2) amostras VÁLIDAS de um terminal real para
validar o mapeamento DDI -> campo canônico e (3) tratamento da codificação binária dos time logs por fabricante.
Nesta entrega não havia amostra válida verificável; escrever o parser sem ela seria produzir código não validado que
parece funcionar. Ver docs/GUIA_TECNICO.md (seção 5.6) para o caminho de implementação.
"""
from .base import NotImplementedAdapter

STATUS = "not_implemented"
REASON = "sem amostra TASKDATA/TLG válida e sem parser validado; ver docs/GUIA_TECNICO.md (seção 5.6)"


def read_isoxml(*_a, **_k):
    raise NotImplementedAdapter(f"ISOXML/TASKDATA não implementado: {REASON}")
