#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from datetime import datetime
from pathlib import Path
from time import sleep

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
FLUXO_URL = "https://services.contaazul.com/finance-pro-reader/v1/monthly-cash-flow"

sys.path.insert(0, str(BASE_DIR))

from scripts.autenticador_ca import autenticar_contaazul


class ContaAzulAuthError(Exception):
    pass


def carregar_credenciais():
    path = DATA_DIR / "credenciais_contaazul.json"
    if not path.exists():
        raise FileNotFoundError(f"Arquivo {path} nao encontrado.")
    return json.loads(path.read_text(encoding="utf-8"))


def carregar_contas(incluir_sem_conta=True, incluir_inativas=True):
    path = DATA_DIR / "base_02_cb.json"
    if not path.exists():
        raise FileNotFoundError(f"Arquivo {path} nao encontrado.")

    df = pd.read_json(path)
    if incluir_inativas:
        contas = df.to_dict(orient="records")
    else:
        contas = df[df["ativo"] == True].to_dict(orient="records")
    if incluir_sem_conta and not any(c.get("financialAccountId") == "NONE" for c in contas):
        contas.append({
            "ativo": True,
            "nmBanco": "Sem conta",
            "financialAccountId": "NONE",
        })
    return contas


def carregar_headers_existentes():
    path = DATA_DIR / "headers_contaazul.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _extrair_cookie(cookie_header, nome):
    for item in (cookie_header or "").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        chave, valor = item.split("=", 1)
        if chave == nome:
            return valor
    return None


def normalizar_headers_fluxo(headers):
    headers = dict(headers or {})
    cookie_header = headers.get("Cookie") or headers.get("cookie") or ""
    token = (
        headers.get("x-authorization")
        or headers.get("X-Authorization")
        or _extrair_cookie(cookie_header, "ca-pro-auth-token-current")
        or _extrair_cookie(cookie_header, "auth-token")
        or _extrair_cookie(cookie_header, "auth-token-pd")
        or _extrair_cookie(cookie_header, "redirect_token")
    )

    headers.update({
        "accept": "application/json",
        "content-type": "application/json",
        "origin": "https://pro.contaazul.com",
        "referer": "https://pro.contaazul.com/",
        "user-agent": headers.get("user-agent") or "Mozilla/5.0",
    })
    if token:
        headers["x-authorization"] = token
    return headers


def _linhas_fluxo(rows):
    for row in rows or []:
        yield row
        yield from _linhas_fluxo(row.get("children") or [])


def _meses_do_ano(ano):
    return [f"{ano}-{mes:02d}" for mes in range(1, 13)]


def _valor_realizado(cell):
    if not cell:
        return None
    return cell.get("accomplished")


def extrair_registros_fluxo(data, ano, conta_id, conta_nome):
    registros = []

    if isinstance(data, dict):
        bloco = data.get("data", {})
        headers = bloco.get("header", [])
        meses = [datetime.fromtimestamp(h / 1000).strftime("%Y-%m") for h in headers]
        rows = bloco.get("rows", [])
    elif isinstance(data, list):
        meses = _meses_do_ano(ano)
        rows = data
    else:
        return registros

    for row in _linhas_fluxo(rows):
        label = (row.get("label") or "Nao Informado").strip()
        cells = row.get("cells") or []
        if len(cells) > len(meses):
            cells = cells[:len(meses)]

        for idx, cell in enumerate(cells):
            if idx >= len(meses):
                break
            valor = _valor_realizado(cell)
            if valor is None:
                continue
            registros.append({
                "mes": meses[idx],
                "subcategoria": label,
                "categoria": "",
                "conta_id": conta_id,
                "conta_nome": conta_nome,
                "status": "Consolidado",
                "valor": valor or 0,
            })
    return registros


def _indexar_valores(registros):
    valores = {}
    for registro in registros:
        chave = (registro["mes"], registro["subcategoria"])
        valores[chave] = valores.get(chave, 0) + (registro.get("valor") or 0)
    return valores


def _registros_por_diferenca(total_idx, sem_conta_idx, conta_id, conta_nome):
    registros = []
    for mes, subcategoria in sorted(set(total_idx) | set(sem_conta_idx)):
        valor = round(total_idx.get((mes, subcategoria), 0) - sem_conta_idx.get((mes, subcategoria), 0), 2)
        if abs(valor) < 0.005:
            continue
        registros.append({
            "mes": mes,
            "subcategoria": subcategoria,
            "categoria": "",
            "conta_id": conta_id,
            "conta_nome": conta_nome,
            "status": "Consolidado",
            "valor": valor,
        })
    return registros


def consultar_fluxo(headers, conta_ids, ano):
    if isinstance(conta_ids, str):
        conta_ids = [conta_ids]
    payload = {
        "view": "ACCOMPLISHED",
        "year": ano,
        "financialAccountIds": conta_ids,
    }
    print(f"Request fluxo Conta Azul: contas={len(conta_ids)} ano={ano}")
    resp = requests.post(
        FLUXO_URL,
        headers=normalizar_headers_fluxo(headers),
        json=payload,
        timeout=60,
    )
    print(f"Status Code: {resp.status_code}")
    if resp.status_code in (401, 403):
        raise ContaAzulAuthError(f"Sessao Conta Azul invalida: {resp.status_code}")
    if resp.status_code != 200:
        raise RuntimeError(f"Erro na API: {resp.status_code} - {resp.text}")
    return resp.json()


