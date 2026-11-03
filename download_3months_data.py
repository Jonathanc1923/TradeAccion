"""
Descarga y Ensamble de 3 Meses Completos de Barras M30 desde TopstepX API
Periodo: 03 de Agosto de 2026 a 03 de Noviembre de 2026
Maneja el Roll Trimestral de Futuros: Contrato U26 (Agosto-Septiembre) + Z26 (Septiembre-Noviembre)
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

def fetch_bars_with_retry(contract_id: str, start_time: str, end_time: str, token: str) -> list:
    hist_url = f"{TOPSTEPX_BASE_URL}/api/History/retrieveBars"
    payload = {
        "contractId": contract_id,
        "live": False,
        "startTime": start_time,
        "endTime": end_time,
        "unit": 2,          # Minutos
        "unitNumber": 30,   # 30 minutos
        "limit": 20000,
        "includePartialBar": False
    }

    hist_req = urllib.request.Request(
        hist_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    )

    for attempt in range(5):
        try:
            with urllib.request.urlopen(hist_req, context=ctx, timeout=25) as r:
                data = json.loads(r.read().decode())
                bars = data.get("bars", [])
                print(f"  ✓ {contract_id} ({start_time[:10]} -> {end_time[:10]}): {len(bars)} barras recibidas.")
                return bars
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_sec = (attempt + 1) * 8
                print(f"  [Rate Limit 429] Esperando {wait_sec}s...")
                time.sleep(wait_sec)
            else:
                print(f"  Aviso: HTTP {e.code} en {contract_id}: {e}")
                return []
        except Exception as e:
            print(f"  Error en {contract_id}: {e}")
            return []
    return []

def get_3month_continuous_bars(symbol_root: str, u_contract: str, z_contract: str, token: str) -> list:
    cache_path = f"cache_m30_3months_{symbol_root}.json"
    if os.path.exists(cache_path):
        print(f"✓ Cargando {symbol_root} desde caché local ({cache_path})...")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    print(f"\nDescargando 3 meses de historia para {symbol_root}...")
    
    # 1. Contrato Z26 (Septiembre a Noviembre)
    time.sleep(4)
    z_bars = fetch_bars_with_retry(z_contract, "2026-09-10T00:00:00Z", "2026-11-03T23:59:59Z", token)

    # 2. Contrato U26 (Agosto a Septiembre)
    time.sleep(4)
    u_bars = fetch_bars_with_retry(u_contract, "2026-08-03T00:00:00Z", "2026-09-15T00:00:00Z", token)

    combined_map = {}
    for b in u_bars + z_bars:
        t_key = b["t"]
        if t_key not in combined_map:
            combined_map[t_key] = b

    all_bars = list(combined_map.values())
    print(f"✓ Total {symbol_root} ensamblado a 3 meses: {len(all_bars)} velas M30.")

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(all_bars, f)
    return all_bars

if __name__ == "__main__":
    print("Iniciando descarga de 3 meses (03 Agosto a 03 Noviembre, 2026)...")
    time.sleep(12) # Pausa inicial para resetear límite 429
    token = get_token()
    print("✓ Autenticado con éxito.")

    # 1. ES (S&P 500)
    es_bars = get_3month_continuous_bars("ES", "CON.F.US.EP.U26", "CON.F.US.EP.Z26", token)

    # 2. NQ (Nasdaq 100)
    time.sleep(4)
    nq_bars = get_3month_continuous_bars("NQ", "CON.F.US.ENQ.U26", "CON.F.US.ENQ.Z26", token)

    print("\nDescarga y ensamblado completado exitosamente.")
