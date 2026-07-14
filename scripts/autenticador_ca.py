import json
import time
from pathlib import Path

import pyotp
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

def encontrar_raiz_projeto(start):
    for path in [start, *start.parents]:
        if (path / "app.py").exists():
            return path
    return start.parent


PROJECT_ROOT = encontrar_raiz_projeto(Path(__file__).resolve().parent)
DATA_DIR = PROJECT_ROOT / "dados"


def _cookie_header(cookies):
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)


def _cookie_value(cookies, *names):
    wanted = set(names)
    for cookie in cookies:
        if cookie.get("name") in wanted:
            return cookie.get("value")
    return None


def _wait_for_token(context, timeout_seconds=20):
    deadline = time.time() + timeout_seconds
    cookies = context.cookies()
    token = _cookie_value(
        cookies,
        "ca-pro-auth-token-current",
        "auth-token",
        "auth-token-pd",
        "redirect_token",
    )

    while not token and time.time() < deadline:
        time.sleep(1)
        cookies = context.cookies()
        token = _cookie_value(
            cookies,
            "ca-pro-auth-token-current",
            "auth-token",
            "auth-token-pd",
            "redirect_token",
        )
    return token, cookies


def _aguardar_painel(page, timeout=15000):
    try:
        page.wait_for_url("**/visao-geral", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        return False


def autenticar_contaazul(email, senha, totp_secret):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://login.contaazul.com/#/", wait_until="domcontentloaded")

        page.wait_for_selector('input[type="email"]', timeout=20000)
        page.fill('input[type="email"]', email)
        page.click('button[type="submit"]')

        page.wait_for_selector('input[type="password"]', timeout=20000)
        page.fill('input[type="password"]', senha)
        page.click('button[type="submit"]')

        if not _aguardar_painel(page):
            try:
                page.wait_for_selector('input[type="text"]', timeout=20000)
                codigo = pyotp.TOTP(totp_secret).now()
                page.fill('input[type="text"]', codigo)
                page.keyboard.press("Enter")
            except Exception as exc:
                (DATA_DIR / "erro_2fa.html").write_text(page.content(), encoding="utf-8")
                raise RuntimeError("Campo de codigo 2FA nao encontrado ou falha no 2FA.") from exc

            if not _aguardar_painel(page, timeout=30000):
                page.wait_for_load_state("networkidle", timeout=30000)

        try:
            page.goto("https://pro.contaazul.com/", wait_until="networkidle", timeout=60000)
        except PlaywrightTimeoutError:
            page.goto("https://pro.contaazul.com/", wait_until="domcontentloaded", timeout=60000)

        token, cookies = _wait_for_token(context)
        cookie_header = _cookie_header(cookies)
        browser.close()

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://pro.contaazul.com",
            "referer": "https://pro.contaazul.com/",
            "user-agent": "Mozilla/5.0",
            "x-authorization": token or "",
            "Cookie": cookie_header,
        }

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "headers_contaazul.json").write_text(
            json.dumps(headers, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print("Headers Conta Azul Pro salvos com sucesso.")
        return headers
