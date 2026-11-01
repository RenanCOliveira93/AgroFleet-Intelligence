"""E2E em navegador real (Chromium) contra a pilha no ar (podman/docker compose up). Marcado 'e2e'.
Variáveis: AGROFLEET_E2E_URL (padrão http://localhost:8081), AGROFLEET_E2E_{ADMIN,ANALYST,VIEWER}_PASSWORD."""
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e
URL = os.environ.get("AGROFLEET_E2E_URL", "http://localhost:8081")
PW = {"admin": os.environ.get("AGROFLEET_E2E_ADMIN_PASSWORD", "admin-local-1"), "analyst": os.environ.get("AGROFLEET_E2E_ANALYST_PASSWORD", "analyst-local-1"),
      "viewer": os.environ.get("AGROFLEET_E2E_VIEWER_PASSWORD", "viewer-local-1")}
IMG = Path(__file__).resolve().parents[2] / "docs" / "img"
ROOT = Path(__file__).resolve().parents[2]


def api_token(user="admin"):
    return requests.post(f"{URL}/api/auth/login", json={"username": user, "password": PW[user]}, timeout=20).json()["access_token"]


@pytest.fixture(scope="module", autouse=True)
def clean_state():
    h = {"Authorization": f"Bearer {api_token()}"}
    for o in ("simulated", "real", "unknown"):
        requests.delete(f"{URL}/api/data", params={"origin": o}, headers=h, timeout=60)
    yield


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        yield b
        b.close()


def login(browser, user="admin"):
    ctx = browser.new_context(viewport={"width": 1500, "height": 950})
    pg = ctx.new_page()
    pg.goto(URL)
    pg.fill("input[name=username]", user)
    pg.fill("input[name=password]", PW[user])
    pg.click("button[type=submit]")
    pg.wait_for_selector("[data-testid=provenance-banner]")
    return pg


def tab(pg, name):
    pg.get_by_role("tab", name=name).click()


def test_login_invalido(browser):
    pg = browser.new_context().new_page()
    pg.goto(URL)
    pg.fill("input[name=username]", "admin"); pg.fill("input[name=password]", "errada"); pg.click("button[type=submit]")
    expect(pg.get_by_role("alert")).to_contain_text("credenciais inválidas")


