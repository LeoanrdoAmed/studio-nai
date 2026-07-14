#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import pandas as pd
import requests
from pathlib import Path

import os
from pathlib import Path

# ─── Inicialização de paths ───
# Garante que o cwd (diretório de trabalho) seja sempre a raiz do projeto,
# i.e. a pasta dash_way_group onde estão app.py, /scripts, /dados, /uploads, etc.
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

def coletar_recebiveis():
    """
    Coleta contas a receber (installments) por centro de custo
    e salva em dados/base_03_rc.json. Retorna o DataFrame final.
    """
    # 1) Define diretório de dados
    script_dir = Path(__file__).resolve().parent
    base_dir   = script_dir.parent
    data_dir   = base_dir / 'dados'
    data_dir.mkdir(parents=True, exist_ok=True)

    # 2) Configura endpoint
    url = "https://services.contaazul.com/finance-pro-reader/v1/installment-view"
    all_items = []

    # 3) Lê lista de centros de custo
    cc_path = data_dir / 'base_01_cc.json'
    if not cc_path.exists():
        raise FileNotFoundError(f"Arquivo {cc_path} não encontrado.")
    centro_custo_json = pd.read_json(cc_path)
    cost_center_ids   = centro_custo_json["centroCusto"].dropna().tolist()

    # 4) Paginação e coleta
    headers_path = data_dir / 'headers_contaazul.json'
    if not headers_path.exists():
        raise FileNotFoundError(f"Arquivo {headers_path} não encontrado.")
    with open(headers_path, 'r', encoding='utf-8') as f:
        base_headers = json.load(f)

    for cost_center_id in cost_center_ids:
        page      = 1
        page_size = 100

        while True:
            print(f"Consultando centro {cost_center_id}, página {page}...")

            payload = {
                "dateFrom": None,
                "dateTo": None,
                "search": None,
                "quickFilter": "ALL",
                "costCenterIds": [cost_center_id],
                "type": "REVENUE",
            }

            resp = requests.post(
                f"{url}?page={page}&page_size={page_size}",
                headers=base_headers,
                json=payload
            )

            if resp.status_code != 200:
                print(f"Erro {resp.status_code} na requisição.")
                break

            data = resp.json()
            if 'totalItems' not in data or 'items' not in data:
                print("Chaves 'items' ou 'totalItems' ausentes na resposta.")
                break

            items      = data['items']
            total      = data['totalItems']
            print(f"Página {page} recebida com {len(items)} itens.")

            for item in items:
                item['centroCusto'] = cost_center_id
            all_items.extend(items)

            total_pages = (total // page_size) + (1 if total % page_size else 0)
            if page >= total_pages:
                print("Centro de custo finalizado.\n")
                break

            page += 1

    # 5) Monta DataFrame e normaliza
    df = pd.DataFrame(all_items)
    df = df.rename(columns={"id": "id_lancamento"})
    df_expanded = pd.json_normalize(df['financialAccount'])
    df_expanded = df_expanded.rename(columns={"id": "financialAccountId2"})

    df_final = pd.concat([df.drop(columns=['financialAccount']), df_expanded], axis=1)
    df_final = df_final.rename(columns={
        "financialAccountId2": "financialAccountId"
    })

    # 6) Salva JSON
    out_path = data_dir / 'base_03_rc.json'
    df_final.to_json(str(out_path), orient='records', force_ascii=False, indent=2)

    print(f"✅ base_03_rc.json gerado em {out_path}")
    return df_final

if __name__ == '__main__':
    coletar_recebiveis()
