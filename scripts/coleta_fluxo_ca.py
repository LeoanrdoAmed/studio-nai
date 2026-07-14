#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import pandas as pd
import requests
from datetime import datetime
from time import sleep
from pathlib import Path
import sys

# ─── Inicialização de paths ───
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR   = SCRIPT_DIR.parent
DATA_DIR   = BASE_DIR / 'dados'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Garante que scripts/ esteja no sys.path
sys.path.insert(0, str(BASE_DIR))

from scripts.autenticador_ca import autenticar_contaazul

def carregar_credenciais():
    """
    Lê credenciais de dados/credenciais_contaazul.json
    """
    path = DATA_DIR / 'credenciais_contaazul.json'
    if not path.exists():
        raise FileNotFoundError(f'❌ Arquivo {path} não encontrado.')
    return json.loads(path.read_text(encoding='utf-8'))

def carregar_contas():
    """
    Lê dados/base_02_cb.json e retorna lista de contas ativas
    """
    path = DATA_DIR / 'base_02_cb.json'
    if not path.exists():
        raise FileNotFoundError(f'❌ Arquivo {path} não encontrado.')
    df = pd.read_json(path)
    return df[df['ativo'] == True].to_dict(orient='records')

def carregar_headers_existentes():
    """
    Retorna headers de dados/headers_contaazul.json ou None
    """
    path = DATA_DIR / 'headers_contaazul.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return None

def consultar_fluxo(headers, conta_id, ano):
    """
    Chama a API de fluxo mensal ContaAzul
    """
    url = "https://services.contaazul.com/contaazul-bff/reports/v1/monthly-cash-flow"
    payload = {
        "year": ano,
        "view": "ACCOMPLISHED",
        "financialAccountIds": [conta_id]
    }
    print(f"➡️ Fazendo request para conta {conta_id} ano {ano}...")
    resp = requests.post(url, headers=headers, json=payload)
    print(f'➡️ Status Code: {resp.status_code}')
    if resp.status_code != 200:
        raise Exception(f"❌ Erro na API: {resp.status_code} - {resp.text}")
    return resp.json()

def coletar_fluxo():
    """
    Autentica (se necessário), coleta fluxo mensal para cada conta e salva
    em dados/base_fluxo_ca.json. Retorna o DataFrame resultante.
    """
    print('🟦 Iniciando coleta de fluxo da Conta Azul...')
    # 1) Carrega credenciais
    cred = carregar_credenciais()
    email      = cred['email']
    senha      = cred['senha']
    otp_secret = cred['otp_secret']
    ano_base   = cred.get('ano_base', datetime.today().year)

    # 2) Carrega ou gera headers
    headers = carregar_headers_existentes()
    if not headers:
        headers = autenticar_contaazul(email, senha, otp_secret)
        (DATA_DIR / 'headers_contaazul.json').write_text(
            json.dumps(headers, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        print('✅ Headers gerados e salvos.')

    registros = []
    ano_fim   = datetime.today().year

    # 3) Loop de contas e anos
    for conta in carregar_contas():
        cid, nome = conta['financialAccountId'], conta['nmBanco']
        print(f'🔍 Coletando dados da conta: {nome}')
        for ano in range(ano_base, ano_fim + 1):
            print(f'   ➡️ Ano {ano}')
            try:
                data = consultar_fluxo(headers, cid, ano)
                bloco = data.get('data', {})
                hdrs  = bloco.get('header', [])
                if not hdrs:
                    print(f'⚠️ Sem dados para {nome} no ano {ano}')
                    continue

                meses = [datetime.fromtimestamp(h / 1000).strftime('%Y-%m') for h in hdrs]
                rows  = bloco.get('rows', [])
                for row in rows:
                    label = row.get('label', 'Não Informado').strip()
                    for idx, cell in enumerate(row.get('cells', [])):
                        if idx < len(meses) and cell and cell.get('accomplished') is not None:
                            registros.append({
                                'mes': meses[idx],
                                'subcategoria': label,
                                'categoria': '',
                                'conta_id': cid,
                                'conta_nome': nome,
                                'status': 'Consolidado',
                                'valor': cell['accomplished'] or 0
                            })
                sleep(0.5)
            except Exception as e:
                print(f'❌ Erro na conta {nome} ano {ano}: {e}')

    if not registros:
        print('⚠️ Nenhum dado coletado.')
        return None

    # 4) Grava JSON e retorna DataFrame
    df   = pd.DataFrame(registros)
    path = DATA_DIR / 'base_fluxo_ca.json'
    df.to_json(path, orient='records', force_ascii=False, indent=2)
    print(f'✅ Base salva em {path}')
    return df

def gerar_de_para(data=None):
    """
    Atualiza dados/de_para_categorias_mv.json com novas subcategorias.
    """
    if data is None:
        path = DATA_DIR / 'base_fluxo_ca.json'
        if not path.exists():
            raise FileNotFoundError(f'❌ Arquivo {path} não encontrado.')
        data = pd.read_json(path)

    subcats = sorted(set(data['subcategoria'].dropna()))
    dp_path = DATA_DIR / 'de_para_categorias_mv.json'
    existente = json.loads(dp_path.read_text(encoding='utf-8')) if dp_path.exists() else []
    mapeadas  = {item['subcategoria'] for item in existente if 'subcategoria' in item}

    novos = [{'subcategoria': s, 'categoria': 'Ajustar', 'ordem': 999}
             for s in subcats if s not in mapeadas]

    combinado = existente + novos
    dp_path.write_text(
        json.dumps(combinado, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print('📝 de_para_categorias_mv.json atualizado.')

if __name__ == '__main__':
    coletar_fluxo()
    gerar_de_para()