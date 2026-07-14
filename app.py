from flask import Flask, render_template, request
from flask import flash
import pandas as pd
import os
from flask import Flask, render_template, request, redirect, url_for, session
from functools import wraps
from scripts.extrair_auth import extrair_secret_de_uri
from scripts.autenticador_ca import autenticar_contaazul
from datetime import datetime
from datetime import date
from dateutil.relativedelta import relativedelta
from werkzeug.middleware.proxy_fix import ProxyFix


import os
from pathlib import Path

# ─── Inicialização de paths ───
# Garante que o cwd (diretório de trabalho) seja sempre a raiz do projeto,
# i.e. a pasta dash_way_group onde estão app.py, /scripts, /dados, /uploads, etc.
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "troque-esta-chave-fixa")

def env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# 2) cookies em produção por trás de HTTPS (Nginx faz o TLS)
app.config["SESSION_COOKIE_NAME"]   = "studio_sess"
app.config["SESSION_COOKIE_SECURE"] = env_bool("SESSION_COOKIE_SECURE", True)          # mantém True se o acesso externo é https
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"       # use "None" se precisar cross-site (e aí precisa Secure=True)
# Se usar subdomínios, pode destravar o domínio do cookie:
# app.config["SESSION_COOKIE_DOMAIN"] = ".terceirizapro.com"

# 3) confiar nos headers do Nginx (gera URL/cookies corretos)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_logado' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = None
    if request.method == 'POST':
        usuario = request.form['usuario']
        senha = request.form['senha']
        usuarios = carregar_usuarios()

        usuario_encontrado = next((u for u in usuarios if u["usuario"] == usuario and u["senha"] == senha), None)

        if usuario_encontrado:
            session['usuario_logado'] = usuario
            return redirect(url_for('index'))
        else:
            erro = 'Usuário ou senha inválidos.'
    return render_template('login.html', erro=erro)

@app.route('/logout')
def logout():
    session.pop('usuario_logado', None)
    return redirect(url_for('login'))



UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

FAT_CSV = os.path.join(UPLOAD_FOLDER, 'faturamento.csv')
DESP_CSV = os.path.join(UPLOAD_FOLDER, 'despesas.csv')

import json

def carregar_ordem_dos_grupos():
    caminho = os.path.join("dados", "de_para_categorias_mv.json")
    if not os.path.exists(caminho):
        return []
    
    with open(caminho, "r", encoding="utf-8") as f:
        dados = json.load(f)
    
    grupos_unicos = {}
    for item in dados:
        grupo = item.get("Categoria", "").strip()
        ordem = item.get("ordem", 999)
        if grupo and grupo not in grupos_unicos:
            grupos_unicos[grupo] = ordem
    
    grupos_ordenados = sorted(grupos_unicos.items(), key=lambda x: x[1])
    return [g[0] for g in grupos_ordenados]

def gerar_tabela_dre_expandida(df_formatado):
    import os
    import json
    import pandas as pd

    # 🔥 Carregar ordem da DRE (independente do fluxo)
    caminho_ordem = os.path.join('dados', 'ordem_dre.json')
    if os.path.exists(caminho_ordem):
        with open(caminho_ordem, 'r', encoding='utf-8') as f:
            ordem_dre = json.load(f)
    else:
        ordem_dre = []

    # 🔥 Criar dicionário de ordem dos grupos
    mapa_ordem_dre = {item['grupo']: item.get('ordem', 9999) for item in ordem_dre}

    # 🔥 Aplicar ordem fixa da DRE
    df_formatado["Ordem"] = df_formatado["Grupo"].map(mapa_ordem_dre).fillna(9999)
    df_formatado = df_formatado.sort_values(by=["Ordem", "Tipo", "Subgrupo"])

    # 🔥 Identificar colunas de meses
    meses = [col for col in df_formatado.columns if col not in ["Grupo", "Subgrupo", "Tipo", "id_grupo", "Ordem"]
             and not col.endswith("_AV") and not col.endswith("_AH")]

    # 🔥 Definir se é Entrada ou Saída
    df_formatado['Segmento'] = df_formatado['Grupo'].apply(
        lambda g: 'Saída' if g.endswith('(-)') else 'Entrada'
    )

    # 🔥 Ajuste de sinal para Saídas
    mask_saida = df_formatado['Segmento'] == 'Saída'
    df_formatado.loc[mask_saida, meses] = df_formatado.loc[mask_saida, meses] * -1

    # 🔥 Preencher coluna 'Loja'
    if "Loja" not in df_formatado.columns:
        df_formatado["Loja"] = ""
    df_formatado["Loja"] = df_formatado["Loja"].ffill()

    # 🔥 Base para cálculo de AV — Receita Bruta como base
    base_por_loja_mes = {}
    receita_bruta_df = df_formatado[
        (df_formatado["Grupo"] == "RECEITA BRUTA (+)") &
        (df_formatado["Subgrupo"] == "Receita Bruta")
    ]
    for _, row in receita_bruta_df.iterrows():
        loja = row["Loja"]
        for mes in meses:
            base_por_loja_mes[(loja, mes)] = row[mes]

    # 🔥 Cálculo de AV (Análise Vertical)
    for mes in meses:
        df_formatado[f"{mes}_AV"] = df_formatado.apply(
            lambda r: r[mes] / base_por_loja_mes.get((r["Loja"], mes), 1)
            if base_por_loja_mes.get((r["Loja"], mes), 0) else 0,
            axis=1
        )

    # 🔥 Cálculo de AH (Análise Horizontal)
    for i in range(1, len(meses)):
        atual, anterior = meses[i], meses[i - 1]
        df_formatado[f"{atual}_AH"] = (
            df_formatado[atual] / df_formatado[anterior].replace(0, 1) - 1
        )

    # 🔥 Calcular Total e Média
    df_formatado["Total"] = df_formatado[meses].sum(axis=1)
    df_formatado["Média"] = df_formatado[meses].mean(axis=1)

    # 🔥 Gerar tabela HTML expansível
    html = '''<div class="d-flex gap-2 mb-2">
      <button class="btn btn-outline-secondary btn-sm" onclick="toggleExpandAll(this)">➕ Expandir Tudo</button>
      <button class="btn btn-outline-secondary btn-sm" onclick="toggleIndicadores()">👁 Exibir AV/AH</button>
    </div>
    <table class='table table-bordered table-hover'>
    <thead><tr><th style="text-align:left;">Classificação</th>'''

    for mes in meses:
        html += f"<th>{mes}</th>"
    html += "<th>Total</th><th>Média</th></tr></thead><tbody>"

    for _, row in df_formatado.iterrows():
        classe_grupo = f"grupo-{row['id_grupo']}"
        is_grupo = row["Tipo"] == "Grupo"
        estilo = "font-weight:bold; background:#f0f0f0;" if is_grupo else ""
        label = (
            f'<button class="btn btn-sm btn-link" onclick="toggleDetalhes(\'grupo-{row["id_grupo"]}\')">[+]</button> {row["Grupo"]}'
            if is_grupo else f"➔ {row['Subgrupo']}"
        )
        visibilidade = "" if is_grupo else "display:none;"

        html += f"<tr{' class=' + classe_grupo if not is_grupo else ''} style='{visibilidade}{estilo}'><td style='padding-left:30px; text-align:left;'>{label}</td>"

        for mes in meses:
            valor = row.get(mes, 0)
            val_str = f"R$ {round(valor):,}".replace(",", "X").replace(".", ",").replace("X", ".")

            av_raw = round(row.get(f"{mes}_AV", 0) * 100)
            ah_raw = round(row.get(f"{mes}_AH", 0) * 100)

            av_str = f"{av_raw}%" if av_raw else ""
            ah_str = f"{ah_raw}%" if ah_raw else ""

            superscript = f"<sup class='indicadores' style='font-size:0.7em; color:#666; display:none;'>({av_str} {ah_str})</sup>" if av_str or ah_str else ""

            html += f"<td>{val_str}{superscript}</td>"

        total_val = f"R$ {round(row['Total']):,}".replace(",", "X").replace(".", ",").replace("X", ".")
        media_val = (
            f"R$ {round(row['Média']):,}".replace(",", "X").replace(".", ",").replace("X", ".")
            if pd.notna(row['Média']) else ""
        )
        html += f"<td>{total_val}</td><td>{media_val}</td>"
        html += "</tr>"

    html += "</tbody></table>"

    html += '''<script>
    function toggleDetalhes(classe) {
        const linhas = document.querySelectorAll("tr." + classe);
        for (const linha of linhas) {
            linha.style.display = linha.style.display === "none" ? "table-row" : "none";
        }
    }
    function toggleExpandAll(button) {
        const linhas = document.querySelectorAll('tr[class^="grupo-"]');
        const algumVisivel = Array.from(linhas).some(l => l.style.display !== 'none');
        const expandir = !algumVisivel;
        linhas.forEach(l => l.style.display = expandir ? 'table-row' : 'none');
        button.innerText = expandir ? '🔽 Recolher Tudo' : '➕ Expandir Tudo';
    }
    function toggleIndicadores() {
        const itens = document.querySelectorAll('.indicadores');
        const hidden = Array.from(itens).every(e => e.style.display === 'none' || e.style.display === '');
        itens.forEach(e => e.style.display = hidden ? 'inline' : 'none');
    }
    </script>'''

    return html



def atualizar_ordem_dre_automaticamente(df):
    import os
    import json

    caminho_ordem = os.path.join('dados', 'ordem_dre.json')

    # Carregar ordem atual, se existir
    if os.path.exists(caminho_ordem):
        with open(caminho_ordem, 'r', encoding='utf-8') as f:
            ordem_atual = json.load(f)
    else:
        ordem_atual = []

    grupos_atuais = [item['grupo'] for item in ordem_atual]

    # Grupos encontrados na base atual
    grupos_na_base = sorted(set(df['Grupo'].dropna()))

    novos_grupos = [g for g in grupos_na_base if g not in grupos_atuais]

    if novos_grupos:
        maior_ordem = max([item['ordem'] for item in ordem_atual], default=0)
        novos_itens = [{'grupo': g, 'ordem': maior_ordem + i + 1} for i, g in enumerate(novos_grupos)]

        # Combinar e ordenar pela ordem atual
        ordem_final = ordem_atual + novos_itens

        with open(caminho_ordem, 'w', encoding='utf-8') as f:
            json.dump(ordem_final, f, ensure_ascii=False, indent=2)

        print(f"✅ Novos grupos adicionados na ordem_dre.json: {novos_grupos}")
    else:
        print("✅ Nenhum novo grupo encontrado na base DRE.")

@app.route('/studio/data_consulta_web')
@login_required
def data_consulta_web():
    import os, json
    path = os.path.join(app.root_path, 'dados', 'consulta_web.json')
    if not os.path.exists(path):
        return jsonify({"error": "Arquivo consulta_web.json não encontrado."}), 404
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return jsonify(data)

@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    lojas_selecionadas = request.form.getlist("lojas[]")
    acao = request.form.get("acao")
    periodo_inicio = request.form.get("periodo_inicio")
    periodo_fim = request.form.get("periodo_fim")
    fat_file = request.files.get("faturamento")
    desp_file = request.files.get("despesas")

    lojas = []
    if os.path.exists(FAT_CSV):
        try:
            df_temp = pd.read_csv(FAT_CSV)
            lojas = sorted(df_temp["Loja"].dropna().unique().tolist())
        except Exception as e:
            print("Erro ao carregar lojas:", e)

    if acao == "limpar":
        lojas = []
        if os.path.exists(FAT_CSV):
            df_temp = pd.read_csv(FAT_CSV)
            lojas = df_temp["Loja"].dropna().unique().tolist()
        return render_template("index.html", tabela_html_dre='', lojas=lojas, lojas_selecionadas=[], request_form={})

    if acao == "upload":
        if fat_file and fat_file.filename != '':
            pd.read_excel(fat_file).to_csv(FAT_CSV, index=False)
        if desp_file and desp_file.filename != '':
            pd.read_excel(desp_file).to_csv(DESP_CSV, index=False)
        return render_template("index.html", tabela_html_dre='', lojas=[], lojas_selecionadas=[], request_form={})

    if not os.path.exists(FAT_CSV) or not os.path.exists(DESP_CSV):
        return render_template("index.html", tabela_html_dre='', lojas=[], lojas_selecionadas=[], request_form={})

    df_fat_full = pd.read_csv(FAT_CSV)
    df_desp = pd.read_csv(DESP_CSV)

    df_fat_full.columns = df_fat_full.columns.str.strip()
    df_desp.columns = df_desp.columns.str.strip()

    df_fat_full["Mês"] = pd.to_datetime(df_fat_full["Mês"]).dt.to_period("M")

    lojas = df_fat_full["Loja"].dropna().unique().tolist()
    df_fat = df_fat_full.copy()

    if acao == "filtrar" and lojas_selecionadas:
        df_fat = df_fat[df_fat["Loja"].isin(lojas_selecionadas)]

    registros = []

    for loja in df_fat["Loja"].unique():
        for mes in df_fat["Mês"].unique():
            df_mes_loja = df_fat[(df_fat["Loja"] == loja) & (df_fat["Mês"] == mes)]
            mes_str = mes.strftime("%Y-%m")

            receita = df_mes_loja["Receita Bruta"].sum()
            impostos = df_mes_loja["Impostos"].sum()
            cmv = df_mes_loja["CMV"].sum()
            tarifa = df_mes_loja["Tarifa Cartão"].sum()

            registros.extend([
                {"Grupo": "RECEITA BRUTA (+)", "Subgrupo": "Receita Bruta", "Mês": mes_str, "Valor": receita, "Loja": loja},
                {"Grupo": "TRIBUTOS (-)", "Subgrupo": "Impostos", "Mês": mes_str, "Valor": impostos, "Loja": loja},
                {"Grupo": "CUSTOS OPERACIONAIS (-)", "Subgrupo": "CMV", "Mês": mes_str, "Valor": cmv, "Loja": loja},
                {"Grupo": "DEDUÇÕES/RETENÇÕES (-)", "Subgrupo": "Tarifa Cartão", "Mês": mes_str, "Valor": tarifa, "Loja": loja}
            ])

            total_fat_lojas = df_fat_full[df_fat_full["Mês"] == mes].groupby("Loja")["Receita Bruta"].sum().to_dict()
            total_geral = sum(total_fat_lojas.values())

            for _, row in df_desp.iterrows():
                if str(row["Mês"]) != mes_str:
                    continue

                grupo = row.get("Grupo", "OUTRAS DESPESAS NÃO IDENTIFICADAS (-)")
                subgrupo = row.get("Subgrupo", "Não Classificado")
                valor = 0

                if str(row.get("Rateio", "")).strip().lower() == "sim":
                    if loja in total_fat_lojas and total_geral > 0:
                        valor = row["Valor"] * (total_fat_lojas[loja] / total_geral)
                elif row.get("Loja Base") == loja:
                    valor = row["Valor"]

                if valor > 0:
                    registros.append({
                        "Grupo": grupo,
                        "Subgrupo": subgrupo,
                        "Mês": mes_str,
                        "Valor": valor,
                        "Loja": loja
                    })

            # ✅ Novo cálculo usando qualquer grupo com "(+)" como receita
            receitas_loja = sum([
                r["Valor"] for r in registros
                if r["Mês"] == mes_str and r["Grupo"].endswith("(+)") and r["Loja"] == loja
            ])

            despesas_loja = sum([
                r["Valor"] for r in registros
                if r["Mês"] == mes_str and r["Grupo"].endswith("(-)") and r["Loja"] == loja
            ])

            lucro_loja = receitas_loja - despesas_loja

            registros.append({
                "Grupo": "LUCRO OU PREJUÍZO DO EXERCÍCIO (=)",
                "Subgrupo": loja,
                "Mês": mes_str,
                "Valor": lucro_loja,
                "Loja": loja
            })

    df = pd.DataFrame(registros)
    df["Mês"] = pd.to_datetime(df["Mês"], format="%Y-%m").dt.to_period("M")

    if acao == "filtrar":
        if periodo_inicio:
            periodo_inicio = pd.Period(periodo_inicio, freq="M")
            df = df[df["Mês"] >= periodo_inicio]
        if periodo_fim:
            periodo_fim = pd.Period(periodo_fim, freq="M")
            df = df[df["Mês"] <= periodo_fim]

    df["Mês"] = df["Mês"].astype(str)

    meses = sorted(df["Mês"].unique())

    df_sub = df[df["Subgrupo"] != ""]
    df_totais = df_sub.groupby(["Grupo", "Mês"], as_index=False)["Valor"].sum()
    df_totais["Subgrupo"] = ""
    df_totais["Tipo"] = "Grupo"
    df_sub["Tipo"] = "Subgrupo"

    df_final = pd.concat([df_sub, df_totais], ignore_index=True)
    df_final["id_grupo"] = df_final["Grupo"].str.replace(r"[^a-zA-Z0-9]", "", regex=True)

    atualizar_ordem_dre_automaticamente(df_final)

    tabela = df_final.pivot_table(
        index=["Grupo", "Subgrupo", "Tipo", "id_grupo"],
        columns="Mês",
        values="Valor",
        aggfunc="sum"
    ).fillna(0).reset_index()

    tabela_html_dre = gerar_tabela_dre_expandida(tabela)

    return render_template(
        "index.html",
        tabela_html_dre=tabela_html_dre,
        lojas=lojas,
        lojas_selecionadas=lojas_selecionadas,
        request_form=request.form
    )

