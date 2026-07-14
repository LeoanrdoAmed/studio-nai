import os
import re
import json
import time
import logging
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from anticaptchaofficial.turnstileproxyless import turnstileProxyless

# ============== LOGGING ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('web_scraping.log', encoding='utf-8'),
              logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ============== CONSTANTES ==============
LOGIN_URL = "https://www.weblocacao.com.br/authentication/login"
CLOSING_URL = "https://www.weblocacao.com.br/Order/Closing"
DETAILS_URL_TMPL = "https://www.weblocacao.com.br/order/closingdetails?date={ini}&idStore=0&dateEnd={fim}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")

def require_env(name, value):
    if not value:
        raise RuntimeError(
            f"{name} ausente. Configure a variavel de ambiente ou dados/credenciais_weblocacao.json."
        )
    return value

# DUMPS sempre em ../dados (irmã de scripts)
SCRIPTS_DIR = Path(__file__).resolve().parent
DADOS_DIR = SCRIPTS_DIR.parent / "dados"
DADOS_DIR.mkdir(parents=True, exist_ok=True)
WEBLOCACAO_CREDENTIALS_PATH = DADOS_DIR / "credenciais_weblocacao.json"
LAST_LOGIN_HTML   = DADOS_DIR / "last_login.html"
LAST_LOGIN_PNG    = DADOS_DIR / "login.png"
LAST_CLOSING_HTML = DADOS_DIR / "last_closingdetails.html"
LAST_CLOSING_PNG  = DADOS_DIR / "closing.png"


def _read_weblocacao_credentials_file():
    if not WEBLOCACAO_CREDENTIALS_PATH.exists() or WEBLOCACAO_CREDENTIALS_PATH.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(WEBLOCACAO_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Credenciais do Web Locacao invalidas em {WEBLOCACAO_CREDENTIALS_PATH}: {exc}")
        return {}
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), {})
    return payload if isinstance(payload, dict) else {}


def _pick_credential(source, *keys):
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            return value
    return None


def load_weblocacao_credentials():
    file_credentials = _read_weblocacao_credentials_file()
    email = os.getenv("WEB_EMAIL") or _pick_credential(
        file_credentials, "email", "login", "usuario", "user", "WEB_EMAIL"
    )
    password = os.getenv("WEB_PASSWORD") or _pick_credential(
        file_credentials, "senha", "password", "WEB_PASSWORD"
    )
    anticaptcha_key = os.getenv("ANTICAPTCHA_KEY") or _pick_credential(
        file_credentials, "anticaptcha_key", "anti_captcha_key", "captcha_key", "ANTICAPTCHA_KEY"
    )
    return email, password, anticaptcha_key


def refresh_weblocacao_credentials():
    global EMAIL, PASSWORD, ANTICAPTCHA_KEY
    EMAIL, PASSWORD, ANTICAPTCHA_KEY = load_weblocacao_credentials()
    return EMAIL, PASSWORD, ANTICAPTCHA_KEY


EMAIL, PASSWORD, ANTICAPTCHA_KEY = refresh_weblocacao_credentials()

# ============== HELPERS DUMP ==============
def _save_dump(page, html_path: Path, png_path: Path, label: str):
    try:
        html_path.write_text(page.content(), encoding='utf-8'); logger.info(f"[SAVE] OK → {html_path}")
    except Exception as e:
        logger.warning(f"[SAVE] ERRO HTML {label}: {e}")
    try:
        page.screenshot(path=png_path.as_posix(), full_page=True); logger.info(f"[SAVE] OK → {png_path}")
    except Exception as e:
        logger.warning(f"[SAVE] ERRO PNG {label}: {e}")

# ============== CAPTCHA ==============
def solve_captcha_smart(page, quick_ms=6000):
    """1) tenta token nativo; 2) fallback Anti-Captcha."""
    # fast-path: input preenchido pelo Turnstile
    try:
        page.wait_for_selector("input[name='cf-turnstile-response']", timeout=quick_ms)
        page.wait_for_function(
            "document.querySelector('input[name=\"cf-turnstile-response\"]')?.value?.length > 10",
            timeout=quick_ms
        )
        token = page.eval_on_selector("input[name='cf-turnstile-response']", "el => el.value")
        if token:
            logger.info("Token nativo do Turnstile capturado.")
            return token
    except Exception:
        logger.info("Fast-path não preencheu a tempo; usando Anti-Captcha...")

    # fallback Anti-Captcha
    try:
        widget = page.locator(".cf-turnstile[data-sitekey]").first
        widget.wait_for(state="attached", timeout=15000)
        sitekey = widget.get_attribute("data-sitekey")
        logger.info(f"SITEKEY: {sitekey}")

        solver = turnstileProxyless()
        solver.set_verbose(0)
        solver.set_key(require_env("ANTICAPTCHA_KEY", ANTICAPTCHA_KEY))
        solver.set_website_url(LOGIN_URL)
        solver.set_website_key(sitekey)

        token = solver.solve_and_return_solution()
        if token == 0:
            logger.error(f"Anti-Captcha falhou: {solver.err_string}")
            return None
        return token
    except Exception as e:
        logger.error(f"Erro no processo de CAPTCHA: {e}")
        return None