def autenticar_e_salvar_headers(email, senha, otp_secret):
    headers = autenticar_contaazul(email, senha, otp_secret)
    (DATA_DIR / "headers_contaazul.json").write_text(
        json.dumps(headers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("Headers gerados e salvos.")
    return headers


def coletar_fluxo():
    print("Iniciando coleta de fluxo da Conta Azul...")
    cred = carregar_credenciais()
    email = cred["email"]
    senha = cred["senha"]
    otp_secret = cred["otp_secret"]
    ano_base = cred.get("ano_base", datetime.today().year)

    headers = carregar_headers_existentes()
    if not headers:
        headers = autenticar_e_salvar_headers(email, senha, otp_secret)

    registros = []
    ano_fim = datetime.today().year

    contas = []
    conta_ids = []
    for conta in carregar_contas():
        conta_id = conta.get("financialAccountId")
        if conta_id and conta_id not in conta_ids:
            conta_ids.append(conta_id)
            contas.append(conta)
    if not conta_ids:
        print("Nenhuma conta encontrada para coleta.")
        return None

    print(f"Coletando fluxo por diferenca de {len(conta_ids)} contas.")
    for ano in range(ano_base, ano_fim + 1):
        try:
            try:
                data_total = consultar_fluxo(headers, conta_ids, ano)
            except ContaAzulAuthError:
                print("Sessao expirada. Refazendo autenticacao...")
                headers = autenticar_e_salvar_headers(email, senha, otp_secret)
                data_total = consultar_fluxo(headers, conta_ids, ano)

            total_registros = extrair_registros_fluxo(data_total, ano, "ALL", "Todas as contas")
            total_idx = _indexar_valores(total_registros)
            if not total_idx:
                print(f"Sem dados no ano {ano}")
                continue

            if len(conta_ids) == 1:
                registros.extend(extrair_registros_fluxo(data_total, ano, conta_ids[0], contas[0].get("nmBanco") or "Sem nome"))
                continue

            for conta in contas:
                conta_id = conta["financialAccountId"]
                conta_nome = conta.get("nmBanco") or "Sem nome"
                ids_sem_conta = [cid for cid in conta_ids if cid != conta_id]

                try:
                    data_sem_conta = consultar_fluxo(headers, ids_sem_conta, ano)
                except ContaAzulAuthError:
                    print("Sessao expirada. Refazendo autenticacao...")
                    headers = autenticar_e_salvar_headers(email, senha, otp_secret)
                    data_sem_conta = consultar_fluxo(headers, ids_sem_conta, ano)

                sem_conta_registros = extrair_registros_fluxo(data_sem_conta, ano, "ALL", "Todas exceto a conta")
                sem_conta_idx = _indexar_valores(sem_conta_registros)
                conta_registros = _registros_por_diferenca(total_idx, sem_conta_idx, conta_id, conta_nome)
                registros.extend(conta_registros)
                print(f"Conta {conta_nome}: {len(conta_registros)} registros no ano {ano}")
                sleep(0.2)

            sleep(0.5)
        except Exception as exc:
            print(f"Erro no fluxo por conta ano {ano}: {exc}")

    if not registros:
        print("Nenhum dado coletado.")
        return None

    df = pd.DataFrame(registros)
    path = DATA_DIR / "base_fluxo_ca.json"
    df.to_json(path, orient="records", force_ascii=False, indent=2)
    print(f"Base salva em {path}")
    return df


def gerar_de_para(data=None):
    if data is None:
        path = DATA_DIR / "base_fluxo_ca.json"
        if not path.exists():
            raise FileNotFoundError(f"Arquivo {path} nao encontrado.")
        data = pd.read_json(path)

    subcats = sorted({
        str(subcat).strip()
        for subcat in data["subcategoria"].dropna()
        if str(subcat).strip()
    })
    dp_path = DATA_DIR / "de_para_categorias_mv.json"
    existente_raw = json.loads(dp_path.read_text(encoding="utf-8")) if dp_path.exists() else []

    pure_groups = {}
    por_subcategoria = {}
    pendentes = {"", "Ajustar", "Sem Grupo"}
    for item in existente_raw:
        sub = str(item.get("subcategoria") or item.get("Subcategoria") or "").strip()
        cat = str(item.get("categoria") or item.get("Categoria") or "").strip()
        ordem = item.get("ordem", item.get("Ordem", 999))

        if cat == "Ajustar":
            cat = "Sem Grupo"

        if not sub:
            if cat and (cat not in pure_groups or ordem < pure_groups[cat]["ordem"]):
                pure_groups[cat] = {"subcategoria": "", "categoria": cat, "ordem": ordem}
            continue

        atual = por_subcategoria.get(sub)
        novo = {"subcategoria": sub, "categoria": cat or "Sem Grupo", "ordem": ordem}
        if atual is None or (atual["categoria"] in pendentes and novo["categoria"] not in pendentes):
            por_subcategoria[sub] = novo

    mapeadas = set(por_subcategoria)

    novos = [
        {"subcategoria": subcat, "categoria": "Sem Grupo", "ordem": 999}
        for subcat in subcats
        if subcat not in mapeadas
    ]

    combinado = list(pure_groups.values()) + list(por_subcategoria.values()) + novos
    dp_path.write_text(
        json.dumps(combinado, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("de_para_categorias_mv.json atualizado.")


if __name__ == "__main__":
    coletar_fluxo()
    gerar_de_para()