@app.route('/fluxo')
@login_required
def fluxo():
    """
    Rota que renderiza o fluxo de caixa, atualiza o mapeamento de categorias
    e integra campos personalizados corretamente em todos os meses, sem crash quando não há campos.
    Default: de janeiro até o mês atual do ano corrente.
    """
    from flask import render_template, flash, request
    import pandas as pd
    import os, json
    from datetime import datetime

    # 1) Parâmetros de consulta
    hoje = datetime.today()
    ano_atual = hoje.year
    default_inicio = f"{ano_atual}-01"       # janeiro do ano atual
    default_fim    = hoje.strftime("%Y-%m")  # até mês atual
    inicio = request.args.get('inicio') or default_inicio
    fim    = request.args.get('fim')    or default_fim
    contas_sel = request.args.getlist('conta')
    status_sel = request.args.getlist('status')

    # 2) Paths dos arquivos
    fluxo_path   = os.path.join(app.root_path, 'dados', 'base_fluxo_ca.json')
    de_para_path = os.path.join(app.root_path, 'dados', 'de_para_categorias_mv.json')
    campos_path  = os.path.join(app.root_path, 'dados', 'campos_personalizados.json')

    # 3) Leitura da base de fluxo
    if not os.path.exists(fluxo_path):
        flash('❌ Arquivo base_fluxo_ca.json não encontrado.', 'danger')
        return render_template('fluxo.html',
                               fluxo_table='',
                               contas=[], status_list=[],
                               inicio=inicio, fim=fim)
    df = pd.read_json(fluxo_path)
    df.columns = df.columns.str.lower()
    if df.empty:
        flash('⚠️ Nenhum dado na base de fluxo.', 'warning')
        return render_template('fluxo.html',
                               fluxo_table='',
                               contas=[], status_list=[],
                               inicio=inicio, fim=fim)

    # normalizar coluna 'mes' para YYYY-MM
    df['mes'] = df['mes'].astype(str).str[:7]

    # 4) Carregar mapping de subcategoria→categoria
    de_para = []
    if os.path.exists(de_para_path):
        with open(de_para_path, 'r', encoding='utf-8') as f:
            try:
                raw = json.load(f)
            except json.JSONDecodeError:
                raw = []
        for item in raw:
            sub  = (item.get('subcategoria') or item.get('Subcategoria', '')).strip()
            cat  = (item.get('categoria')   or item.get('Categoria',   '')).strip()
            ordv = item.get('ordem')       or item.get('Ordem', 9999)
            de_para.append({'subcategoria': sub, 'categoria': cat, 'ordem': ordv})

    # 5) Atualizar mapping com novas entradas
    unique_subs = df['subcategoria'].dropna().astype(str).str.strip().unique()
    mapped_subs = {d['subcategoria'] for d in de_para}
    novos       = [s for s in unique_subs if s not in mapped_subs]
    temp_map    = {d['subcategoria']: d['categoria'] for d in de_para}

    # mapear e preencher 'Sem Grupo'
    df['categoria'] = (
        df['subcategoria'].astype(str).str.strip()
          .map(temp_map)
          .fillna(df.get('categoria', pd.NA).fillna('Sem Grupo'))
    )

    unique_cats = df['categoria'].dropna().unique()
    mapped_cats = {d['categoria'] for d in de_para}
    novas_cats  = [c for c in unique_cats if c not in mapped_cats]
    if novos or novas_cats:
        base_ord = max((d['ordem'] for d in de_para), default=0)
        for i, sub in enumerate(novos, start=1):
            de_para.append({'subcategoria': sub, 'categoria': 'Sem Grupo', 'ordem': base_ord + i})
        offset = base_ord + len(novos)
        for j, cat in enumerate(novas_cats, start=1):
            de_para.append({'subcategoria': '', 'categoria': cat, 'ordem': offset + j})
        with open(de_para_path, 'w', encoding='utf-8') as f:
            json.dump(de_para, f, ensure_ascii=False, indent=2)

    # 6) Aplicar mapping definitivo
    mapa      = {d['subcategoria']: d['categoria'] for d in de_para}
    ordem_map = {d['categoria']:   d['ordem'] for d in de_para}
    df['categoria'] = df['subcategoria'].map(mapa).fillna(df['categoria'])

    # **Remover** todas as linhas cuja categoria ficou 'Sem Grupo'
    df = df[df['categoria'] != 'Sem Grupo']

    # preparar filtros para a UI
    contas = (
        df[['conta_id','conta_nome']]
          .drop_duplicates()
          .rename(columns={'conta_id':'id','conta_nome':'nome'})
          .to_dict(orient='records')
    )
    status_list = sorted(df['status'].dropna().unique())

    # 7) Aplicar filtros de período, conta e status (string 'YYYY-MM')
    df = df[(df['mes'] >= inicio) & (df['mes'] <= fim)]
    if contas_sel:
        df = df[df['conta_id'].isin(contas_sel)]
    if status_sel:
        df = df[df['status'].isin(status_sel)]
    if df.empty:
        flash('⚠️ Nenhum registro após filtros.', 'warning')
        return render_template('fluxo.html',
                               fluxo_table='',
                               contas=contas,
                               status_list=status_list,
                               inicio=inicio, fim=fim)

    # recalcular lista de meses após filtro
    meses = sorted(df['mes'].unique())

    # 8) Preparar subgrupos
    df_sub = df[df['subcategoria'].notna()].copy()
    df_sub['tipo'] = 'Subgrupo'

    # 9) Totais iniciais de grupos
    df_init_tot = (
        df_sub.groupby(['categoria','mes'], as_index=False)['valor'].sum()
        if not df_sub.empty else pd.DataFrame(columns=['categoria','mes','valor'])
    )

    # 10) Calcular campos personalizados
    custom = []
    if os.path.exists(campos_path):
        with open(campos_path, 'r', encoding='utf-8') as f:
            try:
                campos = json.load(f)
            except:
                campos = []
        for campo in campos:
            nome, grp, formula = campo.get('nome',''), campo.get('grupo',''), campo.get('formula','')
            for m in meses:
                expr = formula
                for _, row in df_init_tot[df_init_tot['mes']==m].iterrows():
                    expr = expr.replace(row['categoria'], str(row['valor']))
                try:
                    val = eval(expr)
                except:
                    val = 0
                custom.append({
                    'categoria': grp,
                    'subcategoria': nome,
                    'mes': m,
                    'valor': val,
                    'tipo': 'Subgrupo'
                })
    df_custom = pd.DataFrame(custom, columns=['categoria','subcategoria','mes','valor','tipo'])

    # 11) Totais finais de grupos
    frames = []
    if not df_init_tot.empty:
        frames.append(df_init_tot[['categoria','mes','valor']])
    if not df_custom.empty:
        frames.append(df_custom[['categoria','mes','valor']])
    if frames:
        df_tot = (pd.concat(frames, ignore_index=True)
                  .groupby(['categoria','mes'], as_index=False)['valor'].sum())
    else:
        df_tot = pd.DataFrame(columns=['categoria','mes','valor'])
    df_tot['subcategoria'] = ''
    df_tot['tipo']       = 'Grupo'

    # 12) Combinar e pivotar
    frames_all = [df_tot]
    if not df_sub.empty:
        frames_all.append(df_sub)
    if not df_custom.empty:
        frames_all.append(df_custom)
    df_all = pd.concat(frames_all, ignore_index=True)
    df_all['id_grupo'] = df_all['categoria'].str.replace(r'[^\w]','',regex=True)
    tabela = (df_all.pivot_table(
        index=['categoria','subcategoria','tipo','id_grupo'],
        columns='mes', values='valor', aggfunc='sum', fill_value=0
    ).reset_index())
    tabela['ordem'] = tabela['categoria'].map(ordem_map).fillna(9999)
    tabela.sort_values(by=['ordem','categoria','subcategoria'], inplace=True)

    # 13) Montar HTML expansível
    html  = '<div class="d-flex gap-2 mb-2">'
    html += '<button class="btn btn-outline-secondary btn-sm" onclick="toggleExpandAll(this)">➕ Expandir Tudo</button></div>'
    html += '<table class="table table-bordered table-hover"><thead><tr><th>Classificação</th>' \
            + ''.join(f'<th>{m}</th>' for m in meses) + '<th>Total</th></tr></thead><tbody>'
    for _, r in tabela.iterrows():
        is_grp = (r['tipo'] == 'Grupo')
        idg    = r['id_grupo']
        classes= f"grupo-{idg} {'grupo-header' if is_grp else 'grupo-item'}"
        disp   = '' if is_grp else 'display:none;'
        style  = 'font-weight:bold;background:#f0f0f0;' if is_grp else ''
        label  = (
            f"<button class=\"btn btn-sm btn-link\" onclick=\"toggleDetalhes('{idg}')\">[+]</button> {r['categoria']}"
            if is_grp else f"➔ {r['subcategoria']}"
        )
        html += f"<tr class='{classes}' style='{disp}{style}'><td style='padding-left:30px'>{label}</td>"
        total = 0
        for m in meses:
            v = r.get(m,0)
            total += v
            cell = f"R$ {round(v):,}".replace(',', 'X').replace('.', ',').replace('X','.')
            html += f"<td>{cell}</td>"
        tot = f"R$ {round(total):,}".replace(',', 'X').replace('.', ',').replace('X','.')
        html += f"<td>{tot}</td></tr>"
    html += '</tbody></table>'
    html += """
<script>
    function toggleDetalhes(id) {
        document.querySelectorAll('.grupo-' + id + '.grupo-item').forEach(function(row) {
            row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
        });
    }
    function toggleExpandAll(button) {
        const items = document.querySelectorAll('tr.grupo-item');
        const anyVisible = Array.from(items).some(r => r.style.display !== 'none');
        items.forEach(r => r.style.display = anyVisible ? 'none' : 'table-row');
        button.innerText = anyVisible ? '➕ Expandir Tudo' : '🔽 Recolher Tudo';
    }
</script>"""
    return render_template('fluxo.html',
                           fluxo_table=html,
                           contas=contas,
                           status_list=status_list,
                           inicio=inicio,
                           fim=fim)



@app.route('/ajuste_ordem_dre', methods=['GET', 'POST'])
@login_required
def ajuste_ordem_dre():
    import os
    import json

    ordem_path = os.path.join(app.root_path, 'dados', 'ordem_dre.json')

    # 🔧 Ler ordem existente
    if os.path.exists(ordem_path):
        with open(ordem_path, 'r', encoding='utf-8') as f:
            ordem_dre = json.load(f)
    else:
        # Se não existir, carregar dos grupos existentes na base atual
        fluxo_path = os.path.join(app.root_path, 'dados', 'base_fluxo_ca.json')
        if not os.path.exists(fluxo_path):
            flash('❌ Arquivo base_fluxo_ca.json não encontrado.', 'danger')
            return redirect('/')

        import pandas as pd
        df = pd.read_json(fluxo_path)
        df.columns = [c.lower() for c in df.columns]

        grupos = sorted(df['categoria'].dropna().unique())
        ordem_dre = [{'grupo': g, 'ordem': 999} for g in grupos]

    if request.method == 'POST':
        # 🔧 Atualizar ordem
        for item in ordem_dre:
            key = f"ordem_{item['grupo']}"
            nova_ordem = request.form.get(key)
            try:
                item['ordem'] = int(nova_ordem)
            except:
                item['ordem'] = 999

        # 🔧 Salvar ordem
        with open(ordem_path, 'w', encoding='utf-8') as f:
            json.dump(ordem_dre, f, ensure_ascii=False, indent=2)

        flash('✅ Ordem atualizada com sucesso.', 'success')
        return redirect('/ajuste_ordem_dre')

    return render_template('ajuste_ordem_dre.html', ordem_dre=ordem_dre)


import json
from pathlib import Path
import json, tempfile, os, logging

logger = logging.getLogger(__name__)
DATA_DIR = Path(app.root_path) / "dados"
DATA_DIR.mkdir(parents=True, exist_ok=True)
USUARIOS_JSON = DATA_DIR / "usuarios.json"

