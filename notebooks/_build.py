"""Helper: monta .ipynb com células Markdown/Código a partir de listas (md/code)."""
import nbformat as nbf


def build(path, cells):
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
    for kind, text in cells:
        nb.cells.append((nbf.v4.new_markdown_cell if kind == "md" else nbf.v4.new_code_cell)(text.strip("\n")))
    nbf.write(nb, path)
    print("escrito", path, len(cells), "células")
