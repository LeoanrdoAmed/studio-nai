import requests
import pandas as pd
import json
import os
from pathlib import Path

def coletar_contas_bancarias():
    """
    Coleta contas bancárias, processa os dados e salva em dados/base_02_cb.json.
    Retorna o DataFrame final.
    Mantém exatamente o mesmo tratamento do script original.
    """
    # Inicialização de paths robusta
    script_dir = Path(__file__).resolve().parent
    base_dir   = script_dir.parent
    data_dir   = base_dir / 'dados'
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir)  # Para garantir compatibilidade máxima

    url = "https://services.contaazul.com/contaazul-bff/dashboard/v1/financial-accounts"
    payload = {}

    headers_path = data_dir / 'headers_contaazul.json'
    if not headers_path.exists():
        raise FileNotFoundError(f"Arquivo {headers_path} não encontrado.")
    with open(headers_path, "r", encoding="utf-8") as f:
        headers = json.load(f)

    response = requests.request("GET", url, headers=headers, data=payload)
    if response.status_code != 200:
        print(f"Erro na requisição: {response.status_code}")
        print("Resposta:", response.text)
        return None

    data = response.json()
    base_cb = pd.DataFrame(data["dashboardBankAccounts"])

    # -- Tratamento idêntico ao original
    base_cb['ativo'] = base_cb['bankAccount'].apply(lambda x: x['ativo'])
    base_cb['nmBanco'] = base_cb['bankAccount'].apply(lambda x: x['nmBanco'])
    base_cb['uuid'] = base_cb['bankAccount'].apply(lambda x: x['uuid'])

    # Renomear e filtrar colunas
    base_cb.rename(columns={'uuid': 'financialAccountId'}, inplace=True)
    filtered_df = base_cb[['ativo', 'nmBanco', 'financialAccountId']]

    # Salvar em JSON
    out_path = data_dir / "base_02_cb.json"
    filtered_df.to_json(str(out_path), force_ascii=False, indent=2)
    print(f"✅ Consulta de base CB finalizada com sucesso em {out_path}")

    return filtered_df

if __name__ == '__main__':
    coletar_contas_bancarias()