def carregar_usuarios():
    """Carrega usuários com path absoluto e tolerância a JSON inválido."""
    if not USUARIOS_JSON.exists():
        logger.warning(f"[usuarios] arquivo não existe: {USUARIOS_JSON}")
        return []
    try:
        with USUARIOS_JSON.open('r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"[usuarios] JSON inválido em {USUARIOS_JSON}: {e}")
        return []
    except Exception as e:
        logger.error(f"[usuarios] erro ao ler {USUARIOS_JSON}: {e}")
        return []

def salvar_usuarios(lista):
    """Escrita atômica: grava em arquivo temporário e faz replace."""
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), prefix="usuarios.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as tmpf:
            json.dump(lista, tmpf, ensure_ascii=False, indent=2)
            tmpf.flush()
            os.fsync(tmpf.fileno())
        os.replace(tmp_path, USUARIOS_JSON)  # atômico no mesmo FS
    except Exception as e:
        logger.error(f"[usuarios] erro ao salvar: {e}")
        # se falhar, remove temporário
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise

@app.route("/usuarios", methods=["GET", "POST"])
def cadastro_usuarios():
    erro = sucesso = None
    lista_usuarios = carregar_usuarios()

    if request.method == "POST":
        if "excluir_usuario" in request.form:
            usuario_para_excluir = request.form.get("excluir_usuario")
            lista_usuarios = [u for u in lista_usuarios if u["usuario"] != usuario_para_excluir]
            salvar_usuarios(lista_usuarios)
            sucesso = f"Usuário '{usuario_para_excluir}' excluído com sucesso."
        else:
            novo_usuario = request.form.get("usuario")
            nova_senha = request.form.get("senha")

            if not novo_usuario or not nova_senha:
                erro = "Preencha todos os campos."
            elif any(u["usuario"] == novo_usuario for u in lista_usuarios):
                erro = "Usuário já existe."
            else:
                lista_usuarios.append({"usuario": novo_usuario, "senha": nova_senha})
                salvar_usuarios(lista_usuarios)
                sucesso = "Usuário cadastrado com sucesso."

    lista_usuarios = carregar_usuarios()
    return render_template("usuarios.html", usuarios=lista_usuarios, erro=erro, sucesso=sucesso)

