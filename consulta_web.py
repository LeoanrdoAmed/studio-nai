import os
import re
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime, timedelta
from anticaptchaofficial.turnstileproxyless import *
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import time

LOGIN_URL = "https://www.weblocacao.com.br/authentication/login"
CLOSING_URL = "https://www.weblocacao.com.br/Order/Closing"
DETAILS_URL_TMPL = "https://www.weblocacao.com.br/order/closingdetails?date={ini}&idStore=0&dateEnd={fim}"

EMAIL = os.getenv("WEB_EMAIL")
PASSWORD = os.getenv("WEB_PASSWORD")
ANTICAPTCHA_KEY = os.getenv("ANTICAPTCHA_KEY")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"

def require_env(name, value):
    if not value:
        raise RuntimeError(f"Environment variable {name} is required.")
    return value

def solve_captcha(page):
    """Resolve CAPTCHA usando Anti-Captcha (baseado no seu script)"""
    print("🔄 Resolvendo CAPTCHA...")
    
    try:
        # Esperar o widget existir na árvore
        widget = page.locator(".cf-turnstile[data-sitekey]").first
        widget.wait_for(state="attached", timeout=10000)
        
        sitekey = widget.get_attribute("data-sitekey")
        print(f"🔑 SITEKEY: {sitekey}")

        solver = turnstileProxyless()
        solver.set_verbose(1)
        solver.set_key(require_env("ANTICAPTCHA_KEY", ANTICAPTCHA_KEY))
        solver.set_website_url(LOGIN_URL)
        solver.set_website_key(sitekey)
        
        resposta = solver.solve_and_return_solution()
        if resposta != 0:
            print("✅ CAPTCHA resolvido com sucesso!")
            return resposta
        else:
            print(f"❌ Erro ao resolver CAPTCHA: {solver.err_string}")
            return None
            
    except Exception as e:
        print(f"⚠️ Erro no processo de CAPTCHA: {e}")
        return None

