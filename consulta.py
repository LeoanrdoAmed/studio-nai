from anticaptchaofficial.turnstileproxyless import *
from playwright.sync_api import sync_playwright
import os



link = "https://www.weblocacao.com.br/authentication/login"
ANTICAPTCHA_KEY = os.getenv("ANTICAPTCHA_KEY")

if not ANTICAPTCHA_KEY:
    raise RuntimeError("Environment variable ANTICAPTCHA_KEY is required.")



with sync_playwright() as p:
    page = p.chromium.launch(headless=True).new_page()
    page.goto(link)

    # espere o widget existir na árvore
    widget = page.locator(".cf-turnstile[data-sitekey]").first
    widget.wait_for(state="attached")

    sitekey = widget.get_attribute("data-sitekey")
    print("SITEKEY:", sitekey)

    solver = turnstileProxyless()
    solver.set_verbose(1)
    solver.set_key(ANTICAPTCHA_KEY)
    solver.set_website_url(link)
    solver.set_website_key(sitekey)
    resposta = solver.solve_and_return_solution()
    if resposta != 0:
        print("Resposta:", resposta)
    else:
        print(solver.err_string)
