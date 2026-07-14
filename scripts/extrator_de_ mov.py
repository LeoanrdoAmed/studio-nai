import requests
import json
import pandas as pd

import os
from pathlib import Path

# ─── Inicialização de paths ───
# Garante que o cwd (diretório de trabalho) seja sempre a raiz do projeto,
# i.e. a pasta dash_way_group onde estão app.py, /scripts, /dados, /uploads, etc.
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

url = "https://services.contaazul.com/finance-pro-reader/v1/financial-statement-view"

# Inicializando uma lista para armazenar todos os dados
all_items = []

# Lista de IDs de centros de custo (substitua pelo seu JSON neste formato)
base_centros_de_custos = r"dados/base_01_cc.json"
centro_custo_json = pd.read_json(base_centros_de_custos)


# Extraindo os IDs dos centros de custo
cost_center_ids = centro_custo_json["centroCusto"]

# Iterando sobre cada ID de centro de custo
for cost_center_id in cost_center_ids:
    page = 1  # Começando pela primeira página
    page_size = 100  # Número de itens por página

    while True:
        # Configurando o payload com o centro de custo
        payload = json.dumps({
            "dateFrom": None,
            "dateTo": None,
            "search": None,
            "quickFilter": "ALL",
            "costCenterIds": [cost_center_id],  # Adicionando o ID do centro de custo
        })

        with open("headers_contaazul.json", "r") as f:
            headers = json.load(f)


        # Fazendo a requisição com paginação
        response = requests.post(f"{url}?page={page}&page_size={page_size}", headers=headers, data=payload)

        # Verifique se a requisição foi bem-sucedida
        if response.status_code == 200:
            data = response.json()

            if 'totalItems' in data and 'items' in data:
                total_items = data['totalItems']
                items = data['items']

                # Adicionando uma nova coluna "centroCusto" a cada item
                for item in items:
                    item['centroCusto'] = cost_center_id  # Associa o ID do centro de custo ao item

                all_items.extend(items)  # Adicionando os itens à lista

                # Calcular o número total de páginas
                total_pages = (total_items // page_size) + (1 if total_items % page_size > 0 else 0)

                # Se já pegou todos os itens, pare a iteração
                if page >= total_pages:
                    break
                page += 1  # Incrementa a página para a próxima requisição
            else:
                print(f"A chave 'items' ou 'totalItems' não foi encontrada na resposta para o centro de custo {cost_center_id}.")
                break
        else:
            print(f"Erro na requisição para o centro de custo {cost_center_id}: {response.status_code}")
            break

# Criando o DataFrame com todos os dados coletados
df = pd.DataFrame(all_items)

# Salvando o DataFrame em um arquivo Excel
df.to_json(r"dados/base_05_mv.json", orient="records")
#df.to_excel("base_extrato.xlsx")