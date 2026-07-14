import pyotp
import json
import time
from playwright.sync_api import sync_playwright
import os
from pathlib import Path

# ─── Inicialização de paths ───
# Garante que o cwd (diretório de trabalho) seja sempre a raiz do projeto,
# i.e. a pasta dash_way_group onde estão app.py, /scripts, /dados, /uploads, etc.
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

def autenticar_contaazul(email, senha, totp_secret):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://login.contaazul.com/#/", wait_until="domcontentloaded")

        # Etapa 1: E-mail
        page.wait_for_selector('input[type="email"]', timeout=20000)
        page.fill('input[type="email"]', email)
        page.click('button[type="submit"]')

        # Etapa 2: Senha
        page.wait_for_selector('input[type="password"]', timeout=15000)
        page.fill('input[type="password"]', senha)
        page.click('button[type="submit"]')

        # Etapa 3: 2FA
        try:
            page.wait_for_selector('input[placeholder*="código"], input[type="text"]', timeout=15000)
            codigo = pyotp.TOTP(totp_secret).now()
            print(f"Inserindo código 2FA: {codigo}")
            page.fill('input[placeholder*="código"], input[type="text"]', codigo)

            # Clicar no botão verde "Autenticar"
            page.click('button:has-text("Autenticar")')

        except Exception as e:
            print("⚠️ Campo de código 2FA não encontrado.")
            with open("erro_2fa.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            raise e

        # Espera dashboard
        page.wait_for_url("**/visao-geral", timeout=20000)
        print("✅ Login realizado com sucesso.")

        cookies = context.cookies()
        auth_token = next((c['value'] for c in cookies if c['name'] == 'auth-token'), None)
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

        browser.close()

        headers = {
            'accept': 'application/json',
            'content-type': 'application/json',
            'origin': 'https://app.contaazul.com',
            'referer': 'https://app.contaazul.com/',
            'user-agent': 'Mozilla/5.0',
            'x-authorization': auth_token,
            'Cookie': cookie_header
        }

        with open("headers_contaazul.json", "w", encoding="utf-8") as f:
            json.dump(headers, f, indent=2, ensure_ascii=False)
        print("✅ Headers salvos com sucesso.")

        return headers
