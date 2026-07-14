import requests
import pandas as pd
import json
import os
from pathlib import Path

def coletar_centros_de_custo():
    """
    Coleta centros de custo, processa os dados e salva em dados/base_01_cc.json.
    Retorna o DataFrame final.
    Mantém exatamente o mesmo tratamento do script original.
    """
    # Inicialização de paths robusta
    script_dir = Path(__file__).resolve().parent
    base_dir   = script_dir.parent
    data_dir   = base_dir / 'dados'
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir)  # Compatibilidade máxima para salvar arquivos

    url = "https://services.contaazul.com/finance-pro/v1/cost-centers?search=&page_size=10&page=1&quick_filter=ACTIVE"

    headers_path = data_dir / 'headers_contaazul.json'
    if not headers_path.exists():
        raise FileNotFoundError(f"Arquivo {headers_path} não encontrado.")
    with open(headers_path, "r", encoding="utf-8") as f:
        headers = json.load(f)

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        base_cc = pd.DataFrame(data["items"])

        # Cria uma nova linha com valores "NONE"
        new_row = pd.DataFrame([{
            "id": "NONE",
            "version": "NONE",
            "code": "NONE",
            "name": "NONE",
            "parent": "NONE",
            "active": "NONE"
        }])

        # Adiciona a nova linha ao DataFrame
        base_cc = pd.concat([base_cc, new_row], ignore_index=True)
        base_cc.rename(columns={'id': 'centroCusto'}, inplace=True)

        # Salva o DataFrame atualizado em JSON
        out_path = data_dir / "base_01_cc.json"
        base_cc.to_json(str(out_path), force_ascii=False, indent=2)
        print(f"✅ Consulta de base CC finalizada com sucesso em {out_path}")
        return base_cc
    else:
        print(f"Erro na requisição: {response.status_code}")
        print("Resposta:", response.text)
        return None

if __name__ == '__main__':
    coletar_centros_de_custo()
