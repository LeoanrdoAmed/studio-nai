import json
import sys
from pathlib import Path

import pandas as pd
import requests


def encontrar_raiz_projeto(start):
    for path in [start, *start.parents]:
        if (path / "app.py").exists():
            return path
    return start.parent


SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = encontrar_raiz_projeto(SCRIPT_DIR)
DATA_DIR = BASE_DIR / "dados"
DATA_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BASE_DIR))

from scripts.coleta_fluxo_ca import normalizar_headers_fluxo


def coletar_contas_bancarias():
    """
    Coleta contas financeiras da API atual do Conta Azul Pro.
    """
    headers_path = DATA_DIR / "headers_contaazul.json"
    if not headers_path.exists():
        raise FileNotFoundError(f"Arquivo {headers_path} nao encontrado.")

    headers = normalizar_headers_fluxo(json.loads(headers_path.read_text(encoding="utf-8")))
    url = "https://services.contaazul.com/finance-pro/v1/financial-accounts"
    params = {"search": "", "page_size": 100, "page": 1}
    response = requests.get(url, headers=headers, params=params, timeout=60)
    if response.status_code != 200:
        raise RuntimeError(f"Erro na requisicao: {response.status_code} - {response.text}")

    data = response.json()
    items = data.get("items", [])
    rows = [
        {
            "ativo": bool(item.get("active")),
            "nmBanco": item.get("name") or "Sem nome",
            "financialAccountId": item.get("id"),
        }
        for item in items
        if item.get("id")
    ]

    df = pd.DataFrame(rows, columns=["ativo", "nmBanco", "financialAccountId"])
    out_path = DATA_DIR / "base_02_cb.json"
    df.to_json(str(out_path), orient="records", force_ascii=False, indent=2)
    print(f"Consulta de base CB finalizada com sucesso em {out_path}")
    return df


if __name__ == "__main__":
    coletar_contas_bancarias()