def login_and_build_session(email: str, password: str, headless: bool = False) -> requests.Session:
    """
    Login com Playwright e retorna sessão autenticada
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, timeout=60000)
        context = browser.new_context(user_agent=UA, viewport={'width': 1280, 'height': 800})
        page = context.new_page()
        page.set_default_timeout(30000)

        print("🌐 Acessando página de login...")
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            print("✅ Página carregada com sucesso!")
        except PWTimeout:
            print("⚠️ Timeout no carregamento, tentando continuar...")
        
        time.sleep(3)

        # Resolver CAPTCHA antes de preencher os campos
        print("🔍 Verificando e resolvendo CAPTCHA...")
        captcha_token = solve_captcha(page)
        
        if captcha_token:
            # Injeta o token no formulário
            page.evaluate(f"""
            (function() {{
                // Remove qualquer token anterior
                document.querySelectorAll('input[name="cf-turnstile-response"]').forEach(el => el.remove());
                
                // Cria novo input com o token
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'cf-turnstile-response';
                input.value = '{captcha_token}';
                
                // Adiciona ao formulário
                const form = document.querySelector('form');
                if (form) {{
                    form.appendChild(input);
                    console.log('Token CAPTCHA injetado com sucesso');
                }}
            }})();
            """)
            print("✅ Token CAPTCHA injetado no formulário")
            time.sleep(2)
        else:
            print("⚠️ Não foi possível resolver CAPTCHA automaticamente")
            if not headless:
                input("⏸️ Resolva o CAPTCHA manualmente e pressione Enter...")
                time.sleep(3)

        # Preencher campos de login
        print("📝 Preenchendo campos de login...")
        
        # Campo de email/login (name="Name")
        email_field = page.query_selector("input[name='Name']")
        if email_field:
            email_field.fill(email)
            print("✅ Email preenchido")
        else:
            raise RuntimeError("❌ Campo de email não encontrado")

        time.sleep(1)

        # Campo de senha (name="password")
        password_field = page.query_selector("input[name='password']")
        if password_field:
            password_field.fill(password)
            print("✅ Senha preenchida")
        else:
            raise RuntimeError("❌ Campo de senha não encontrado")

        time.sleep(1)

        # Clicar no botão de login
        print("🖱️ Clicando no botão de login...")
        
        login_clicked = False
        button_selectors = [
            "button:has-text('Login')",
            "button:has-text('Entrar')",
            "input[type='submit']",
            "button[type='submit']",
            ".btn-primary",
            ".btn-login"
        ]

        for selector in button_selectors:
            try:
                button = page.query_selector(selector)
                if button and button.is_visible():
                    button.click()
                    print(f"✅ Botão clicado: {selector}")
                    login_clicked = True
                    break
            except Exception as e:
                continue

        if not login_clicked:
            print("⚠️ Botão não encontrado, tentando submit via JavaScript...")
            page.evaluate("""
                const form = document.querySelector('form');
                if (form) form.submit();
            """)

        # Aguardar login
        print("⏳ Aguardando login...")
        try:
            page.wait_for_url(re.compile(r"weblocacao\.com\.br/(?!authentication/login)"), timeout=15000)
            print("✅ Login bem-sucedido!")
        except PWTimeout:
            print("⚠️ Timeout no redirecionamento, verificando estado...")
            current_url = page.url
            print(f"URL atual: {current_url}")
            
            if "authentication/login" not in current_url:
                print("✅ Parece estar logado")
            else:
                print("❌ Ainda na página de login")
                # Tentar navegar para página destino
                try:
                    page.goto(CLOSING_URL, wait_until="domcontentloaded", timeout=10000)
                    print("➡️ Navegado diretamente para página destino")
                except:
                    raise RuntimeError("Falha completa no login")

        # Extrair cookies para sessão requests
        print("🍪 Extraindo cookies...")
        cookies = context.cookies()
        
        session = requests.Session()
        session.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Referer": CLOSING_URL,
        })

        # Adicionar cookies à sessão
        for cookie in cookies:
            session.cookies.set(
                name=cookie['name'],
                value=cookie['value'],
                domain=cookie['domain'].lstrip('.') if cookie['domain'].startswith('.') else cookie['domain'],
                path=cookie['path']
            )

        # Validar sessão
        print("🔍 Validando sessão...")
        try:
            test_response = session.get(CLOSING_URL, timeout=10)
            if test_response.status_code == 200 and "authentication/login" not in test_response.url:
                print("✅ Sessão validada com sucesso!")
            else:
                print(f"⚠️ Sessão pode não estar autenticada - Status: {test_response.status_code}")
                print(f"📋 URL: {test_response.url}")
                
        except Exception as e:
            print(f"⚠️ Erro ao validar sessão: {e}")

        browser.close()
        return session

def get_date_ranges():
    """Retorna os intervalos de datas para consulta: ano anterior completo e ano atual até o mês atual"""
    agora = datetime.now()
    ano_atual = agora.year
    mes_atual = agora.month
    ano_anterior = ano_atual - 1
    
    date_ranges = []
    
    # Ano anterior completo (todos os meses)
    for mes in range(1, 13):
        data_inicio = f"01/{mes:02d}/{ano_anterior}"
        ultimo_dia = (datetime(ano_anterior, mes, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        data_fim = ultimo_dia.strftime("%d/%m/%Y")
        date_ranges.append((data_inicio, data_fim, mes, ano_anterior))
    
    # Ano atual até o mês atual
    for mes in range(1, mes_atual + 1):
        data_inicio = f"01/{mes:02d}/{ano_atual}"
        ultimo_dia = (datetime(ano_atual, mes, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        data_fim = ultimo_dia.strftime("%d/%m/%Y")
        date_ranges.append((data_inicio, data_fim, mes, ano_atual))
    
    return date_ranges

def consultar_web(session: requests.Session):
    """Consulta os dados para todos os períodos necessários"""
    print("📊 Iniciando consulta de dados...")
    
    # Obter intervalos de datas
    date_ranges = get_date_ranges()
    print(f"📅 Períodos a consultar: {len(date_ranges)} meses")
    
    dfs = []
    
    for data_inicio, data_fim, mes, ano in date_ranges:
        print(f"🔍 Consultando: {data_inicio} a {data_fim}")
        
        url = DETAILS_URL_TMPL.format(
            ini=quote(data_inicio),
            fim=quote(data_fim)
        )
        
        # Fazer requisição
        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
            
            print(f"   📋 Status: {resp.status_code}")
            
            # Verificar se foi redirecionado para login
            if "authentication/login" in resp.url:
                print("   ❌ Redirecionado para login - sessão expirada")
                break
            
            if resp.status_code == 200:
                # Processar dados
                soup = BeautifulSoup(resp.text, "html.parser")
                table = soup.find("table")
                
                if table:
                    headers = [th.get_text(strip=True) for th in table.find_all("th")]
                    rows = []
                    for tr in table.find_all("tr")[1:]:
                        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                        if cells:
                            rows.append(cells)
                    
                    if rows:
                        df = pd.DataFrame(rows, columns=headers)
                        df["mes"] = mes
                        df["ano"] = ano
                        df["periodo_consulta"] = f"{data_inicio} a {data_fim}"
                        dfs.append(df)
                        print(f"   ✅ {len(rows)} registros encontrados")
                    else:
                        print("   ⚠️ Tabela vazia")
                else:
                    print("   ⚠️ Tabela não encontrada")
            else:
                print(f"   ❌ Erro HTTP: {resp.status_code}")
                
        except Exception as e:
            print(f"   ❌ Erro na consulta: {e}")
        
        # Pequena pausa entre consultas para não sobrecarregar o servidor
        time.sleep(1)
    
    # Consolidar todos os dados
    if dfs:
        df_final = pd.concat(dfs, ignore_index=True)
        print(f"✅ Dados consolidados: {len(df_final)} registros no total")
        
        # Salvar resultados - SEMPRE com o mesmo nome
        base_dir = os.path.dirname(os.path.abspath(__file__))
        dados_dir = os.path.join(base_dir, "dados")
        os.makedirs(dados_dir, exist_ok=True)
        
        # Nome fixo do arquivo
        output_path = os.path.join(dados_dir, "resultado_web.json")
        df_final.to_json(output_path, orient="records", force_ascii=False, indent=2)
        print(f"💾 JSON salvo em: {output_path}")
        
        # Também salvar como CSV com nome fixo
        csv_path = os.path.join(dados_dir, "resultado_web.csv")
        df_final.to_csv(csv_path, index=False, encoding='utf-8')
        print(f"💾 CSV salvo em: {csv_path}")
        
        # Mostrar resumo
        print("\n📊 RESUMO DA CONSULTA:")
        print(f"Total de registros: {len(df_final)}")
        print(f"Período coberto: {len(date_ranges)} meses")
        print(f"Anos: {df_final['ano'].unique().tolist()}")
        
        return df_final
    else:
        print("❌ Nenhum dado foi encontrado em nenhum dos períodos consultados")
        return None

def web_consulta():
    try:
        print("=== INICIANDO PROCESSO ===")
        print(f"📅 Data atual: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        
        email = require_env("WEB_EMAIL", EMAIL)
        password = require_env("WEB_PASSWORD", PASSWORD)
        sess = login_and_build_session(email, password, headless=False)
        if sess:
            resultado = consultar_web(sess)
            
        print("=== PROCESSO CONCLUÍDO ===")
        
    except Exception as e:
        print(f"❌ ERRO: {e}")
        import traceback
        traceback.print_exc()



if __name__ == "__main__":
    web_consulta()
