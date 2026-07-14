#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import pandas as pd
import requests
from pathlib import Path


def extrator_mv():
    # Detect project directories
    SCRIPT_DIR = Path(__file__).resolve().parent
    BASE_DIR   = SCRIPT_DIR.parent
    DATA_DIR   = BASE_DIR / 'dados'
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    URL = "https://services.contaazul.com/finance-pro-reader/v1/financial-statement-view"

    PATH_CENTROS = DATA_DIR / 'base_01_cc.json'
    PATH_HEADERS = DATA_DIR / 'headers_contaazul.json'

    if not PATH_CENTROS.exists():
        raise FileNotFoundError(f"File {PATH_CENTROS} not found.")

    centros = pd.read_json(PATH_CENTROS)['centroCusto'].dropna().tolist()
    headers = json.loads(PATH_HEADERS.read_text(encoding='utf-8'))

    all_items=[]
    for cc in centros:
        page=1
        size=100
        while True:
            payload={"dateFrom":None,"dateTo":None,"search":None,"quickFilter":"ALL","costCenterIds":[cc]}
            resp=requests.post(f"{URL}?page={page}&page_size={size}",headers=headers,json=payload)
            if resp.status_code!=200:
                print(f"Error {resp.status_code} for {cc}")
                break
            data=resp.json()
            items=data.get('items',[])
            total=data.get('totalItems')
            for it in items:
                it['centroCusto']=cc
            all_items.extend(items)
            pages=(total//size)+(1 if total%size else 0)
            if page>=pages: break
            page+=1

    if all_items:
        out=DATA_DIR/'base_05_mv.json'
        pd.DataFrame(all_items).to_json(out,orient='records',force_ascii=False,indent=2)
        print(f"Saved extrato to {out}")

if __name__=='__main__':
    extrator_mv()