@app.route("/conectar_contaazul", methods=["GET", "POST"])
@login_required
def conectar_contaazul():
    import json
    from datetime import datetime
    from pathlib import Path

    # 1) Define DATA_DIR e garante existência
    PROJECT_ROOT     = Path(__file__).resolve().parent
    DATA_DIR         = PROJECT_ROOT / "dados"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    headers_path     = DATA_DIR / "headers_contaazul.json"
    credenciais_path = DATA_DIR / "credenciais_contaazul.json"
    ultima_conexao   = None

    if request.method == "POST":
        acao = request.form.get("acao")  # Saber qual botão foi clicado

        try:
            from scripts.autenticador_ca       import autenticar_contaazul
            from scripts.extrair_auth          import extrair_secret_de_uri
            from scripts.coleta_fluxo_ca       import coletar_fluxo, gerar_de_para
            from scripts.extrator_de_rc        import coletar_recebiveis
            from scripts.unificador_de_tabelas import unificador
            from scripts.consulta_web   import web_consulta
            from scripts.extrator_de_cb import coletar_contas_bancarias
            from scripts.extrator_de_cc import coletar_centros_de_custo
            if acao == "conectar":
                # 🔥 Capturar dados do formulário
                email   = request.form.get("email", "").strip()
                senha   = request.form.get("senha", "").strip()
                otp_uri = request.form.get("otp_uri", "").strip()
                ano_base= request.form.get("ano_base", "").strip()

                if not (email and senha and otp_uri and ano_base):
                    flash("❌ Todos os campos são obrigatórios para salvar as credenciais.", "danger")
                    return render_template("conectar_contaazul.html", ultima_conexao=ultima_conexao)

                # 🔐 Extrair o OTP_SECRET
                otp_secret = extrair_secret_de_uri(otp_uri)

                # ✔️ Salvar credenciais
                credenciais = {
                    "email": email,
                    "senha": senha,
                    "otp_secret": otp_secret,
                    "ano_base": int(ano_base) if ano_base.isdigit() else datetime.today().year
                }
                credenciais_path.write_text(
                    json.dumps(credenciais, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                # ✔️ Autenticar e gerar headers
                headers = autenticar_contaazul(email, senha, otp_secret)
                headers_path.write_text(
                    json.dumps(headers, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                flash("✅ Conexão realizada e credenciais salvas com sucesso!", "success")

            elif acao == "atualizar":
                # 🔄 Usar credenciais salvas
                if not credenciais_path.exists():
                    flash("❌ Nenhuma credencial encontrada. Faça a conexão primeiro.", "danger")
                    return render_template("conectar_contaazul.html", ultima_conexao=ultima_conexao)

                credenciais = json.loads(credenciais_path.read_text(encoding="utf-8"))
                email      = credenciais.get("email")
                senha      = credenciais.get("senha")
                otp_secret = credenciais.get("otp_secret")

                if not (email and senha and otp_secret):
                    flash("❌ Credenciais incompletas. Refazer a conexão.", "danger")
                    return render_template("conectar_contaazul.html", ultima_conexao=ultima_conexao)

                # 🔄 Autenticar novamente
                headers = autenticar_contaazul(email, senha, otp_secret)
                headers_path.write_text(
                    json.dumps(headers, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                flash("✅ Conexão atualizada com sucesso usando as credenciais salvas!", "success")

            # ✔️ Após qualquer ação, executar todos os scripts
            web_consulta()
            coletar_contas_bancarias()
            coletar_centros_de_custo()
            df_fluxo = coletar_fluxo()
            gerar_de_para(df_fluxo)
            coletar_recebiveis()
            unificador()
        

            flash("📥 Dados do fluxo atualizados com sucesso após conexão!", "success")
            ultima_conexao = datetime.now().strftime("%d/%m/%Y %H:%M")

        except Exception as e:
            flash(f"❌ Erro ao conectar com a Conta Azul: {e}", "danger")
            print(f"[ERRO] {e}")

    # 🔎 Verificar data da última conexão
    if headers_path.exists():
        mod_time = headers_path.stat().st_mtime
        ultima_conexao = datetime.fromtimestamp(mod_time).strftime("%d/%m/%Y %H:%M")

    return render_template("conectar_contaazul.html", ultima_conexao=ultima_conexao)



@app.route("/conectar_contaazul_automatica")
@login_required
def conectar_contaazul_automatica():
    import os
    import json
    from datetime import datetime
    from scripts.autenticador_ca import autenticar_contaazul
    from scripts.coleta_fluxo_ca import coletar_fluxo, gerar_de_para

    headers_path = os.path.join("dados", "headers_contaazul.json")
    credenciais_path = os.path.join("dados", "credenciais_contaazul.json")

    try:
        if not os.path.exists(credenciais_path):
            flash("❌ Nenhuma credencial salva. Faça a conexão manual primeiro.", "danger")
            return redirect(url_for("conectar_contaazul"))

        with open(credenciais_path, "r", encoding="utf-8") as f:
            credenciais = json.load(f)

        email = credenciais.get("email")
        senha = credenciais.get("senha")
        otp_secret = credenciais.get("otp_secret")

        if not all([email, senha, otp_secret]):
            flash("❌ Credenciais incompletas. Faça a conexão manual novamente.", "danger")
            return redirect(url_for("conectar_contaazul"))

        # 🔄 Autenticar novamente
        headers = autenticar_contaazul(email, senha, otp_secret)

        with open(headers_path, "w", encoding="utf-8") as f:
            json.dump(headers, f, indent=2, ensure_ascii=False)

        flash("✅ Conexão atualizada automaticamente com sucesso!", "success")

        # 🔥 Executa coleta e geração de de-para
        coletar_fluxo()
        gerar_de_para()

        flash("📥 Dados do fluxo atualizados com sucesso após atualização automática!", "success")

    except Exception as e:
        flash(f"❌ Erro na conexão automática: {e}", "danger")
        print(f"[ERRO] {e}")

    return redirect(url_for("conectar_contaazul"))

@app.route('/editar_grupo', methods=['POST'])
@login_required
def editar_grupo():
    antigo = request.form['grupo_antigo']
    novo = request.form['grupo_novo']
    caminho = os.path.join('dados', 'de_para_categorias_mv.json')
    if os.path.exists(caminho):
        with open(caminho, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        for item in dados:
            if item['Categoria'] == antigo:
                item['Categoria'] = novo
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    return redirect(url_for('cadastro_grupos'))

@app.route('/excluir_grupo', methods=['POST'])
@login_required
def excluir_grupo():
    grupo = request.form['grupo']
    caminho = os.path.join('dados', 'de_para_categorias_mv.json')
    if os.path.exists(caminho):
        with open(caminho, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        # filtra usando a chave lowercase 'categoria', caindo em compatibilidade se ainda houver 'Categoria'
        dados = [
            item for item in dados
            if item.get('categoria', item.get('Categoria', '')) != grupo
        ]
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    return redirect(url_for('cadastro_grupos'))



from flask import jsonify

@app.route("/cadastro_grupos", methods=["GET", "POST"])
@login_required
def cadastro_grupos():
    caminho_json = "dados/de_para_categorias_mv.json"

    # Lê os dados existentes do JSON
    if os.path.exists(caminho_json):
        with open(caminho_json, "r", encoding="utf-8") as f:
            dados = json.load(f)
    else:
        dados = []

    # Adicionar novo grupo
    if request.method == "POST" and "novo_grupo" in request.form:
        novo_grupo = request.form["novo_grupo"].strip()
        if novo_grupo:
            dados.append({"Categoria": novo_grupo, "Grupo": None, "ordem": 9999})
            with open(caminho_json, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
        return redirect(url_for("cadastro_grupos"))

    # Organiza os grupos únicos com menor ordem atribuída
    ordens = {}
    for item in dados:
        categoria = item.get("Categoria", "").strip()
        ordem = item.get("ordem", 9999)
        if categoria:
            if categoria not in ordens or ordem < ordens[categoria]:
                ordens[categoria] = ordem

    grupos_existentes = sorted(ordens.keys(), key=lambda x: ordens[x])

    return render_template("cadastro_grupos.html", grupos=grupos_existentes, ordens=ordens)


# Rota de Ajuste de Categorias (com preservação de grupos puros)
@app.route('/ajuste_categorias', methods=['GET', 'POST'])
@login_required
def ajuste_categorias():
    import os, json, pandas as pd
    from flask import render_template, request, flash, redirect, url_for

    # --- Caminhos ---
    fluxo_path  = os.path.join(app.root_path, 'dados', 'base_fluxo_ca.json')
    grupos_path = os.path.join(app.root_path, 'dados', 'de_para_categorias_mv.json')

    # --- Carregar subcategorias da base de fluxo ---
    if not os.path.exists(fluxo_path):
        flash('Base de fluxo não encontrada.', 'danger')
        return render_template('ajuste_categorias.html', subcategorias=[], grupos=[], associacoes={})
    df = pd.read_json(fluxo_path)
    df.columns = df.columns.str.lower()
    subcategorias = sorted(df['subcategoria'].dropna().astype(str).unique())

    # --- Carregar JSON de-para ---
    de_para_raw = []
    if os.path.exists(grupos_path):
        with open(grupos_path, 'r', encoding='utf-8') as f:
            de_para_raw = json.load(f)

    # GET: preparar listas para renderizar
    if request.method == 'GET':
        # normalizar keys
        de_para = []
        for item in de_para_raw:
            sub = item.get('subcategoria', item.get('Subcategoria', ''))
            cat = item.get('categoria',    item.get('Categoria',    ''))
            ordv= item.get('ordem',        item.get('Ordem',        999))
            de_para.append({'subcategoria': sub, 'categoria': cat, 'ordem': ordv})

        grupos = sorted({d['categoria'] for d in de_para if d['categoria']})
        associacoes = {d['subcategoria']: d['categoria'] for d in de_para if d['subcategoria']}
        return render_template(
            'ajuste_categorias.html',
            subcategorias=subcategorias,
            grupos=grupos,
            associacoes=associacoes
        )

    # POST: salvar alterações
    # 1️⃣ preservar todos os grupos puros (sem subcategoria)
    pure_groups = [item for item in de_para_raw if not item.get('subcategoria')]
    # 2️⃣ construir nova lista mantendo pure_groups
    novo_de_para = list(pure_groups)
    # 3️⃣ para cada subcategoria, ler seleção e adicionar
    for sub in subcategorias:
        grupo_sel = request.form.get(sub)
        if grupo_sel:
            # tentar reaproveitar ordem antiga
            orig = next((i for i in de_para_raw if i.get('subcategoria') == sub), None)
            ordem_ant = orig.get('ordem', 999) if orig else 999
            novo_de_para.append({
                'subcategoria': sub,
                'Categoria':    grupo_sel,  # manter maiúscula para compatibilidade
                'ordem':        ordem_ant
            })

    # 4️⃣ salvar de volta
    with open(grupos_path, 'w', encoding='utf-8') as f:
        json.dump(novo_de_para, f, ensure_ascii=False, indent=2)

    flash('✔️ Ajustes salvos com sucesso.', 'success')
    return redirect(url_for('ajuste_categorias'))


@app.route("/atualizar_ordem", methods=["POST"])
@login_required
def atualizar_ordem():
    caminho_json = os.path.join("dados", "de_para_categorias_mv.json")
    
    if not os.path.exists(caminho_json):
        flash("Arquivo de grupos não encontrado.", "danger")
        return redirect(url_for("cadastro_grupos"))
    
    with open(caminho_json, "r", encoding="utf-8") as f:
        dados = json.load(f)

    # Atualizar ordem apenas para categorias existentes
    for item in dados:
        grupo = item.get("Categoria")
        campo_ordem = f"ordem_{grupo}"
        nova_ordem = request.form.get(campo_ordem)

        if nova_ordem and nova_ordem.isdigit():
            item["ordem"] = int(nova_ordem)

    with open(caminho_json, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

    flash("Ordem dos grupos atualizada com sucesso!", "success")
    return redirect(url_for("cadastro_grupos"))


@app.route('/campos_personalizados', methods=['GET', 'POST'])
@login_required
def campos_personalizados():
    import os
    import json
    from flask import request, redirect, url_for, render_template

    # Caminho do JSON de campos
    caminho_campos = os.path.join(app.root_path, 'dados', 'campos_personalizados.json')
    campos = []

    # Carrega campos existentes
    if os.path.exists(caminho_campos):
        with open(caminho_campos, 'r', encoding='utf-8') as f:
            campos = json.load(f)

    # POST: adiciona novo campo sem sobrescrever o arquivo inteiro
    if request.method == 'POST':
        try:
            # Copia os campos atuais
            existing = list(campos)
            # Extrai dados do formulário para o novo campo
            nome = request.form.get('nome', '').strip()
            formula = request.form.get('formula', '').strip()
            grupo = request.form.get('grupo', '').strip()
            ordem = request.form.get('ordem', '').strip()
            ordem = int(ordem) if ordem.isdigit() else (len(existing) + 1)

            novo_campo = {
                'nome': nome,
                'grupo': grupo,
                'ordem': ordem,
                'formula': formula
            }
            # Adiciona o novo campo à lista
            existing.append(novo_campo)

            # Salva a lista completa de volta ao JSON
            with open(caminho_campos, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Erro ao salvar campos personalizados]: {e}")
        return redirect(url_for('campos_personalizados'))

    # GET: carrega lista completa de grupos a partir do de_para
    grupos = carregar_ordem_dos_grupos()
    return render_template(
        'campos_personalizados.html',
        campos=campos,
        grupos=grupos
    )

def carregar_ordem_dre():
    caminho = os.path.join("dados", "ordem_dre.json")
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        dados = json.load(f)
    ordem = {item["grupo"]: item["ordem"] for item in dados}
    return ordem



@app.route('/atualizar_campos_personalizados', methods=['POST'])
@login_required
def atualizar_campos_personalizados():
    caminho_campos = os.path.join('dados', 'campos_personalizados.json')
    if not os.path.exists(caminho_campos):
        return redirect(url_for('campos_personalizados'))

    with open(caminho_campos, 'r', encoding='utf-8') as f:
        campos = json.load(f)

    for campo in campos:
        ordem_nova = request.form.get(f'ordem_{campo["nome"]}')
        if ordem_nova and ordem_nova.isdigit():
            campo['ordem'] = int(ordem_nova)

    campos.sort(key=lambda x: x.get('ordem', 9999))

    with open(caminho_campos, 'w', encoding='utf-8') as f:
        json.dump(campos, f, indent=2, ensure_ascii=False)

    return redirect(url_for('campos_personalizados'))

@app.route('/excluir_campo_personalizado', methods=['POST'])
@login_required
def excluir_campo_personalizado():
    nome = request.form.get('nome')
    caminho_campos = os.path.join('dados', 'campos_personalizados.json')

    if os.path.exists(caminho_campos):
        with open(caminho_campos, 'r', encoding='utf-8') as f:
            campos = json.load(f)

        campos = [c for c in campos if c['nome'] != nome]

        with open(caminho_campos, 'w', encoding='utf-8') as f:
            json.dump(campos, f, indent=2, ensure_ascii=False)

    return redirect(url_for('campos_personalizados'))




### 1. app.py – Rotas para adicionar, editar e excluir tarefas
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os, json
from functools import wraps
from datetime import datetime

# Path do JSON
TODO_PATH = os.path.join(app.root_path, 'dados', 'todo.json')

def load_tasks():
    if not os.path.exists(TODO_PATH):
        return []
    with open(TODO_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_tasks(tasks):
    os.makedirs(os.path.dirname(TODO_PATH), exist_ok=True)
    with open(TODO_PATH, 'w', encoding='utf-8') as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


# Listagem do board
@app.route('/todo')
@login_required
def todo_board():
    tasks = load_tasks()
    boards = {'Backlog': [], 'Sprint': [], 'Feito': []}
    for t in tasks:
        boards.get(t['status'], boards['Backlog']).append(t)
    return render_template('todo.html', boards=boards)

# Adição de nova tarefa
@app.route('/todo/add', methods=['POST'])
@login_required
def add_task():
    tasks = load_tasks()
    data = request.form
    new = {
        'id': int(datetime.now().timestamp() * 1000),
        'title': data.get('title', '').strip(),
        'responsavel': data.get('responsavel', '').strip(),
        'prazo': data.get('prazo', '').strip(),
        'descricao': data.get('descricao', '').strip(),
        'status': 'Backlog'
    }
    tasks.append(new)
    save_tasks(tasks)
    return redirect(url_for('todo_board'))

# Atualização de status ou exclusão via POST
@app.route('/todo/update_status', methods=['POST'])
@login_required
def update_status():
    # trata JSON ou form
    if request.is_json:
        payload = request.get_json()
        tid = int(payload.get('id'))
        new_status = payload.get('status')
        respond_json = True
    else:
        tid = int(request.form['id'])
        new_status = request.form['status']
        respond_json = False
    
    tasks = load_tasks()
    for t in tasks:
        if t['id'] == tid:
            t['status'] = new_status
            break
    save_tasks(tasks)
    if respond_json:
        return jsonify(success=True)
    return redirect(url_for('todo_board'))

# Excluir tarefa
@app.route('/todo/delete', methods=['POST'])
@login_required
def delete_task():
    tid = int(request.form['id'])
    tasks = load_tasks()
    tasks = [t for t in tasks if t['id'] != tid]
    save_tasks(tasks)
    return redirect(url_for('todo_board'))

# Editar tarefa
@app.route('/todo/edit/<int:task_id>', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    tasks = load_tasks()
    task = next((t for t in tasks if t['id'] == task_id), None)
    if not task:
        return redirect(url_for('todo_board'))

    if request.method == 'POST':
        data = request.form
        task['title'] = data.get('title','').strip()
        task['responsavel'] = data.get('responsavel','').strip()
        task['prazo'] = data.get('prazo','').strip()
        task['descricao'] = data.get('descricao','').strip()
        task['status'] = data.get('status', task['status'])
        save_tasks(tasks)
        return redirect(url_for('todo_board'))

    return render_template('todo_edit.html', task=task)


from datetime import datetime
from dateutil.relativedelta import relativedelta

@app.route('/dashboard')
@login_required
def dashboard():
    # --- 1) Carrega JSON financeiro ---
    path_ca = os.path.join(app.root_path, 'dados', 'base_fluxo_ca.json')
    with open(path_ca, encoding='utf-8') as f:
        data = json.load(f)
    df = pd.DataFrame(data)

    # --- 2) Padroniza coluna 'mes' ---
    df['mes'] = pd.to_datetime(df['mes'], format='%Y-%m').dt.strftime('%Y-%m')

    # --- 3) Define período padrão ---
    hoje = datetime.now()
    ano_atual = hoje.year
    default_start = f"{ano_atual}-01"
    default_end = f"{ano_atual}-12"

    # --- 4) Lê parâmetros ou usa padrão ---
    start = request.args.get('start', default_start)
    end = request.args.get('end', default_end)

    # --- 5) Filtra período ---
    df_periodo = df[df['mes'].between(start, end)].copy()

    # --- 6) Filtra contas bancárias ---
    contas_all = sorted(df_periodo['conta_nome'].unique())
    selected_contas = request.args.getlist('conta') or contas_all
    df_contas = df_periodo[df_periodo['conta_nome'].isin(selected_contas)]

    # --- 7) Define labels de meses ---
    labels = sorted(df_contas['mes'].unique())

    # --- 8) Série de valores por subcategoria ---
    def serie(subcat):
        return (
            df_contas[df_contas['subcategoria'] == subcat]
                      .groupby('mes')['valor']
                      .sum()
                      .reindex(labels, fill_value=0)
                      .tolist()
        )

    saldo = serie('Saldo Final de Caixa')
    receitas = serie('Total de Recebimentos')
    pagamentos = serie('Total de Pagamentos')
    pagamentos_abs = [abs(v) for v in pagamentos]
    geracao = [r - p for r, p in zip(receitas, pagamentos_abs)]

    # --- 9) Calcula % de ocupação ---
    def load_prod(path):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return {}
        with open(path, encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

    prod_cons = load_prod(os.path.join(app.root_path, 'dados', 'produtividade_consultores.json'))
    prod_bpo = load_prod(os.path.join(app.root_path, 'dados', 'produtividade_bpo.json'))

    consult_pct = []
    bpo_pct = []
    for mes in labels:
        # Consultores
        cons = prod_cons.get(mes, {})
        aloc_cons = sum(item.get('alocacoes', 0) for item in cons.values())
        disp_cons = sum(item.get('disponibilidade', 0) for item in cons.values())
        pct_cons = round((aloc_cons / disp_cons * 100), 1) if disp_cons else 0
        consult_pct.append(pct_cons)

        # BPO
        bpo = prod_bpo.get(mes, {})
        aloc_bpo = sum(item.get('alocacoes', 0) for item in bpo.values())
        disp_bpo = sum(item.get('disponibilidade', 0) for item in bpo.values())
        pct_bpo = round((aloc_bpo / disp_bpo * 100), 1) if disp_bpo else 0
        bpo_pct.append(pct_bpo)

    # --- 10) Carrega dados de projetos/faturamento ---
    path_proj = os.path.join(app.root_path, 'dados', 'base_final_04_rc.json')
    with open(path_proj, encoding='utf-8') as f:
        raw_proj = json.load(f)
    df_proj = pd.DataFrame(raw_proj)

    # --- 11) Prepara DataFrame de projetos ---
    df_proj['dueDate'] = pd.to_datetime(df_proj['dueDate'])
    df_proj['year'] = df_proj['dueDate'].dt.year
    df_proj['month_num'] = df_proj['dueDate'].dt.month
    df_proj['totalNetValue'] = (
        pd.to_numeric(df_proj['totalNetValue'], errors='coerce')
          .fillna(0)
    )
    df_proj['name_y'] = df_proj['name_y'].astype(str)
    df_proj['status'] = df_proj['status'].astype(str)

    anos = [ano_atual - 1, ano_atual]
    df_proj = df_proj[df_proj['year'].isin(anos)]

    # --- 12) Filtro de Centro de Custos ---
    centros_all = sorted(df_proj['name_y'].unique())
    selected_cc = request.args.getlist('cc') or centros_all
    df_cc = df_proj[df_proj['name_y'].isin(selected_cc)]

    # --- 13) Séries comparativas mês a mês ---
    months = list(range(1, 13))
    fat_prev = []  # Faturamento ano anterior
    fat_curr = []  # Faturamento ano atual
    proj_prev = []  # Qtde projetos ano anterior
    proj_curr = []  # Qtde projetos ano atual
    ticket_prev = []
    ticket_curr = []

    for m in months:
        df_prev = df_cc[(df_cc['year'] == ano_atual - 1) & (df_cc['month_num'] == m)]
        df_curr = df_cc[(df_cc['year'] == ano_atual) & (df_cc['month_num'] == m)]

        sum_prev = df_prev['totalNetValue'].sum()
        sum_curr = df_curr['totalNetValue'].sum()
        cnt_prev = df_prev.shape[0]
        cnt_curr = df_curr.shape[0]

        fat_prev.append(sum_prev)
        fat_curr.append(sum_curr)
        proj_prev.append(cnt_prev)
        proj_curr.append(cnt_curr)
        ticket_prev.append(round((sum_prev / cnt_prev), 2) if cnt_prev else 0)
        ticket_curr.append(round((sum_curr / cnt_curr), 2) if cnt_curr else 0)


    rev_prev = []
    rev_curr = []
    for m in months:
        key_prev   = f"{ano_atual-1}-{m:02d}"
        key_curr   = f"{ano_atual}-{m:02d}"
        # soma do faturamento naquele mês
        total_prev = df_cc[(df_cc['year'] == ano_atual-1) & (df_cc['month_num'] == m)]['totalNetValue'].sum()
        total_curr = df_cc[(df_cc['year'] == ano_atual)   & (df_cc['month_num'] == m)]['totalNetValue'].sum()
        # conta quantos colaboradores (consultores + BPO) naquele mês
        count_prev = len(prod_cons.get(key_prev, {})) + len(prod_bpo.get(key_prev, {}))
        count_curr = len(prod_cons.get(key_curr, {})) + len(prod_bpo.get(key_curr, {}))
        rev_prev.append(total_prev/count_prev if count_prev else 0)
        rev_curr.append(total_curr/count_curr if count_curr else 0)

    # --- 14) Inadimplência ---
    df_cc['mes'] = df_cc['dueDate'].dt.strftime('%Y-%m')
    df_period_cc = df_cc[df_cc['mes'].between(start, end)]

    overdue = df_period_cc[df_period_cc['status'] == 'OVERDUE']
    inadimplencia = (
        overdue.groupby('mes')['totalNetValue']
               .sum()
               .reindex(labels, fill_value=0)
               .to_dict()
    )
    inadimpl_count = (
        overdue.groupby('mes')
               .size()
               .reindex(labels, fill_value=0)
               .to_dict()
    )

    # --- 15) Faturamento por Colaborador ---
    collab_counts = []
    rev_per_collab = []
    for idx, m in enumerate(months):
        mes_str = datetime(ano_atual, m, 1).strftime('%Y-%m')
        if 'Terceirizapro' in selected_cc:
            total_collab = len(prod_bpo.get(mes_str, {}))
        elif 'Way' in selected_cc:
            total_collab = len(prod_cons.get(mes_str, {}))
        else:
            total_collab = (
                len(prod_bpo.get(mes_str, {})) +
                len(prod_cons.get(mes_str, {}))
            )

        collab_counts.append(total_collab)
        revenue = fat_curr[idx]
        rev_per_collab.append(
            round((revenue / total_collab), 2)
            if total_collab else 0
        )
    
    month_labels = [
        datetime(ano_atual, m, 1).strftime('%b')
        for m in months
    ]

# --- 15-b) Faturamento mensal (WEB) + Ticket Médio e Qtd Vendas ---
    def brl_to_float(s):
        if s is None:
            return 0.0
        s = str(s).strip()
        if not s or s == 'nan':
            return 0.0
        s = s.replace('R$', '').replace('.', '').replace(',', '.')
        try:
            return float(s)
        except ValueError:
            return 0.0

    path_web = os.path.join(app.root_path, 'dados', 'resultado_web.json')

    # arrays de 12 meses
    fat_web_prev     = [0.0] * 12
    fat_web_curr     = [0.0] * 12
    vendas_web_prev  = [0]   * 12
    vendas_web_curr  = [0]   * 12
    ticket_web_prev  = [0.0] * 12
    ticket_web_curr  = [0.0] * 12

    if os.path.exists(path_web) and os.path.getsize(path_web) > 0:
        with open(path_web, encoding='utf-8') as f:
            raw_web = json.load(f)
        df_web = pd.DataFrame(raw_web)

        # Trata valores e datas (somente pagamentos efetivos)
        df_web['valor_pago'] = df_web.get('Valor Pago', 0).apply(brl_to_float)
        df_web['data_pg'] = pd.to_datetime(
            df_web.get('Data Pgto', None),
            format='%d/%m/%Y',
            errors='coerce'
        )
        df_web = df_web.dropna(subset=['data_pg'])  # só linhas com pagamento

        # EXCLUI pagamentos via "Voucher Loja"
        if 'Forma Pgto' in df_web.columns:
            mask_voucher = (
                df_web['Forma Pgto']
                .astype(str).str.strip().str.casefold()
                .eq('voucher loja')
            )
            df_web = df_web[~mask_voucher]

        df_web['year'] = df_web['data_pg'].dt.year
        df_web['month_num'] = df_web['data_pg'].dt.month

        # Monta séries mês a mês (ano anterior vs ano atual)
        for m in months:
            mask_prev = (df_web['year'] == ano_atual - 1) & (df_web['month_num'] == m)
            mask_curr = (df_web['year'] == ano_atual)     & (df_web['month_num'] == m)

            # Faturamento
            sum_prev = float(df_web.loc[mask_prev, 'valor_pago'].sum())
            sum_curr = float(df_web.loc[mask_curr, 'valor_pago'].sum())
            fat_web_prev[m-1] = sum_prev
            fat_web_curr[m-1] = sum_curr

            # Quantidade de vendas (linhas pagas)
            cnt_prev = int(mask_prev.sum())
            cnt_curr = int(mask_curr.sum())
            vendas_web_prev[m-1] = cnt_prev
            vendas_web_curr[m-1] = cnt_curr

            # Ticket médio (mês)
            ticket_web_prev[m-1] = round(sum_prev / cnt_prev, 2) if cnt_prev else 0.0
            ticket_web_curr[m-1] = round(sum_curr / cnt_curr, 2) if cnt_curr else 0.0

    # --- 16) Renderiza template ---
    return render_template(
        'dashboard.html',
        labels=labels,
        saldo=saldo,
        receitas=receitas,
        pagamentos=pagamentos_abs,
        geracao=geracao,
        consult_pct=consult_pct,
        bpo_pct=bpo_pct,
        start=start,
        end=end,
        contas_all=contas_all,
        selected_contas=selected_contas,
        centros_all=centros_all,
        selected_cc=selected_cc,
        month_labels=month_labels,
        fat_prev=fat_prev,
        fat_curr=fat_curr,
        proj_prev=proj_prev,
        proj_curr=proj_curr,
        ticket_prev=ticket_prev,
        ticket_curr=ticket_curr,
        inadimplencia=inadimplencia,
        inadimpl_count=inadimpl_count,
        collab_counts=collab_counts,
        rev_per_collab=rev_per_collab,
        rev_prev=rev_prev,
        rev_curr=rev_curr,
        fat_web_prev=fat_web_prev,
        fat_web_curr=fat_web_curr,
        vendas_web_prev=vendas_web_prev,
        vendas_web_curr=vendas_web_curr,
        ticket_web_prev=ticket_web_prev,
        ticket_web_curr=ticket_web_curr
)


CREDENTIALS_PATH = os.path.join(app.root_path, 'dados', 'credentials.json')

# Leitura e escrita de credenciais
from datetime import datetime

def load_credentials():
    if not os.path.exists(CREDENTIALS_PATH):
        return []
    with open(CREDENTIALS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_credentials(creds):
    os.makedirs(os.path.dirname(CREDENTIALS_PATH), exist_ok=True)
    with open(CREDENTIALS_PATH, 'w', encoding='utf-8') as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)

@app.route('/credentials')
@login_required
def credentials():
    creds = load_credentials()
    return render_template('credentials.html', credentials=creds)

@app.route('/credentials/add', methods=['POST'])
@login_required
def add_credential():
    creds = load_credentials()
    data = request.form
    new = {
        'id': int(datetime.now().timestamp() * 1000),
        'conta':        data.get('conta','').strip(),
        'link':         data.get('link','').strip(),
        'usuario':      data.get('usuario','').strip(),
        'senha':        data.get('senha','').strip(),
        'data_criacao': datetime.now().strftime('%Y-%m-%d')
    }
    creds.append(new)
    save_credentials(creds)
    return redirect(url_for('credentials'))

@app.route('/credentials/delete', methods=['POST'])
@login_required
def delete_credential():
    cid = int(request.form['id'])
    creds = [c for c in load_credentials() if c['id'] != cid]
    save_credentials(creds)
    return redirect(url_for('credentials'))

@app.route('/credentials/edit/<int:cid>', methods=['GET','POST'])
@login_required
def edit_credential(cid):
    creds = load_credentials()
    cred = next((c for c in creds if c['id']==cid), None)
    if not cred:
        return redirect(url_for('credentials'))
    if request.method=='POST':
        form = request.form
        cred['conta']   = form.get('conta','').strip()
        cred['link']    = form.get('link','').strip()
        cred['usuario'] = form.get('usuario','').strip()
        cred['senha']   = form.get('senha','').strip()
        save_credentials(creds)
        return redirect(url_for('credentials'))
    return render_template('credentials_edit.html', cred=cred)



import os
import json
import calendar
from collections import OrderedDict
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for
from functools import wraps



GANTT_ACTIVITIES = os.path.join(app.root_path, 'dados', 'gantt_activities.json')
GANTT_LOG        = os.path.join(app.root_path, 'dados', 'gantt_log.json')

def load_gantt():
    if not os.path.exists(GANTT_ACTIVITIES):
        return []
    with open(GANTT_ACTIVITIES, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_gantt(acts):
    os.makedirs(os.path.dirname(GANTT_ACTIVITIES), exist_ok=True)
    with open(GANTT_ACTIVITIES, 'w', encoding='utf-8') as f:
        json.dump(acts, f, ensure_ascii=False, indent=2)

def load_log():
    if not os.path.exists(GANTT_LOG):
        return {}
    with open(GANTT_LOG, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_log(log):
    os.makedirs(os.path.dirname(GANTT_LOG), exist_ok=True)
    with open(GANTT_LOG, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

@app.route('/gantt')
@login_required
def gantt():
    hoje  = date.today()
    year  = int(request.args.get('year',  hoje.year))
    month = int(request.args.get('month', hoje.month))

    # gera dias do mês
    nd = calendar.monthrange(year, month)[1]
    days = [f"{year}-{month:02d}-{d:02d}" for d in range(1, nd+1)]

    # agrupa por MM/YYYY
    grouped_days = OrderedDict()
    grouped_days[f"{month:02d}/{year}"] = days

    activities = load_gantt()
    log        = load_log()

    return render_template('gantt.html',
        activities=activities,
        grouped_days=grouped_days,
        log=log,
        year=year,
        month=month
    )

@app.route('/gantt/add', methods=['POST'])
@login_required
def gantt_add():
    acts = load_gantt()
    form = request.form
    acts.append({
        'id': int(datetime.now().timestamp() * 1000),
        'title':       form['title'].strip(),
        'start_date':  form['start_date'],
        'periodicity': form['periodicity']
    })
    save_gantt(acts)
    return redirect(url_for('gantt',
        year = form.get('year', date.today().year),
        month= form.get('month',date.today().month)
    ))

@app.route('/gantt/edit/<int:aid>', methods=['GET','POST'])
@login_required
def gantt_edit(aid):
    acts = load_gantt()
    act  = next((a for a in acts if a['id']==aid), None)
    if not act:
        return redirect(url_for('gantt'))
    if request.method == 'POST':
        form = request.form
        act['title']       = form['title'].strip()
        act['start_date']  = form['start_date']
        act['periodicity'] = form['periodicity']
        save_gantt(acts)
        return redirect(url_for('gantt',
            year = form.get('year', date.today().year),
            month= form.get('month',date.today().month)
        ))
    # GET
    return render_template('gantt_edit.html',
        act=act,
        year=request.args.get('year', date.today().year),
        month=request.args.get('month', date.today().month)
    )

@app.route('/gantt/delete/<int:aid>', methods=['POST'])
@login_required
def gantt_delete(aid):
    acts = [a for a in load_gantt() if a['id'] != aid]
    save_gantt(acts)
    return redirect(url_for('gantt',
        year=request.args.get('year'),
        month=request.args.get('month')
    ))

@app.route('/gantt/toggle/<int:aid>/<day>', methods=['POST'])
@login_required
def gantt_toggle(aid, day):
    log = load_log()
    key = str(aid)
    dates = set(log.get(key, []))
    if day in dates:
        dates.remove(day)
    else:
        dates.add(day)
    log[key] = sorted(dates)
    save_log(log)
    return ('', 204)

PROD_PATH = os.path.join(app.root_path, 'dados', 'produtividade_consultores.json')
CONS_PATH = os.path.join(app.root_path, 'dados', 'consultores.json')

def load_produtividade():
    """
    Carrega o JSON de produtividade como dict.
    Se não existir ou não for um dict, retorna {}.
    """
    if not os.path.exists(PROD_PATH):
        return {}
    with open(PROD_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # garante que só aceitaremos um dict
    return data if isinstance(data, dict) else {}

def save_produtividade(data):
    os.makedirs(os.path.dirname(PROD_PATH), exist_ok=True)
    with open(PROD_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route('/produtividade_consultores', methods=['GET', 'POST'])
@login_required
def produtividade_consultores():
    # Caminho do JSON de produtividade
    PROD_PATH = os.path.join(app.root_path, 'dados', 'produtividade_consultores.json')

    def load_produtividade():
        if not os.path.exists(PROD_PATH):
            return {}
        with open(PROD_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    def save_produtividade(data):
        os.makedirs(os.path.dirname(PROD_PATH), exist_ok=True)
        with open(PROD_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # 1) Determina mês/ano visualizados
    hoje  = date.today()
    year  = int(request.args.get('year', hoje.year))
    month = int(request.args.get('month', hoje.month))
    ym    = f"{year}-{month:02d}"

    # 2) Carrega todos os dados e os do mês corrente
    prod    = load_produtividade()
    valores = prod.get(ym, {})

    # 3) POST: adicionar, excluir ou salvar produtividade
    if request.method == 'POST':
        action = request.form.get('action')

        # 3a) Adicionar novo consultor
        if action == 'add_consultor':
            nome_novo = request.form['nome_novo'].strip()
            if nome_novo and nome_novo not in valores:
                valores[nome_novo] = {'alocacoes': 0, 'disponibilidade': 0, 'livres': 0}
                prod[ym] = valores
                save_produtividade(prod)
                flash(f'✅ Consultor “{nome_novo}” adicionado em {ym}!', 'success')
            else:
                flash('⚠️ Nome inválido ou já existente.', 'warning')
            return redirect(url_for('produtividade_consultores', year=year, month=month))

        # 3b) Excluir consultor do mês
        if request.form.get('delete_consultor'):
            nome_del = request.form['delete_consultor']
            valores.pop(nome_del, None)
            prod[ym] = valores
            save_produtividade(prod)
            flash(f'🗑️ Consultor “{nome_del}” removido de {ym}.', 'info')
            return redirect(url_for('produtividade_consultores', year=year, month=month))

        # 3c) Salvar produtividade e renomeações
        new_vals = {}
        for old_nome, v in valores.items():
            new_nome = request.form.get(f'name_{old_nome}', old_nome).strip() or old_nome
            alloc     = int(request.form.get(f'alloc_{old_nome}', 0))
            disp      = int(request.form.get(f'disp_{old_nome}', 0))
            livres    = max(0, disp - alloc)
            new_vals[new_nome] = {
                'alocacoes': alloc,
                'disponibilidade': disp,
                'livres': livres
            }
        prod[ym] = new_vals
        save_produtividade(prod)
        flash(f'💾 Produtividade de {ym} atualizada!', 'success')
        return redirect(url_for('produtividade_consultores', year=year, month=month))

    # 4) Monta histórico de ocupação para o gráfico de linha
    history_labels = []
    history_data   = []
    for key in sorted(prod.keys()):
        month_data   = prod[key] or {}
        total_alloc  = sum(item['alocacoes'] for item in month_data.values())
        total_disp   = sum(item['disponibilidade'] for item in month_data.values())
        pct_ocupado  = (total_alloc / total_disp * 100) if total_disp else 0
        history_labels.append(key)
        history_data.append(round(pct_ocupado, 1))

    # 5) Navegação mês anterior/próximo
    prev_m = month - 1 or 12
    prev_y = year     if month >  1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year     if month < 12 else year + 1

    # 6) Renderiza template com todas as variáveis
    return render_template(
        'produtividade_consultores.html',
        valores=valores,
        history_labels=history_labels,
        history_data=history_data,
        year=year, month=month,
        prev_year=prev_y, prev_month=prev_m,
        next_year=next_y, next_month=next_m
    )




# Caminho do arquivo de configuração
CONFIG_MODULES = os.path.join(app.root_path, 'dados', 'modules_config.json')

# Definição de todos os módulos do sistema
MODULES_INFO = [
    # Módulos principais
    {'key':'dashboard',   'endpoint':'dashboard',          'icon':'bi-speedometer2',    'label':'Dashboard',           'type':'main'},
    {'key':'fluxo',       'endpoint':'fluxo',              'icon':'bi-cash-coin',       'label':'Fluxo de Caixa',      'type':'main'},
    {'key':'dre',         'endpoint':'index',              'icon':'bi-columns-gap',     'label':'DRE',                 'type':'main'},
    {'key':'gantt',       'endpoint':'gantt',              'icon':'bi-calendar-event',  'label':'Gantt',               'type':'main'},
    {'key':'todo',        'endpoint':'todo_board',         'icon':'bi-check2-square',   'label':'To-Do',               'type':'main'},
    {'key':'credentials', 'endpoint':'credentials',        'icon':'bi-key',             'label':'Credenciais',         'type':'main'},
    {'key':'prod_consult', 'endpoint':'produtividade_consultores', 'icon':'bi-bar-chart-line',  'label':'Produtividade',          'type':'main'},
    {'key':'prod_bpo', 'endpoint':'produtividade_bpo', 'icon':'bi-bar-chart-line', 'label':'Produtividade BPO', 'type':'main'},
    {'key':'contaazul',   'endpoint':'conectar_contaazul', 'icon':'bi-link-45deg',      'label':'Conectar Conta Azul',  'type':'main'},
    {'key':'producao',       'endpoint':'producao_index',     'icon':'bi-stopwatch',       'label':'Produção',                'type':'main'},
    {'key':'producao_hist',  'endpoint':'producao_historico', 'icon':'bi-clock-history',   'label':'Histórico de Produção',   'type':'main'},
    

    # Módulos de configuração
    {'key':'groups',      'endpoint':'cadastro_grupos',    'icon':'bi-folder-plus',     'label':'Cadastro de Grupos',   'type':'config'},
    {'key':'adjust',      'endpoint':'ajuste_categorias',   'icon':'bi-sliders2',        'label':'Ajustes de Cadastros', 'type':'config'},
    {'key':'order',       'endpoint':'ajuste_ordem_dre',    'icon':'bi-list-ol',         'label':'Ordem da DRE',        'type':'config'},
    {'key':'fields',      'endpoint':'campos_personalizados','icon':'bi-calculator',      'label':'Campos Personalizados','type':'config'},
    {'key':'users',       'endpoint':'cadastro_usuarios',   'icon':'bi-people',          'label':'Usuários',            'type':'config'},
    {'key':'modules',     'endpoint':'settings_modules',    'icon':'bi-grid',            'label':'Gerenciar Módulos',    'type':'config'},

        # Módulos de gráficos
    {'key': 'chart_fat',      'endpoint': None, 'icon': 'bi-bar-chart',              'label': 'Faturamento Mensal',           'type': 'chart'},
    {'key': 'chart_proj',     'endpoint': None, 'icon': 'bi-bar-chart-line',         'label': 'Quantidade de Projetos',       'type': 'chart'},
    {'key': 'chart_ticket',   'endpoint': None, 'icon': 'bi-ticket-perforated',      'label': 'Ticket Médio',                 'type': 'chart'},
    {'key': 'chart_rev_collab', 'endpoint': None, 'icon': 'bi-people-fill',       'label': 'Faturamento por Colaborador', 'type': 'chart'},
    {'key': 'chart_consult',  'endpoint': None, 'icon': 'bi-people-fill',            'label': '% Ocupação Consultores',       'type': 'chart'},
    {'key': 'chart_bpo',      'endpoint': None, 'icon': 'bi-people',                 'label': '% Ocupação BPO',               'type': 'chart'},
    {'key': 'chart_inad',     'endpoint': None, 'icon': 'bi-exclamation-triangle',   'label': 'Inadimplência Mensal',         'type': 'chart'},
    {'key': 'chart_recpag',   'endpoint': None, 'icon': 'bi-currency-exchange',      'label': 'Receitas x Pagamentos',        'type': 'chart'},
    {'key': 'chart_gen',      'endpoint': None, 'icon': 'bi-cash-stack',            'label': 'Geração de Caixa Mensal',      'type': 'chart'},
    {'key': 'chart_saldo',    'endpoint': None, 'icon': 'bi-cash',                  'label': 'Saldo Final',                  'type': 'chart'},
    {'key': 'chart_fat_web', 'endpoint': None, 'icon': 'bi-bar-chart', 'label': 'Faturamento Mensal (WEB)', 'type': 'chart'},
    {'key': 'chart_ticket_web', 'endpoint': None, 'icon': 'bi-ticket-perforated', 'label': 'Ticket Médio (WEB)',  'type': 'chart'},
    {'key': 'chart_qtd_web',    'endpoint': None, 'icon': 'bi-bar-chart',        'label': 'Qtd. de Vendas (WEB)', 'type': 'chart'},
]

def load_enabled_modules():
    """Retorna lista de chaves de módulos habilitados."""
    if not os.path.exists(CONFIG_MODULES):
        # Na primeira execução, habilita tudo por padrão
        return [m['key'] for m in MODULES_INFO]
    with open(CONFIG_MODULES, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_enabled_modules(enabled):
    """Persiste lista de módulos habilitados em JSON."""
    os.makedirs(os.path.dirname(CONFIG_MODULES), exist_ok=True)
    with open(CONFIG_MODULES, 'w', encoding='utf-8') as f:
        json.dump(enabled, f, ensure_ascii=False, indent=2)

@app.context_processor
def inject_modules_config():
    """
    Injeta em todos os templates:
      - modules_info: metadados de todos os módulos
      - enabled_modules: lista de chaves habilitadas
    """
    return {
        'modules_info':  MODULES_INFO,
        'enabled_modules': load_enabled_modules()
    }

@app.route('/settings/modules', methods=['GET', 'POST'])
@login_required
def settings_modules():
    """
    Tela de Gerenciamento de Módulos:
      - GET: exibe checkboxes com todos os módulos
      - POST: atualiza o JSON de configurações e recarrega a página
    """
    enabled = load_enabled_modules()
    if request.method == 'POST':
        selected = request.form.getlist('modules')
        save_enabled_modules(selected)
        return redirect(url_for('settings_modules'))
    return render_template(
        'settings_modules.html',
        modules_info=MODULES_INFO,
        enabled_modules=enabled
    )


# Paths dos JSONs
CONS_PATH = os.path.join(app.root_path, 'dados', 'produtividade_consultores.json')
BPO_PATH  = os.path.join(app.root_path, 'dados', 'produtividade_bpo.json')

def load_bpo():
    if not os.path.exists(BPO_PATH):
        return {}
    with open(BPO_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}

def save_bpo(data):
    os.makedirs(os.path.dirname(BPO_PATH), exist_ok=True)
    with open(BPO_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route('/produtividade_bpo', methods=['GET', 'POST'])
@login_required
def produtividade_bpo():
    # Determina mês/ano
    hoje  = date.today()
    year  = int(request.args.get('year', hoje.year))
    month = int(request.args.get('month', hoje.month))
    ym    = f"{year}-{month:02d}"

    # Carrega dados
    prod    = load_bpo()
    valores = prod.get(ym, {})

    if request.method == 'POST':
        action = request.form.get('action')

        # Adicionar um novo BPO
        if action == 'add_consultor':  # manter action == 'add_consultor' para compatibilidade
            nome_novo = request.form['nome_novo'].strip()
            if nome_novo and nome_novo not in valores:
                valores[nome_novo] = {'alocacoes':0,'disponibilidade':0,'livres':0}
                prod[ym] = valores
                save_bpo(prod)
                flash(f'✅ BPO “{nome_novo}” adicionado em {ym}!', 'success')
            else:
                flash('⚠️ Nome inválido ou já existente.', 'warning')
            return redirect(url_for('produtividade_bpo', year=year, month=month))

        # Excluir
        if request.form.get('delete_consultor'):
            nome_del = request.form['delete_consultor']
            valores.pop(nome_del, None)
            prod[ym] = valores
            save_bpo(prod)
            flash(f'🗑️ BPO “{nome_del}” removido de {ym}.', 'info')
            return redirect(url_for('produtividade_bpo', year=year, month=month))

        # Salvar produtividade
        new_vals = {}
        for old, v in valores.items():
            new_nome = request.form.get(f'name_{old}', old).strip() or old
            alloc     = int(request.form.get(f'alloc_{old}', 0))
            disp      = int(request.form.get(f'disp_{old}', 0))
            livres    = max(0, disp - alloc)
            new_vals[new_nome] = {
                'alocacoes': alloc,
                'disponibilidade': disp,
                'livres': livres
            }
        prod[ym] = new_vals
        save_bpo(prod)
        flash(f'💾 Produtividade BPO de {ym} atualizada!', 'success')
        return redirect(url_for('produtividade_bpo', year=year, month=month))

    # Navegação de meses
    prev_m = month-1 or 12
    prev_y = year if month>1 else year-1
    next_m = month+1 if month<12 else 1
    next_y = year if month<12 else year+1

    return render_template(
        'produtividade_bpo.html',
        valores=valores,
        year=year, month=month,
        prev_year=prev_y, prev_month=prev_m,
        next_year=next_y, next_month=next_m
    )
# --- CONTROLE DE PRODUÇÃO -----------------------------------------------------
from flask import jsonify, render_template, request, redirect, url_for, flash
import os, json, tempfile, re
from pathlib import Path
from datetime import datetime
from time import time
from zoneinfo import ZoneInfo   # << timezone Fortaleza

# --------- Configuração de timezone (Fortaleza) ---------
FORTALEZA_TZ = ZoneInfo("America/Fortaleza")

def _now_dt_naive_fortaleza():
    """Retorna datetime NAIVE no fuso de Fortaleza (sem tzinfo)."""
    return datetime.now(FORTALEZA_TZ).replace(tzinfo=None)

def _now_iso():
    """String 'YYYY-MM-DD HH:MM:SS' no fuso de Fortaleza."""
    return _now_dt_naive_fortaleza().strftime("%Y-%m-%d %H:%M:%S")
# ---------------------------------------------------------

PROD_PATH = Path(app.root_path) / "dados" / "producao.json"
PROD_PATH.parent.mkdir(parents=True, exist_ok=True)

RET_PATH = Path(app.root_path) / "dados" / "retiradas_web_locacao.json"  # base pendências

def _load_producao():
    """Carrega JSON; migra registros antigos p/ garantir id e data_termino quando aplicável."""
    if not PROD_PATH.exists() or PROD_PATH.stat().st_size == 0:
        return {"andamento": [], "historico": []}
    try:
        with PROD_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return {"andamento": [], "historico": []}

    changed = False

    for r in data.get("andamento", []):
        if "id" not in r:
            r["id"] = int(time() * 1000); changed = True
        if "data_termino" not in r:
            r["data_termino"] = None; changed = True

    for r in data.get("historico", []):
        if "id" not in r:
            r["id"] = int(time() * 1000); changed = True
        if "data_termino" not in r:
            if r.get("hora_termino"):
                r["data_termino"] = r.get("data")
            else:
                r["data_termino"] = None
            changed = True

    if changed:
        _save_producao(data)

    return data

def _save_producao(data: dict):
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(PROD_PATH.parent), prefix="producao.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmpf:
            json.dump(data, tmpf, ensure_ascii=False, indent=2)
            tmpf.flush(); os.fsync(tmpf.fileno())
        os.replace(tmp_path, PROD_PATH)
    finally:
        try:
            if os.path.exists(tmp_path): os.remove(tmp_path)
        except Exception:
            pass

def _validate_piece_operator(peca: str, operador: str):
    peca = (peca or "").strip()
    operador = (operador or "").strip()
    if not peca:
        return False, "Informe a peça."
    if not operador.isdigit():
        return False, "Operador deve ser numérico."
    return True, ""

def _parse_hora(val: str):
    """Aceita HH:MM ou HH:MM:SS; retorna HH:MM:SS (valida formato)."""
    val = (val or "").strip()
    if not val:
        return None
    if len(val) == 5:
        val = val + ":00"
    datetime.strptime(val, "%H:%M:%S")
    return val

def _recalcular_duracao(reg: dict):
    """
    Recalcula duracao_seg e duracao_hms usando valores NAIVE (no fuso de Fortaleza).
    """
    di = reg.get("data")
    hi = reg.get("hora_inicio")
    dt = reg.get("data_termino")
    ht = reg.get("hora_termino")

    if not (di and hi and dt and ht):
        reg.pop("duracao_seg", None)
        reg.pop("duracao_hms", None)
        return

    t0 = datetime.strptime(f"{di} {hi}", "%Y-%m-%d %H:%M:%S")  # naive Fortaleza
    t1 = datetime.strptime(f"{dt} {ht}", "%Y-%m-%d %H:%M:%S")  # naive Fortaleza
    delta = max(0, int((t1 - t0).total_seconds()))
    reg["duracao_seg"] = delta
    h = delta // 3600
    m = (delta % 3600) // 60
    s = delta % 60
    reg["duracao_hms"] = f"{h:02d}:{m:02d}:{s:02d}"

# -------------------------
# Helpers p/ inferir contrato das pendências (simples)
# -------------------------
def _norm(s: str) -> str:
    s = (s or "").strip().upper()
    try:
        s = s.encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass
    return re.sub(r"[^A-Z0-9]", "", s)

def _infer_contract_for_piece(peca: str) -> str:
    """Busca contrato nas retiradas pendentes (RetiredDate vazio)."""
    try:
        if not RET_PATH.exists() or RET_PATH.stat().st_size == 0:
            return "manutenção/NDI"
        with RET_PATH.open("r", encoding="utf-8") as f:
            bank = json.load(f)
    except Exception:
        return "manutenção/NDI"

    key = _norm(peca)
    if not key:
        return "manutenção/NDI"

    # achatar meses -> linhas
    linhas = []
    if isinstance(bank, dict):
        for _, bucket in bank.items():
            arr = []
            if isinstance(bucket, dict):
                if isinstance(bucket.get("aaData"), list):
                    arr = bucket["aaData"]
                elif isinstance(bucket.get("data"), list):
                    arr = bucket["data"]
            for r in arr:
                if isinstance(r, list):
                    order_id     = (r[0] if len(r) > 0 else "")
                    customer     = (r[1] if len(r) > 1 else "")
                    product_id   = (r[2] if len(r) > 2 else "")
                    product_name = (r[4] if len(r) > 4 else "")
                    retire_date  = (r[5] if len(r) > 5 else "")
                    retired_date = (r[6] if len(r) > 6 else "")
                else:
                    order_id     = r.get("OrderId") or r.get("orderid") or r.get("0") or ""
                    customer     = r.get("CustomerName") or r.get("customername") or r.get("1") or ""
                    product_id   = r.get("ProductId") or r.get("productid") or r.get("2") or ""
                    product_name = r.get("ProductName") or r.get("productname") or r.get("porductname") or r.get("4") or ""
                    retire_date  = r.get("RetireDate") or r.get("retiredate") or r.get("5") or ""
                    retired_date = r.get("RetiredDate") or r.get("retireddate") or r.get("6") or ""

                retired_date = str(retired_date or "").strip()
                if retired_date in ("", "-", "--", "00/00/0000"):  # pendentes
                    linhas.append({
                        "Contrato": str(order_id or "").strip(),
                        "ProdId":   str(product_id or "").strip(),
                        "ProdName": str(product_name or "").strip(),
                    })

    if not linhas:
        return "manutenção/NDI"

    # 1) match exato por código
    m1 = [x for x in linhas if _norm(x["ProdId"]) == key]
    if m1:
        try:
            m1.sort(key=lambda x: int(x["Contrato"]))
        except Exception:
            m1.sort(key=lambda x: str(x["Contrato"]))
        return m1[0]["Contrato"] or "manutenção/NDI"

    # 2) fallback por nome contendo
    m2 = [x for x in linhas if key in _norm(x["ProdName"]) or _norm(x["ProdName"]) in key]
    if m2:
        try:
            m2.sort(key=lambda x: int(x["Contrato"]))
        except Exception:
            m2.sort(key=lambda x: str(x["Contrato"]))
        return m2[0]["Contrato"] or "manutenção/NDI"

    return "manutenção/NDI"

# ------------------------- ROTAS -------------------------

@app.route("/producao")
@login_required
def producao_index():
    return render_template("producao.html")

@app.route("/producao/iniciar", methods=["POST"])
@login_required
def producao_iniciar():
    peca = request.form.get("peca")
    operador = request.form.get("operador")
    ok, msg = _validate_piece_operator(peca, operador)
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 400

    db = _load_producao()
    existe = next((x for x in db["andamento"] if x["peca"] == peca), None)
    if existe:
        return jsonify({"ok": False, "msg": "Já existe processo em andamento para esta peça."}), 409

    agora = _now_iso()  # string em Fortaleza (naive)
    registro = {
        "id": int(time() * 1000),
        "peca": (peca or "").strip(),
        "operador": int(operador),
        "data": agora.split(" ")[0],
        "hora_inicio": agora.split(" ")[1],
        "data_termino": None,
        "hora_termino": None,
        "status": "aberto",
        "ts_inicio": agora,  # também naive Fortaleza
        # não grava contrato aqui — será inferido e salvo no ENCERRAMENTO
    }
    db["andamento"].append(registro)
    _save_producao(db)
    return jsonify({"ok": True})

@app.route("/producao/encerrar", methods=["POST"])
@login_required
def producao_encerrar():
    peca = (request.form.get("peca") or "").strip()
    if not peca:
        return jsonify({"ok": False, "msg": "Informe a peça para encerrar."}), 400

    db = _load_producao()
    idx = next((i for i, x in enumerate(db["andamento"]) if x["peca"] == peca), None)
    if idx is None:
        return jsonify({"ok": False, "msg": "Não há processo em andamento para esta peça."}), 404

    agora = _now_iso()  # naive Fortaleza
    reg = db["andamento"].pop(idx)
    reg["data_termino"]  = agora.split(" ")[0]
    reg["hora_termino"]  = agora.split(" ")[1]
    reg["status"] = "encerrado"

    # Cálculo usando ts_inicio (naive Fortaleza) e agora (naive Fortaleza)
    t0 = datetime.strptime(reg["ts_inicio"], "%Y-%m-%d %H:%M:%S")
    t1 = datetime.strptime(agora, "%Y-%m-%d %H:%M:%S")
    delta = max(0, int((t1 - t0).total_seconds()))
    reg["duracao_seg"] = delta
    h = delta // 3600
    m = (delta % 3600) // 60
    s = delta % 60
    reg["duracao_hms"] = f"{h:02d}:{m:02d}:{s:02d}"

    # >>> NOVO: inferir e salvar CONTRATO
    contrato = _infer_contract_for_piece(reg.get("peca"))
    reg["contrato"] = contrato  # ficará salvo no histórico

    # >>> NOVO: snapshot no formato da tabela "Em andamento"
    reg["andamento_snapshot"] = {
        "peca": reg.get("peca"),
        "operador": reg.get("operador"),
        "data": reg.get("data"),
        "hora_inicio": reg.get("hora_inicio"),
        "elapsed_seg": reg.get("duracao_seg", 0),
        "contrato": contrato,
    }

    db["historico"].append(reg)
    _save_producao(db)
    return jsonify({"ok": True, "duracao_hms": reg["duracao_hms"]})

@app.route("/producao/api/andamento")
@login_required
def producao_api_andamento():
    """Fornece itens em andamento, com segundos corridos (sempre no fuso de Fortaleza)."""
    db = _load_producao()

    # 'now' também é NAIVE em Fortaleza para bater com o ts_inicio salvo
    now = _now_dt_naive_fortaleza()

    itens = []
    for r in db["andamento"]:
        t0 = datetime.strptime(r["ts_inicio"], "%Y-%m-%d %H:%M:%S")  # naive Fortaleza
        elapsed = int((now - t0).total_seconds())
        itens.append({
            "peca": r["peca"],
            "operador": r["operador"],
            "data": r["data"],
            "hora_inicio": r["hora_inicio"],
            "elapsed_seg": max(0, elapsed),
            # Se algum registro já tiver contrato (futuro), devolve também:
            "contrato": r.get("contrato"),
        })
    return jsonify(itens)


@app.route("/producao/historico")
@login_required
def producao_historico():
    """Lista do histórico (encerrados)."""
    db = _load_producao()
    def _key(x):
        dt = x.get("data_termino") or x.get("data") or ""
        ht = x.get("hora_termino") or x.get("hora_inicio") or ""
        return (dt, ht)
    hist = sorted(db["historico"], key=_key, reverse=True)
    return render_template("producao_historico.html", historico=hist)

# --- Edição e exclusão de histórico ------------------------------------------

@app.route("/producao/historico/edit/<int:hid>", methods=["GET", "POST"])
@login_required
def producao_historico_edit(hid):
    db = _load_producao()
    reg = next((r for r in db["historico"] if r.get("id") == hid), None)
    if not reg:
        flash("Registro não encontrado no histórico.", "danger")
        return redirect(url_for("producao_historico"))

    if request.method == "POST":
        peca = (request.form.get("peca") or "").strip()
        operador = (request.form.get("operador") or "").strip()
        data = (request.form.get("data") or "").strip()
        data_termino = (request.form.get("data_termino") or "").strip()
        hora_inicio = request.form.get("hora_inicio") or ""
        hora_termino = request.form.get("hora_termino") or ""

        if not peca:
            flash("Informe a peça.", "warning")
            return redirect(request.url)
        if not operador.isdigit():
            flash("Operador deve ser numérico.", "warning")
            return redirect(request.url)
        try:
            datetime.strptime(data, "%Y-%m-%d")
            datetime.strptime(data_termino, "%Y-%m-%d")
            hora_inicio = _parse_hora(hora_inicio)
            hora_termino = _parse_hora(hora_termino)
        except Exception:
            flash("Data ou hora inválidas. Use data YYYY-MM-DD e hora HH:MM ou HH:MM:SS.", "warning")
            return redirect(request.url)

        reg["peca"] = peca
        reg["operador"] = int(operador)
        reg["data"] = data
        reg["data_termino"] = data_termino
        reg["hora_inicio"] = hora_inicio
        reg["hora_termino"] = hora_termino
        _recalcular_duracao(reg)

        _save_producao(db)
        flash("Registro atualizado com sucesso.", "success")
        return redirect(url_for("producao_historico"))

    return render_template("producao_historico_edit.html", r=reg)

@app.route("/producao/historico/delete", methods=["POST"])
@login_required
def producao_historico_delete():
    try:
        hid = int(request.form.get("id"))
    except Exception:
        flash("ID inválido.", "danger")
        return redirect(url_for("producao_historico"))

    db = _load_producao()
    antes = len(db["historico"])
    db["historico"] = [r for r in db["historico"] if r.get("id") != hid]
    if len(db["historico"]) == antes:
        flash("Registro não encontrado.", "warning")
        return redirect(url_for("producao_historico"))

    _save_producao(db)
    flash("Registro excluído.", "info")
    return redirect(url_for("producao_historico"))
# -----------------------------------------------------------------------------#

# === WEBLOCAÇÃO (JSON): Coleta de retiradas do mês anterior, vigente e do próximo mês + agendador 00:00/12:00 Fortaleza ===
import os, json, calendar, tempfile, logging, threading, time as _time
from datetime import datetime, date, timedelta
from urllib.parse import urlencode
from pathlib import Path
import requests
from flask import jsonify, current_app
from bs4 import BeautifulSoup  # opcional (para inspeções)
# Usa seu login automático e credenciais do consulta_web()
from scripts.consulta_web import login_and_build_session, EMAIL, PASSWORD

# -------------------------------------------------------------------------------------
# login_required SEGURO (funciona com ou sem Flask-Login configurado)
# -------------------------------------------------------------------------------------
from functools import wraps
try:
    import flask_login  # noqa
    _has_flask_login = True
except Exception:
    _has_flask_login = False

def login_required(view_func):
    """
    - Se Flask-Login não estiver presente OU LoginManager não estiver configurado:
      no-op (não exige login, e não quebra).
    - Se LoginManager existir: exige usuário autenticado.
    """
    if not _has_flask_login:
        return view_func

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        try:
            from flask import request, redirect, url_for
            from flask_login import current_user
            lm = getattr(current_app, "login_manager", None)

            if lm is None:
                # Flask-Login instalado mas não inicializado => não exigir login
                return view_func(*args, **kwargs)

            if current_user.is_authenticated:
                return view_func(*args, **kwargs)

            login_view = getattr(lm, "login_view", None)
            if login_view and request.accept_mimetypes.accept_html:
                return redirect(url_for(login_view))
            return jsonify({"ok": False, "erro": "auth_required"}), 401
        except Exception:
            # Nunca derrubar a rota por erro no auth
            return view_func(*args, **kwargs)
    return wrapper

# -------------------------------------------------------------------------------------
# Utilitários de logging
# -------------------------------------------------------------------------------------
_LOGGER = logging.getLogger("weblocacao_json")
if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    _LOGGER.addHandler(_h)

def _log(level: str, msg: str):
    try:
        current_app.logger.log(getattr(logging, level.upper(), logging.INFO), msg)
    except Exception:
        getattr(_LOGGER, level if hasattr(_LOGGER, level) else "info")(msg)

# -------------------------------------------------------------------------------------
# Paths seguros (não assumir 'app' no import-time)
# -------------------------------------------------------------------------------------
def _resolve_out_path():
    try:
        base = os.path.join(current_app.root_path, "dados")
    except Exception:
        base = os.path.join(Path(__file__).resolve().parent, "dados")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "retiradas_web_locacao.json")  # único arquivo

# -------------------------------------------------------------------------------------
# Datas (respeita Fortaleza quando disponível)
# -------------------------------------------------------------------------------------
def _tz_fortaleza():
    tz = globals().get("FORTALEZA_TZ")
    if tz is not None:
        return tz
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Fortaleza")
    except Exception:
        return None

def _now_fortaleza():
    tz = _tz_fortaleza()
    return datetime.now(tz) if tz else datetime.now()

def _mes_vigente_br():
    hoje  = _now_fortaleza().date()
    first = date(hoje.year, hoje.month, 1)
    last  = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    return first.strftime("%d/%m/%Y"), last.strftime("%d/%m/%Y"), first.strftime("%Y-%m")

def _mes_anterior_atual_proximo_br():
    """
    Retorna lista de 3 entradas (tuplas):
    [
      (ini_br_ant, fim_br_ant, 'YYYY-MM' do mês anterior),
      (ini_br_atual, fim_br_atual, 'YYYY-MM' do mês atual),
      (ini_br_prox, fim_br_prox, 'YYYY-MM' do próximo mês)
    ]
    """
    hoje = _now_fortaleza().date()

    # --- mês atual ---
    first_cur = date(hoje.year, hoje.month, 1)
    last_cur  = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])

    # --- mês anterior (com rollover de ano) ---
    if hoje.month == 1:
        py, pm = hoje.year - 1, 12
    else:
        py, pm = hoje.year, hoje.month - 1
    first_prev = date(py, pm, 1)
    last_prev  = date(py, pm, calendar.monthrange(py, pm)[1])

    # --- próximo mês (com rollover de ano) ---
    if hoje.month == 12:
        ny, nm = hoje.year + 1, 1
    else:
        ny, nm = hoje.year, hoje.month + 1
    first_next = date(ny, nm, 1)
    last_next  = date(ny, nm, calendar.monthrange(ny, nm)[1])

    return [
        (first_prev.strftime("%d/%m/%Y"), last_prev.strftime("%d/%m/%Y"), first_prev.strftime("%Y-%m")),
        (first_cur.strftime("%d/%m/%Y"),  last_cur.strftime("%d/%m/%Y"),  first_cur.strftime("%Y-%m")),
        (first_next.strftime("%d/%m/%Y"), last_next.strftime("%d/%m/%Y"), first_next.strftime("%Y-%m")),
    ]

# -------------------------------------------------------------------------------------
# IO seguro
# -------------------------------------------------------------------------------------
def _safe_json_load(path):
    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _safe_json_save_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="webloc.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception:
            pass

def _count_rows(obj) -> int:
    if isinstance(obj, dict):
        for k in ("data", "aaData", "rows", "items"):
            v = obj.get(k)
            if isinstance(v, list):
                return len(v)
        return 0
    if isinstance(obj, list):
        return len(obj)
    return 0

# -------------------------------------------------------------------------------------
# Montagem de URL para o endpoint JSON (mesmos parâmetros do exemplo)
# -------------------------------------------------------------------------------------
def _build_json_url(start_br: str, end_br: str, start_index: int, page_len: int) -> str:
    base = "https://www.weblocacao.com.br/schedule/getajaxdata"
    params = {
        "startDate": start_br,
        "endDate":   end_br,
        "scheduleType": "Output",
        "idStore": "21095",
        "tested": "",
        "productType": "",
        # duas categorias simultâneas:
        "SelectedCategories": ["18487", "18488"],
        "idUser": "",
        # parâmetros estilo DataTables
        "sEcho": "1",
        "iColumns": "10",
        "sColumns": "OrderId,CustomerName,ProductId,Quantity,ProductName,RetireDate,RetiredDate,,,",
        "iDisplayStart": str(start_index),
        "iDisplayLength": str(page_len),
        "mDataProp_0": "0", "bSortable_0": "true",
        "mDataProp_1": "1", "bSortable_1": "true",
        "mDataProp_2": "2", "bSortable_2": "true",
        "mDataProp_3": "3", "bSortable_3": "true",
        "mDataProp_4": "4", "bSortable_4": "true",
        "mDataProp_5": "5", "bSortable_5": "true",
        "mDataProp_6": "6", "bSortable_6": "true",
        "mDataProp_7": "12","bSortable_7": "true",
        "mDataProp_8": "7", "bSortable_8": "true",
        "mDataProp_9": "0", "bSortable_9": "true",
        "iSortCol_0": "0",
        "sSortDir_0": "asc",
        "iSortingCols": "1"
    }
    return f"{base}?{urlencode(params, doseq=True, encoding='utf-8', safe='/')}"

def _json_headers():
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": "https://www.weblocacao.com.br/Schedule/Output",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
        "X-Requested-With": "XMLHttpRequest",
    }

# -------------------------------------------------------------------------------------
# Coleta JSON por mês (com paginação automática de 500 em 500)
# -------------------------------------------------------------------------------------
def _fetch_month_json(session: requests.Session, inicio_br: str, fim_br: str, page_len: int = 500):
    rows = []
    start_index = 0
    tried_relogin = False

    while True:
        url = _build_json_url(inicio_br, fim_br, start_index, page_len)
        _log("info", f"[weblocacao] GET JSON {inicio_br}..{fim_br} offset={start_index}")
        resp = session.get(url, headers=_json_headers(), timeout=60, allow_redirects=True)

        # sessão expirada?
        redir_login = False
        try:
            redir_login = ("authentication/login" in (resp.url or "").lower())
        except Exception:
            pass

        if resp.status_code in (401, 403) or redir_login:
            if tried_relogin:
                raise RuntimeError("Sessão expirada e re-login já tentado.")
            _log("info", "[weblocacao] sessão expirada; refazendo login…")
            session = login_and_build_session(EMAIL, PASSWORD, headless=True)
            tried_relogin = True
            continue  # refaz esta página com nova sessão

        # tenta JSON (algumas vezes pode vir HTML por erro do servidor)
        try:
            payload = resp.json()
        except Exception as e:
            if not tried_relogin:
                _log("info", f"[weblocacao] resposta não-JSON ({e}); revalidando sessão…")
                session = login_and_build_session(EMAIL, PASSWORD, headless=True)
                tried_relogin = True
                continue
            raise RuntimeError(f"Resposta inválida do endpoint JSON: {e}")

        # extrai linhas
        page_rows = []
        if isinstance(payload, dict):
            if isinstance(payload.get("aaData"), list):
                page_rows = payload["aaData"]
            elif isinstance(payload.get("data"), list):
                page_rows = payload["data"]

        if not page_rows:
            break  # nada na página -> fim

        rows.extend(page_rows)

        if len(page_rows) < page_len:
            break  # última página

        start_index += page_len  # próxima página

    return {
        "periodo": {"inicio": inicio_br, "fim": fim_br},
        "source": "schedule/getajaxdata",
        "params": {"SelectedCategories": ["18487", "18488"], "idStore": "21095"},
        "aaData": rows  # compatível com DataTables
    }

# -------------------------------------------------------------------------------------
# API principal: baixa mês anterior, mês vigente e próximo mês; salva/mescla num único arquivo
# -------------------------------------------------------------------------------------
def weblocacao_fetch_and_save_month():
    # lista com (inicio_br, fim_br, mes_key) para anterior, vigente e próximo
    periodos = _mes_anterior_atual_proximo_br()

    # Sessão autenticada (Playwright+cookies) — mesmo princípio do consulta_web()
    session = login_and_build_session(EMAIL, PASSWORD, headless=True)

    # Cabeçalhos base para JSON
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
        "Referer": "https://www.weblocacao.com.br/Schedule/Output",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    out_path = _resolve_out_path()
    banco = _safe_json_load(out_path)
    if not isinstance(banco, dict):
        banco = {}

    counts = {}
    total = 0
    for inicio_br, fim_br, mes_key in periodos:
        payload = _fetch_month_json(session, inicio_br, fim_br, page_len=500)
        banco[mes_key] = payload  # sobrescreve SOMENTE o mês correspondente
        c = _count_rows(payload)
        counts[mes_key] = c
        total += c

    _safe_json_save_atomic(out_path, banco)
    return True, "ok", out_path, total, counts

# -------------------------------------------------------------------------------------
# Helper p/ registrar rotas sem depender do app no import-time
# -------------------------------------------------------------------------------------
def _route(app, rule, endpoint, methods):
    def decorator(func):
        app.add_url_rule(rule, endpoint=endpoint, view_func=func, methods=methods)
        return func
    return decorator

# -------------------------------------------------------------------------------------
# Rotas Flask (mantém endpoints/paths)
# -------------------------------------------------------------------------------------
def register_weblocacao(app):
    @_route(app, "/weblocacao/atualizar_retiradas", "weblocacao_atualizar_retiradas_route", methods=["GET"])
    @login_required
    def _atualizar_retiradas_view():
        try:
            ok, msg, out_path, qtd_total, por_mes = weblocacao_fetch_and_save_month()
            return jsonify({
                "ok": ok,
                "mensagem": "Retiradas do mês anterior, vigente e próximo salvas com sucesso." if ok else msg,
                "arquivo": out_path,
                "arquivos": [out_path],
                "itens": qtd_total,
                "itens_por_mes": por_mes
            }), (200 if ok else 500)
        except Exception as e:
            _log("error", f"[weblocacao] Falha: {e}")
            out_path = _resolve_out_path()
            return jsonify({"ok": False, "erro": str(e), "arquivo": out_path, "arquivos": [out_path]}), 500

    @_route(app, "/weblocacao/atualizar_retiradas_debug", "weblocacao_debug_route", methods=["GET"])
    @login_required
    def _debug_view():
        periodos = _mes_anterior_atual_proximo_br()
        urls_preview = [_build_json_url(ini, fim, 0, 500) for (ini, fim, _k) in periodos]
        return jsonify({
            "periodos": [
                {"inicio": ini, "fim": fim, "mes_key": k} for (ini, fim, k) in periodos
            ],
            "json_endpoints_exemplo": urls_preview,
            "saida_esperada": _resolve_out_path()
        })

# -------------------------------------------------------------------------------------
# Agendador diário 00:00 e 12:00 (horário de Fortaleza)
# -------------------------------------------------------------------------------------
_SCHEDULER_STARTED = False

def _next_run_dt(now):
    base_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [
        base_today,
        base_today + timedelta(hours=12),
        base_today + timedelta(days=1),
        base_today + timedelta(days=1, hours=12),
    ]
    return min(dt for dt in candidates if dt > now)

def _scheduler_loop(app):
    with app.app_context():
        while True:
            now = _now_fortaleza()
            nxt = _next_run_dt(now)
            secs = max(1, int((nxt - now).total_seconds()))
            _log("info", f"[weblocacao] próxima execução agendada para {nxt.isoformat()}")
            _time.sleep(secs)
            try:
                ok, msg, out_path, qtd_total, por_mes = weblocacao_fetch_and_save_month()
                _log("info", f"[weblocacao] execução concluída: ok={ok} total={qtd_total} por_mes={por_mes} -> {out_path}")
            except Exception as e:
                _log("error", f"[weblocacao] erro na execução agendada: {e}")

def start_weblocacao_scheduler(app):
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    # evita duplicação com o reloader do Flask
    if (not app.debug) or (os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
        t = threading.Thread(target=_scheduler_loop, args=(app,), daemon=True)
        t.start()
        _SCHEDULER_STARTED = True
        _log("info", "[weblocacao] scheduler iniciado (00:00 e 12:00 - Fortaleza).")

# -------------------------------------------------------------------------------------
# Registro opcional imediato (se 'app' existir no escopo do import)
# -------------------------------------------------------------------------------------
try:
    register_weblocacao(app)           # mantém compatível com seu template/url_for
    start_weblocacao_scheduler(app)    # inicia agendador automático
except NameError:
    # Se não houver 'app' aqui, basta chamar register_weblocacao(app) e
    # start_weblocacao_scheduler(app) no módulo principal onde o Flask app é criado.
    pass


# routes_producao_ret.py  (ou cole no seu módulo principal de rotas)

import os, json
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, jsonify, current_app


bp_producao_ret = Blueprint("producao_ret", __name__)

# ---------- Datas / Janela (terça -> segunda, fuso Fortaleza) ----------
def _today_fortaleza() -> date:
    try:
        tz = ZoneInfo("America/Fortaleza")
        return datetime.now(tz).date()
    except Exception:
        return date.today()

def _window_tue_to_next_mon(today: date) -> tuple[date, date]:
    # terça da semana "corrente" (semana começando na segunda)
    # Mon=0 ... Sun=6 ; queremos a terça (1)
    start = today - timedelta(days=((today.weekday() - 1) % 7))
    end = start + timedelta(days=6)
    return start, end

# ---------- Util ----------
def _dados_path() -> str:
    base = os.path.join(current_app.root_path, "dados")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "retiradas_web_locacao.json")

def _parse_br_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # formatos comuns
    for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # fallback: ISO ou “2025-09-21T00:00:00”
    try:
        return datetime.fromisoformat(s.replace("Z", "").split(" ")[0]).date()
    except Exception:
        return None

def _pick(cellrow, *cands):
    """Extrai valor de um registro (dict com chaves diversas OU lista por índice)."""
    if isinstance(cellrow, dict):
        low = {str(k).lower(): v for k, v in cellrow.items()}
        for c in cands:
            if isinstance(c, str):
                if c in cellrow: return cellrow[c]
                if c.lower() in low: return low[c.lower()]
            elif isinstance(c, int):
                if str(c) in cellrow: return cellrow[str(c)]
        return ""
    if isinstance(cellrow, (list, tuple)):
        for c in cands:
            if isinstance(c, int) and 0 <= c < len(cellrow):
                return cellrow[c]
    return ""

def _is_blank(v) -> bool:
    return v is None or str(v).strip() in ("", "-", "--", "00/00/0000")

# ---------- API ----------
@bp_producao_ret.get("/producao/api/retiradas_web")
def api_ret():
    path = _dados_path()
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return jsonify({"ok": False, "erro": "arquivo_nao_encontrado", "itens": []}), 200

    try:
        with open(path, "r", encoding="utf-8") as f:
            banco = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "erro": f"json_invalido:{e}", "itens": []}), 200

    hoje = _today_fortaleza()
    win_ini, win_fim = _window_tue_to_next_mon(hoje)

    itens = []
    # banco é { "YYYY-MM": { "aaData": [...] , ... }, ... }
    if isinstance(banco, dict):
        for _, payload in banco.items():
            linhas = []
            if isinstance(payload, dict):
                if isinstance(payload.get("aaData"), list):
                    linhas = payload["aaData"]
                elif isinstance(payload.get("data"), list):
                    linhas = payload["data"]

            for r in linhas:
                orderid      = str(_pick(r, "OrderId", "orderid", 0)).strip()
                customername = str(_pick(r, "CustomerName", "customername", 1)).strip()
                productid    = str(_pick(r, "ProductId", "productid", 2)).strip()
                productname  = str(_pick(r, "ProductName", "porductname", 4)).strip()
                retiredate_s = str(_pick(r, "RetireDate", "retiredate", 5)).strip()
                retired_s    = str(_pick(r, "RetiredDate", "retireddate", 6)).strip()

                # Apenas pendentes (RetiredDate vazio)
                if not _is_blank(retired_s):
                    continue

                # Data de retirada / prazo
                prazo = _parse_br_date(retiredate_s)
                if prazo is None:
                    continue

                # Dentro da janela terça atual -> segunda seguinte
                if not (win_ini <= prazo <= win_fim):
                    continue

                dias_prazo = (prazo - hoje).days  # faltantes até o prazo

                itens.append({
                    "Contrato": orderid,
                    "Cliente": customername,
                    "CodProd": productid,
                    "Produto": productname,
                    "DataRetirada": retiredate_s,
                    "DiasPrazo": dias_prazo
                })

    return jsonify({
        "ok": True,
        "janela": {
            "inicio": win_ini.strftime("%d/%m/%Y"),
            "fim":    win_fim.strftime("%d/%m/%Y")
        },
        "qtd": len(itens),
        "itens": itens
    }), 200

# producao_retiradas_api.py
import os, json, re, logging
from datetime import datetime, timedelta
from flask import jsonify, current_app

# login_required seguro (não quebra se Flask-Login não estiver configurado)
from functools import wraps
try:
    import flask_login  # noqa
    _has_flask_login = True
except Exception:
    _has_flask_login = False

def login_required(view_func):
    if not _has_flask_login:
        return view_func
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        try:
            from flask_login import current_user
            lm = getattr(current_app, "login_manager", None)
            if lm is None:
                return view_func(*args, **kwargs)
            if current_user.is_authenticated:
                return view_func(*args, **kwargs)
            return jsonify({"ok": False, "erro": "auth_required"}), 401
        except Exception:
            return view_func(*args, **kwargs)
    return wrapper

_LOG = logging.getLogger("producao_retiradas_api")

# -------- helpers de data (Fortaleza) ----------
def _tz_fortaleza():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Fortaleza")
    except Exception:
        return None

def _now_fortaleza():
    tz = _tz_fortaleza()
    return datetime.now(tz) if tz else datetime.now()

def _start_of_day(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)

def _window_tuesday_to_next_monday():
    now = _now_fortaleza()
    dow = now.weekday()  # seg=0, ter=1, ... dom=6
    delta = (dow - 1) % 7  # distância até terça
    tuesday = _start_of_day(now - timedelta(days=delta))
    monday  = _start_of_day(tuesday + timedelta(days=6))
    return tuesday, monday

def _fmt_br(d: datetime) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"

_BR_RE = re.compile(r'^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?$')

def _parse_br_datetime(s: str):
    if not s:
        return None
    s = str(s).strip()
    m = _BR_RE.match(s)
    if not m:
        # fallback simples (ISO-like)
        try:
            dt = datetime.fromisoformat(s)
            return dt
        except Exception:
            return None
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    HH = int(m.group(4) or 0)
    MM = int(m.group(5) or 0)
    SS = int(m.group(6) or 0)
    try:
        tz = _tz_fortaleza()
        return datetime(yy, mm, dd, HH, MM, SS, tzinfo=tz) if tz else datetime(yy, mm, dd, HH, MM, SS)
    except Exception:
        return None

# -------- leitura e normalização do banco ----------
def _load_bank():
    try:
        base = os.path.join(current_app.root_path, "dados")
    except Exception:
        base = os.path.join(os.path.dirname(__file__), "dados")
    path = os.path.join(base, "retiradas_web_locacao.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), path
    except Exception as e:
        _LOG.warning("Falha lendo %s: %s", path, e)
        return {}, path

def _canon_row(r):
    """Aceita lista (aaData) ou dict e retorna dict com campos padronizados."""
    if isinstance(r, list):
        # 0=OrderId,1=CustomerName,2=ProductId,4=ProductName,5=RetireDate,6=RetiredDate
        return {
            "OrderId":      r[0] if len(r) > 0 else "",
            "CustomerName": r[1] if len(r) > 1 else "",
            "ProductId":    r[2] if len(r) > 2 else "",
            "ProductName":  r[4] if len(r) > 4 else "",
            "RetireDate":   r[5] if len(r) > 5 else "",
            "RetiredDate":  r[6] if len(r) > 6 else "",
        }
    if isinstance(r, dict):
        low = {str(k).lower(): v for k, v in r.items()}
        def pick(*keys, default=""):
            for k in keys:
                if k in r: return r[k]
                lk = str(k).lower()
                if lk in low: return low[lk]
            return default
        return {
            "OrderId":      pick("OrderId", "orderid", 0),
            "CustomerName": pick("CustomerName", "customername", 1),
            "ProductId":    pick("ProductId", "productid", 2),
            "ProductName":  pick("ProductName", "productname", "porductname", 4),
            "RetireDate":   pick("RetireDate", "retiredate", 5),
            "RetiredDate":  pick("RetiredDate", "retireddate", 6),
        }
    return None

def _iter_all_rows(bank_obj):
    out = []
    if isinstance(bank_obj, dict):
        for _, bucket in bank_obj.items():
            if not isinstance(bucket, dict):
                continue
            rows = bucket.get("aaData") if isinstance(bucket.get("aaData"), list) else \
                   bucket.get("data")   if isinstance(bucket.get("data"), list)   else []
            for r in rows:
                c = _canon_row(r)
                if c: out.append(c)
    return out

# -------- rota principal (tabela pronta) ----------
def register_producao_retiradas_api(app):
    @app.get("/producao/api/retiradas_web_table")
    @login_required
    def producao_api_retiradas_web_table():
        bank, path = _load_bank()
        all_rows = _iter_all_rows(bank)

        # pendentes: RetiredDate vazio
        pend = []
        for r in all_rows:
            retired = str(r.get("RetiredDate") or "").strip()
            if retired in ("", "-", "--", "00/00/0000"):
                pend.append(r)

        tuesday, monday = _window_tuesday_to_next_monday()
        start = _start_of_day(tuesday)
        end   = _start_of_day(monday)

        today = _start_of_day(_now_fortaleza()).date()

        table = []
        for r in pend:
            dt = _parse_br_datetime(r.get("RetireDate"))
            if not dt:
                continue
            d0 = _start_of_day(dt)
            if not (start <= d0 <= end):
                continue
            dias_prazo = (d0.date() - today).days  # positivo = falta X dias; negativo = vencido
            table.append({
                "Contrato":     r.get("OrderId") or "",
                "Cliente":      str(r.get("CustomerName") or "").strip(),
                "CodProd":      r.get("ProductId") or "",
                "Produto":      str(r.get("ProductName") or "").strip(),
                "DataRetirada": r.get("RetireDate") or "",
                "DiasPrazo":    dias_prazo,
            })

        # ordena por data e produto
        table.sort(key=lambda x: (x["DataRetirada"], x["Produto"]))

        return jsonify({
            "ok": True,
            "rows": table,
            "window": {"inicio": _fmt_br(start), "fim": _fmt_br(end)},
            "source_file": path,
            "total_raw": len(all_rows)
        })

    # (opcional) rota para debug: devolve o JSON bruto do arquivo
    @app.get("/producao/api/retiradas_web")
    @login_required
    def producao_api_retiradas_web_raw():
        bank, path = _load_bank()
        return jsonify({"ok": True, "bank": bank, "source_file": path})



register_producao_retiradas_api(app)





if __name__ == '__main__':
    app.run(debug=True)