def test_fluxo_completo_com_dados_simulados(browser):
    pg = login(browser)
    expect(pg.get_by_test_id("provenance-banner")).to_contain_text("Sem dados")
    tab(pg, "Sobre / limites")
    rows = pg.get_by_test_id("adapters").locator("tr", has_text="isoxml")
    expect(rows).to_contain_text("NÃO implementada")
    pg.get_by_test_id("load-demo").click()
    expect(pg.get_by_text(re.compile(r"11331 amostras SIMULADAS"))).to_be_visible(timeout=120000)

    # Frota: 4 máquinas, todas rotuladas SIMULADO, marcadores no mapa
    tab(pg, "Frota")
    expect(pg.get_by_test_id("provenance-banner")).to_contain_text("TODOS OS DADOS EXIBIDOS SÃO SIMULADOS")
    expect(pg.locator(".mcard")).to_have_count(4)
    expect(pg.get_by_test_id("origin-simulated")).to_have_count(4)
    pg.wait_for_selector(".mk", timeout=20000)
    assert pg.locator(".mk").count() >= 3        # SIM-03 pode estar sem posição no último instante
    pg.wait_for_timeout(2500)
    pg.screenshot(path=str(IMG / "ui_frota.png"))

    # Filtro de origem: "real" não tem nada
    pg.get_by_role("button", name="REAL (declarado)").click()
    expect(pg.get_by_text("Nenhuma máquina para o filtro atual.")).to_be_visible()
    pg.get_by_role("button", name="Todas as origens").click()
    expect(pg.locator(".mcard")).to_have_count(4)

    # Reprodução: ir ao instante de perda de GNSS do SIM-03 (10:08:30 + 60 s) e ver o aviso
    tab(pg, "Reprodução")
    pg.wait_for_selector("[data-testid=slider]")
    t0 = pg.get_by_test_id("slider").get_attribute("min")
    target = int(datetime(2026, 3, 10, 10, 9, 30, tzinfo=timezone.utc).timestamp())   # 10:08:30 (início da perda) + 60 s
    pg.get_by_test_id("slider").evaluate("(el, v) => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; s.call(el, v); el.dispatchEvent(new Event('input', {bubbles:true})); }", str(target))
    expect(pg.get_by_test_id("nogps")).to_contain_text("SIM-03")
    expect(pg.get_by_test_id("clock")).to_contain_text("2026-03-10 10:09:")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=str(IMG / "ui_reproducao_sem_gnss.png"))
    assert int(t0) < target

    # Cobertura: SIM-01 no TALHAO-A
    tab(pg, "Cobertura")
    pg.wait_for_selector("[data-testid=field-select]")
    pg.get_by_test_id("field-select").select_option("TALHAO-A")
    expect(pg.get_by_test_id("coverage-table")).to_contain_text("Área única coberta")
    unique = pg.get_by_test_id("unique-ha").inner_text()
    val = float(unique.replace(" ha", "").replace(",", "."))
    assert 20.4 < val < 21.2, unique
    pg.wait_for_timeout(1500)
    pg.screenshot(path=str(IMG / "ui_cobertura.png"))

    # Alertas + avaliação do simulador
    tab(pg, "Alertas")
    expect(pg.get_by_test_id("alerts-table")).to_contain_text("Desvio de vazão")
    expect(pg.get_by_test_id("alerts-table")).to_contain_text("Perda de GNSS")
    expect(pg.get_by_test_id("sim-eval")).to_contain_text("100 %")
    pg.screenshot(path=str(IMG / "ui_alertas.png"))

    # Relatórios por talhão
    tab(pg, "Relatórios")
    pg.get_by_test_id("group-by").select_option("field")
    expect(pg.get_by_test_id("report-table").locator("tbody tr")).to_have_count(4)
    pg.screenshot(path=str(IMG / "ui_relatorios.png"))

    # ROI
    tab(pg, "ROI")
    expect(pg.get_by_test_id("roi-example-warning")).to_contain_text("ILUSTRATIVO")
    pg.get_by_test_id("roi-run").click()
    expect(pg.get_by_test_id("tornado")).to_be_visible()
    expect(pg.get_by_test_id("roi-grid")).to_be_visible()
    pg.get_by_role("button", name=re.compile("Preencher produtividade")).click()
    expect(pg.locator(".prov", has_text="measured_telemetry").first).to_be_visible()
    pg.screenshot(path=str(IMG / "ui_roi.png"), full_page=True)


def test_upload_validacao_e_commit(browser):
    pg = login(browser, "analyst")
    tab(pg, "Upload")
    bad = ROOT / "payload_examples" / "telemetry_sem_fuso.csv"
    good = ROOT / "payload_examples" / "telemetry_minimal.csv"
    pg.get_by_test_id("file").set_input_files(str(bad))
    pg.get_by_test_id("origin").select_option("real")
    pg.get_by_test_id("validate").click()
    expect(pg.get_by_test_id("quality")).to_contain_text("bloqueante")
    expect(pg.get_by_test_id("quality")).to_contain_text("fuso")
    pg.get_by_test_id("file").set_input_files(str(good))
    pg.get_by_test_id("commit").click()
    expect(pg.get_by_test_id("quality")).to_contain_text("gravadas: 6")
    pg.screenshot(path=str(IMG / "ui_upload.png"), full_page=True)
    pg.get_by_role("tab", name="Frota").click()
    expect(pg.get_by_test_id("provenance-banner")).to_contain_text("origens misturadas")
    expect(pg.get_by_test_id("origin-real")).to_have_count(1)
    # limpeza para não vazar para outros testes
    requests.delete(f"{URL}/api/data", params={"origin": "real"}, headers={"Authorization": f"Bearer {api_token()}"})


def test_viewer_nao_tem_acoes_de_escrita(browser):
    pg = login(browser, "viewer")
    tab(pg, "Alertas")
    expect(pg.get_by_test_id("recompute")).to_have_count(0)
    tab(pg, "Upload")
    expect(pg.get_by_text("Upload requer o papel analista ou admin.")).to_be_visible()
    tab(pg, "Sobre / limites")
    expect(pg.get_by_test_id("load-demo")).to_have_count(0)
