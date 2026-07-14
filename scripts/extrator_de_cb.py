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


def sincronizar_nomes_contas(df_contas):
    nomes_por_id = {
        str(row["financialAccountId"]): row["nmBanco"]
        for _, row in df_contas.dropna(subset=["financialAccountId"]).iterrows()
    }

    def sincronizar_arquivo(filename, id_col, name_col):
        path = DATA_DIR / filename
        if not path.exists() or not nomes_por_id:
            return
        try:
            df = pd.read_json(path, convert_dates=False)
        except Exception as exc:
            print(f"Nao foi possivel sincronizar nomes em {path}: {exc}")
            return
        if df.empty or id_col not in df.columns or name_col not in df.columns:
            return

        ids = df[id_col].where(df[id_col].notna(), "").astype(str)
        nomes_atualizados = ids.map(nomes_por_id)
        mask = nomes_atualizados.notna() & (df[name_col].astype(str) != nomes_atualizados.astype(str))
        if not mask.any():
            return

        df.loc[mask, name_col] = nomes_atualizados[mask]
        df.to_json(path, orient="records", force_ascii=False, indent=2)
        print(f"Nomes de contas sincronizados em {path}: {int(mask.sum())} linhas.")

    sincronizar_arquivo("base_fluxo_ca.json", "conta_id", "conta_nome")
    sincronizar_arquivo("fluxo_final.json", "conta_id", "conta_nome")
    sincronizar_arquivo("base_final_04_rc.json", "codigo_bancario", "conta_bancaria")
    sincronizar_arquivo("base_03_rc.json", "financialAccountId", "name")


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
    sincronizar_nomes_contas(df)
    print(f"Consulta de base CB finalizada com sucesso em {out_path}")
    return df


if __name__ == "__main__":
    coletar_contas_bancarias()
