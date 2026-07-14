#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import os
from pathlib import Path

# ─── Inicialização de paths ───
# __file__ está em .../scripts, por isso subimos 2 níveis para chegar em dash_way_group
SCRIPT_DIR   = Path(__file__).resolve().parent        # .../scripts
PROJECT_ROOT = SCRIPT_DIR.parent                      # .../dash_way_group
os.chdir(PROJECT_ROOT)

def unificador():
    # 1) Define DATA_DIR e garante existência
    DATA_DIR = PROJECT_ROOT / 'dados'
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 2) Paths originais agora via pathlib
    base_cc = DATA_DIR / 'base_01_cc.json'
    base_cb = DATA_DIR / 'base_02_cb.json'
    base_rc = DATA_DIR / 'base_03_rc.json'

    # 3) Carrega DataFrames
    df2 = pd.read_json(str(base_cb))
    df3 = pd.read_json(str(base_cc))
    df4 = pd.read_json(str(base_rc))

    # 4) Tabela contas a receber
    tb_rc       = pd.merge(df4, df2, on='financialAccountId', how='left')
    tb_rc_final = pd.merge(tb_rc, df3, on='centroCusto',           how='left')

    # 5) Renomeia colunas conforme original
    tb_rc_final.rename(
        columns={
            "date": "data",
            "description": "descrição",
            "type": "tipo",
            "value": "valor",
            "categoryName": "categoria",
            "financialAccountId": "codigo_bancario",
            "centroCusto": "centro_de_custo_id",
            "nmBanco": "conta_bancaria",
            "name": "centro_de_custo",
            "active": "status_da_conta"
        },
        inplace=True
    )

    # 6) Filtra apenas registros de 'Venda'
    tb_rc_final_01 = tb_rc_final[
        tb_rc_final["descrição"].str.contains(r"\bVenda\b", case=False, na=False)
    ]

    # 7) Salva saída no mesmo local original
    out_path = DATA_DIR / 'base_final_04_rc.json'
    tb_rc_final_01.to_json(
        str(out_path),
        orient='records',
        force_ascii=False,
        indent=2
    )

    print("Consulta de base UNI finalizada com sucesso.")

if __name__ == '__main__':
    unificador()
