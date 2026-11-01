"""Executa as soluções de docs/pt-BR/10_exercicios.md (blocos precedidos por `<!-- run -->`).
Se os números do projeto mudarem, o capítulo precisa ser atualizado: este teste falha de propósito."""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "pt-BR" / "10_exercicios.md"
BLOCKS = re.findall(r"<!-- run -->\n```python\n(.*?)```", DOC.read_text(encoding="utf-8"), flags=re.S)


def test_ha_10_solucoes():
    assert len(BLOCKS) == 9      # exercício 10 é projeto aberto (sem código)


@pytest.mark.parametrize("i", range(len(BLOCKS)))
def test_solucao(i, monkeypatch):
    monkeypatch.chdir(ROOT)
    exec(compile(BLOCKS[i], f"exercicio_{i + 1}", "exec"), {"__name__": "__main__"})