def apply_turnstile_token(page, token: str):
    """Injeta token no form certo + dispara callbacks/eventos + habilita botão."""
    page.evaluate("""
        (token)=>{
            const user = document.querySelector('input[name="Name"]');
            const form = user ? user.closest('form') : document.querySelector('form');
            if(form){
                form.querySelectorAll('input[name="cf-turnstile-response"]').forEach(e=>e.remove());
                let inp = document.createElement('input');
                inp.type='hidden'; inp.name='cf-turnstile-response'; inp.value=token;
                form.appendChild(inp);
                // eventos que alguns frameworks usam p/ validar
                inp.dispatchEvent(new Event('input', {bubbles:true}));
                inp.dispatchEvent(new Event('change', {bubbles:true}));
                // chama callback do widget, se houver
                const widget = document.querySelector('.cf-turnstile[data-sitekey]');
                const cbName = widget?.getAttribute('data-callback');
                if(cbName && typeof window[cbName] === 'function'){
                    try { window[cbName](token); } catch(e){}
                }
                // eventos custom
                try {
                    window.dispatchEvent(new CustomEvent('cf-turnstile-response', {detail:{token}}));
                    document.dispatchEvent(new CustomEvent('cf-turnstile-response', {detail:{token}}));
                } catch(e){}
            }
            // desbloqueia possíveis botões desabilitados
            const btns=[...document.querySelectorAll('#btnLogin,button[type="submit"],.btn-login,.btn-primary')];
            btns.forEach(b=>{ b.removeAttribute('disabled'); b.classList.remove('disabled'); b.disabled=false; });
        }
    """, token)

# ============== LOGIN HEADLESS (sem interação) ==============
def login_and_build_session(email: str, password: str, headless: bool = True) -> requests.Session:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            timeout=90000,
            args=['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
                  '--disable-gpu','--disable-software-rasterizer','--disable-extensions']
        )
        context = browser.new_context(
            user_agent=UA,
            viewport={'width': 1366, 'height': 900},
            java_script_enabled=True
        )
        page = context.new_page()
        page.set_default_timeout(60000)

        logger.info("Acessando página de login...")
        try:
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        except PWTimeout:
            logger.warning("Timeout no carregamento, seguindo mesmo assim.")

        # CAPTCHA automático (sem manual)
        logger.info("Resolvendo CAPTCHA (smart, sem intervenção)...")
        token = solve_captcha_smart(page)
        if not token:
            browser.close()
            raise RuntimeError("CAPTCHA não resolvido automaticamente")

        apply_turnstile_token(page, token)

        # Preencher credenciais
        logger.info("Preenchendo campos de login...")
        email_field = page.query_selector("input[name='Name']")
        if not email_field:
            browser.close(); raise RuntimeError("Campo de email não encontrado")
        email_field.fill(email)

        password_field = page.query_selector("input[name='password']")
        if not password_field:
            browser.close(); raise RuntimeError("Campo de senha não encontrado")
        password_field.fill(password)

        # Clicar Login (ou submit JS)
        logger.info("Clicando no botão de login...")
        clicked=False
        for sel in ["#btnLogin","button:has-text('Login')","button:has-text('Entrar')",
                    "input[type='submit']","button[type='submit']", ".btn-primary",".btn-login"]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    clicked=True
                    break
            except Exception:
                pass
        if not clicked:
            page.evaluate("""
                const user = document.querySelector('input[name="Name"]');
                const form = user ? user.closest('form') : document.querySelector('form');
                if(form) form.submit();
            """)

        # DUMP pós-submit
        _save_dump(page, LAST_LOGIN_HTML, LAST_LOGIN_PNG, "login pós-submit")

        # Redirecionamento / acesso direto ao Closing
        logger.info("Aguardando redirecionamento / abrindo Closing...")
        try:
            page.wait_for_url(re.compile(r"weblocacao\\.com\\.br/(?!authentication/login)"), timeout=25000)
        except PWTimeout:
            logger.warning("Sem redirecionamento; tentando ir direto ao Closing.")
        try:
            page.goto(CLOSING_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        _save_dump(page, LAST_CLOSING_HTML, LAST_CLOSING_PNG, "closing")

        # Montar sessão requests com cookies
        logger.info("Extraindo cookies...")
        cookies = context.cookies()
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": CLOSING_URL,
            "Upgrade-Insecure-Requests": "1",
        })
        for c in cookies:
            sess.cookies.set(
                name=c['name'], value=c['value'], domain=c['domain'],
                path=c['path'], secure=c.get('secure', False),
                rest={'HttpOnly': c.get('httpOnly', False)}
            )

        logger.info("Validando sessão (requests)...")
        try:
            r = sess.get(CLOSING_URL, timeout=15)
            if r.status_code == 200 and "authentication/login" not in r.url:
                logger.info("Sessão validada com sucesso!")
            else:
                logger.warning(f"Sessão pode não estar autenticada - Status: {r.status_code}")
        except Exception as e:
            logger.error(f"Erro ao validar sessão: {e}")

        browser.close()
        return sess

