"""
Descargador Robusto de 30 Días de Barras M30 desde TopstepX API
Maneja el rate limit 429 con reintentos exponenciales y guarda en caché.
"""

import urllib.request
import ssl
import json
import time
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from config import TOPSTEPX_USERNAME, TOPSTEPX_API_KEY, TOPSTEPX_BASE_URL

ctx = ssl._create_unverified_context()

def get_token():
    auth_url = f"{TOPSTEPX_BASE_URL}/api/Auth/loginKey"
    auth_payload = json.dumps({"userName": TOPSTEPX_USERNAME, "apiKey": TOPSTEPX_API_KEY}).encode("utf-8")
    auth_req = urllib.request.Request(auth_url, data=auth_payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(auth_req, context=ctx, timeout=15) as resp:
        return json.loads(resp.read().decode())["token"]

def download_symbol_30d(contract_id: str, symbol_label: str, token: str):
    cache_path = f"cache_m30_30d_{symbol_label}.json"
    if os.path.exists(cache_path):
        print(f"✓ {symbol_label}: Cargado desde {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    hist_url = f"{TOPSTEPX_BASE_URL}/api/History/retrieveBars"
    payload = {
        "contractId": contract_id,
        "live": False,
        "startTime": "2026-10-04T00:00:00Z",
        "endTime": "2026-11-03T23:59:59Z",
        "unit": 2,          # Minutos
        "unitNumber": 30,   # 30m
        "limit": 20000,
        "includePartialBar": False
    }

    hist_req = urllib.request.Request(
        hist_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    )

    print(f"Descargando 30 días de velas M30 para {symbol_label} ({contract_id})...")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(hist_req, context=ctx, timeout=25) as r:
                data = json.loads(r.read().decode())
                bars = data.get("bars", [])
                print(f"✓ {symbol_label}: {len(bars)} velas M30 recibidas de TopstepX API.")
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(bars, f)
                return bars
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_sec = (attempt + 1) * 8
                print(f"  [Rate Limit 429] Esperando {wait_sec}s antes de reintentar {symbol_label}...")
                time.sleep(wait_sec)
            else:
                raise e

if __name__ == "__main__":
    print("Iniciando conexión con TopstepX API...")
    time.sleep(10) # Espera preventiva para limpiar la ventana de rate limit
    tok = get_token()
    print("✓ Token obtenido.")

    # 1. ES (S&P 500)
    time.sleep(2)
    es_bars = download_symbol_30d("CON.F.US.EP.Z26", "ES", tok)

    # 2. NQ (Nasdaq 100)
    time.sleep(4)
    nq_bars = download_symbol_30d("CON.F.US.ENQ.Z26", "NQ", tok)

    print("Descarga completa de 30 días para ES y NQ.")
