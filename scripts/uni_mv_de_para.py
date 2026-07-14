#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import os
import json
import unicodedata
from pathlib import Path

# ─── Inicialização de paths ───
# Garante que o cwd seja sempre a raiz do projeto (dash_way_group)
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

def strip_accents(text: str) -> str:
    """
    Remove acentuação de uma string, normalizando para caracteres ASCII.
    """
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))

def gerar_fluxo_preprocessado():
    # Paths dos arquivos
    base_path   = Path('dados') / 'base_05_mv.json'
    depara_path = Path('dados') / 'de_para_categorias_mv.json'
    contas_path = Path('dados') / 'base_02_cb.json'
    campos_path = Path('dados') / 'campos_personalizados.json'
    output_path = Path('dados') / 'fluxo_final.json'

    # Verificação de existência
    for p in (base_path, depara_path, contas_path):
        if not p.exists():
            print(f"❌ Arquivo não encontrado: {p}")
            return

    # Carregamento dos dados
    df_mov   = pd.read_json(str(base_path))
    df_cb    = pd.read_json(str(contas_path))
    with open(depara_path, 'r', encoding='utf-8') as f:
        depara_json = json.load(f)
    df_depara = pd.DataFrame(depara_json)

    # Normalização de subcategorias (remover espaços, lower, sem acentos)
    df_mov['Subcategoria'] = df_mov['categoryName'].astype(str)
    df_mov['sub_norm'] = (
        df_mov['Subcategoria']
        .str.strip()
        .str.lower()
        .apply(strip_accents)
    )
    df_depara['Subcategoria'] = df_depara['Subcategoria'].astype(str)
    df_depara['sub_norm'] = (
        df_depara['Subcategoria']
        .str.strip()
        .str.lower()
        .apply(strip_accents)
    )

    # Evitar duplicação de mapeamentos
    df_depara = df_depara.drop_duplicates(subset=['sub_norm'])

    # Conversão de datas e valores
    df_mov['date']  = pd.to_datetime(df_mov['date'], errors='coerce')
    df_mov['mes']   = df_mov['date'].dt.to_period('M').astype(str)
    df_mov['valor'] = pd.to_numeric(df_mov['value'], errors='coerce').fillna(0)

    # Merge de contas bancárias
    df_cb = df_cb.rename(columns={
        'financialAccountId': 'conta_id',
        'nmBanco': 'conta_nome'
    })
    df_mov['conta_id'] = df_mov['financialAccountId']
    df_mov = df_mov.merge(
        df_cb[['conta_id', 'conta_nome']],
        on='conta_id', how='left'
    )

    # Merge com categorias via mapeamento
    df_join = df_mov.merge(
        df_depara[['sub_norm', 'Categoria']],
        on='sub_norm', how='inner'
    )

    # Diagnóstico de subcategorias não mapeadas
    unmatched = set(df_mov['sub_norm'].unique()) - set(df_join['sub_norm'].unique())
    if unmatched:
        print(f"⚠️ Subcategorias sem correspondência no de-para: {unmatched}")

    # Agrupamento e soma de valores
    df_fluxo = df_join.groupby(
        ['mes', 'Categoria', 'Subcategoria', 'conta_id', 'conta_nome', 'status'],
        as_index=False
    )['valor'].sum()
    df_fluxo.rename(
        columns={'Categoria': 'categoria', 'Subcategoria': 'subcategoria'},
        inplace=True
    )

    # Cálculos de campos personalizados (se existir o arquivo)
    if campos_path.exists():
        with open(campos_path, 'r', encoding='utf-8') as f:
            campos = json.load(f)
        for campo in campos:
            nome     = campo['nome']
            operacao = campo.get('operacao', '')
            grupos   = campo.get('grupos', [])

            # Executar por mês, conta e status
            for (mes, conta_id, conta_nome, status), grp in df_fluxo.groupby(
                ['mes', 'conta_id', 'conta_nome', 'status']
            ):
                valores = {g: grp.loc[grp['categoria'] == g, 'valor'].sum() for g in grupos}
                formula = operacao
                for k, v in valores.items():
                    formula = formula.replace(k, str(v))
                try:
                    resultado = eval(formula)
                    df_fluxo.loc[len(df_fluxo)] = [
                        mes, nome, nome, conta_id, conta_nome, status, resultado
                    ]
                except Exception as e:
                    print(f"Erro ao calcular campo personalizado '{nome}': {e}")

    # Exportação para JSON de saída
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(
            df_fluxo.to_dict(orient='records'),
            f, ensure_ascii=False, indent=2
        )
    print(f"✅ Arquivo gerado: {output_path} com {len(df_fluxo)} registros.")
    return df_fluxo

if __name__ == '__main__':
    gerar_fluxo_preprocessado()