# ============== DATAS / COLETA ==============
def get_date_ranges():
    agora = datetime.now()
    ano_atual = agora.year
    mes_atual = agora.month
    ano_ant = ano_atual - 1
    out=[]
    for m in range(1,13):
        di=f"01/{m:02d}/{ano_ant}"
        ultimo=(datetime(ano_ant,m,1)+timedelta(days=32)).replace(day=1)-timedelta(days=1)
        df=ultimo.strftime("%d/%m/%Y"); out.append((di,df,m,ano_ant))
    for m in range(1,mes_atual+1):
        di=f"01/{m:02d}/{ano_atual}"
        ultimo=(datetime(ano_atual,m,1)+timedelta(days=32)).replace(day=1)-timedelta(days=1)
        df=ultimo.strftime("%d/%m/%Y"); out.append((di,df,m,ano_atual))
    return out

def consultar_web(session: requests.Session):
    logger.info("Iniciando consulta de dados...")
    ranges=get_date_ranges()
    logger.info(f"Períodos a consultar: {len(ranges)} meses")
    dfs=[]
    first_dump_done = LAST_CLOSING_HTML.exists()

    for di,df,m,ano in ranges:
        logger.info(f"Consultando: {di} a {df}")
        url=DETAILS_URL_TMPL.format(ini=quote(di), fim=quote(df))
        try:
            resp=session.get(url, timeout=45, allow_redirects=True)
            logger.info(f"Status: {resp.status_code}")

            if not first_dump_done:
                try:
                    LAST_CLOSING_HTML.write_text(resp.text, encoding='utf-8')
                    logger.info(f"[SAVE] OK → {LAST_CLOSING_HTML}")
                except Exception as e:
                    logger.warning(f"[SAVE] ERRO dump closingdetails: {e}")
                first_dump_done=True

            if "authentication/login" in resp.url:
                logger.error("Redirecionado para login - sessão expirada"); break

            if resp.status_code==200:
                soup=BeautifulSoup(resp.text,"html.parser")
                table=soup.find("table")
                if table:
                    headers=[th.get_text(strip=True) for th in table.find_all("th")]
                    rows=[]
                    for tr in table.find_all("tr")[1:]:
                        cells=[td.get_text(strip=True) for td in tr.find_all("td")]
                        if cells: rows.append(cells)
                    if rows:
                        dfp=pd.DataFrame(rows, columns=headers)
                        dfp["mes"]=m; dfp["ano"]=ano; dfp["periodo_consulta"]=f"{di} a {df}"
                        dfs.append(dfp); logger.info(f"{len(rows)} registros encontrados")
                    else:
                        logger.warning("Tabela vazia")
                else:
                    logger.warning("Tabela não encontrada")
            else:
                logger.error(f"Erro HTTP: {resp.status_code}")
        except Exception as e:
            logger.error(f"Erro na consulta: {e}")
        time.sleep(2)

    if dfs:
        final=pd.concat(dfs, ignore_index=True)
        out_json=(DADOS_DIR/"resultado_web.json").as_posix()
        out_csv=(DADOS_DIR/"resultado_web.csv").as_posix()
        final.to_json(out_json, orient="records", force_ascii=False, indent=2); logger.info(f"JSON salvo em: {out_json}")
        final.to_csv(out_csv, index=False, encoding='utf-8'); logger.info(f"CSV salvo em: {out_csv}")
        logger.info("\nRESUMO DA CONSULTA:")
        logger.info(f"Total de registros: {len(final)}")
        logger.info(f"Período coberto: {len(ranges)} meses")
        logger.info(f"Anos: {final['ano'].unique().tolist()}")
        return final
    else:
        logger.error("Nenhum dado foi encontrado em nenhum dos períodos consultados")
        return None

# ============== MAIN ==============
def web_consulta():
    try:
        logger.info("=== INICIANDO PROCESSO ===")
        logger.info(f"Data atual: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        email, password, _anticaptcha_key = refresh_weblocacao_credentials()
        email = require_env("WEB_EMAIL", email)
        password = require_env("WEB_PASSWORD", password)
        sess = login_and_build_session(email, password, headless=True)  # 100% automático
        if sess: resultado = consultar_web(sess)
        logger.info("=== PROCESSO CONCLUÍDO ===")
        return resultado
    except Exception as e:
        logger.error(f"ERRO: {e}", exc_info=True); raise

if __name__ == "__main__":
    web_consulta()
