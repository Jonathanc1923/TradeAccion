"""
Backtest Engine: Prueba de los Últimos 7 Días con Datos Directos de TopstepX API
Descarga barras históricas de 15 minutos directamente de https://api.topstepx.com
para el contrato E-Mini S&P 500 (CON.F.US.EP.Z26).
"""

import sys
import io
import os
import json
import ssl
import urllib.request
from datetime import datetime, timezone, time
import zoneinfo
from typing import List, Dict

# Codificación UTF-8 en Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from config import AVAILABLE_SYMBOLS, TOPSTEPX_API_KEY, TOPSTEPX_BASE_URL, TOPSTEPX_USERNAME
from core.vwap_bands import PrecisionVWAP, Candle15M
from core.strategy import VWAPWickBounceStrategy, TradeSignal
from core.schedule_guardian import ScheduleGuardian

def fetch_bars_from_topstepx() -> List[dict]:
    """Descarga las velas oficiales de 15 minutos directamente desde api.topstepx.com con caché local"""
    cache_file = "cache_bars_topstepx.json"
    if os.path.exists(cache_file):
        print(f"[CACHE] Cargando velas desde {cache_file} (evitando saturar el límite 429 de TopstepX)...")
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    ctx = ssl._create_unverified_context()
    
    print(f"[TOPSTEPX API] Autenticando con usuario: {TOPSTEPX_USERNAME}...")
    auth_url = f"{TOPSTEPX_BASE_URL}/api/Auth/loginKey"
    auth_payload = json.dumps({
        "userName": TOPSTEPX_USERNAME,
        "apiKey": TOPSTEPX_API_KEY
    }).encode("utf-8")
    
    auth_req = urllib.request.Request(auth_url, data=auth_payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(auth_req, context=ctx, timeout=15) as resp:
        auth_data = json.loads(resp.read().decode())
        token = auth_data.get("token")
        if not token:
            raise Exception(f"Fallo de login en TopstepX: {auth_data}")
        print("✓ Autenticación exitosa con TopstepX API (Token JWT generado).")

    import time
    time.sleep(3) # Pausa preventiva para no activar el rate limit

    print("[TOPSTEPX API] Consultando barras históricas M15 de ES (CON.F.US.EP.Z26)...")
    hist_url = f"{TOPSTEPX_BASE_URL}/api/History/retrieveBars"
    hist_payload = json.dumps({
        "contractId": "CON.F.US.EP.Z26",
        "live": False,
        "startTime": "2026-10-27T00:00:00Z",
        "endTime": "2026-11-03T23:59:59Z",
        "unit": 2,          # 2 = Minutos
        "unitNumber": 15,   # 15 minutos
        "limit": 1000,
        "includePartialBar": False
    }).encode("utf-8")

    hist_req = urllib.request.Request(
        hist_url, data=hist_payload, 
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    )

    raw_bars = []
    # Reintento con backoff si salta 429
    for attempt in range(4):
        try:
            with urllib.request.urlopen(hist_req, context=ctx, timeout=20) as h_resp:
                data = json.loads(h_resp.read().decode())
                raw_bars = data.get("bars", [])
                print(f"✓ {len(raw_bars)} velas M15 recibidas DIRECTAMENTE desde los servidores de TopstepX.")
                break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_s = (attempt + 1) * 6
                print(f"[RATE LIMIT 429] Esperando {wait_s} segundos para reintentar con TopstepX...")
                time.sleep(wait_s)
            else:
                raise e

    # Convertir y ordenar cronológicamente ascendente (TopstepX las envía en orden inverso)
    formatted_bars = []
    for b in raw_bars:
        dt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
        formatted_bars.append({
            "timestamp": int(dt.timestamp()),
            "datetime_iso": b["t"],
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": float(b["v"]) if b.get("v") else 1000.0
        })

    formatted_bars.sort(key=lambda x: x["timestamp"])
    
    # Guardar en caché local
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(formatted_bars, f)
        print(f"✓ {len(formatted_bars)} velas guardadas en {cache_file}.")

    return formatted_bars

def run_backtest():
    bars = fetch_bars_from_topstepx()
    if not bars:
        print("Error: No se obtuvieron barras de TopstepX.")
        return

    meta = AVAILABLE_SYMBOLS["ES"] # Mini ES: $12.50 / tick | tick_size = 0.25
    guardian = ScheduleGuardian()
    strategy = VWAPWickBounceStrategy(meta)
    vwap_calc = PrecisionVWAP(std_multiplier=1.0)
    tz_ct = zoneinfo.ZoneInfo("America/Chicago")

    daily_results: Dict[str, dict] = {}
    session_candles: List[Candle15M] = []
    current_position: dict = None
    last_session_date = None

    for bar in bars:
        dt_utc = datetime.fromtimestamp(bar["timestamp"], timezone.utc)
        dt_ct = dt_utc.astimezone(tz_ct)
        day_str = dt_ct.strftime("%Y-%m-%d")

        if day_str not in daily_results:
            daily_results[day_str] = {
                "date": day_str,
                "trades": [],
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0
            }

        # Reseteo diario de VWAP en apertura oficial CME (17:00 CT)
        if last_session_date != day_str and dt_ct.hour >= 17:
            vwap_calc.reset()
            session_candles.clear()
            last_session_date = day_str

        # Cálculo de VWAP y bandas
        vwap, up, low = vwap_calc.update_candle(bar["high"], bar["low"], bar["close"], bar["volume"])

        candle = Candle15M(
            timestamp=bar["timestamp"],
            datetime_str=dt_ct.strftime("%Y-%m-%d %H:%M CT"),
            open=bar["open"],
            high=bar["high"],
            low=bar["low"],
            close=bar["close"],
            volume=bar["volume"],
            vwap=vwap,
            upper_band_1=up,
            lower_band_1=low,
            is_closed=True
        )
        session_candles.append(candle)

        # 1. EVALUAR POSICIÓN VIVA (SL / TP / Cierre 15:05 CT)
        if current_position is not None:
            pos = current_position
            if guardian.should_flatten_immediately(dt_ct):
                exit_price = candle.close
                ticks = (exit_price - pos["entry"]) / meta.tick_size
                if pos["action"] == "SELL": ticks = -ticks
                pnl = ticks * meta.tick_value
                pos["exit"] = exit_price
                pos["pnl"] = pnl
                pos["result"] = "FLATTEN_15:05"
                pos["exit_time"] = dt_ct.strftime("%H:%M CT")
                daily_results[pos["day"]]["trades"].append(pos)
                daily_results[pos["day"]]["total_pnl"] += pnl
                if pnl > 0: daily_results[pos["day"]]["wins"] += 1
                else: daily_results[pos["day"]]["losses"] += 1
                current_position = None
            else:
                hit_tp = False
                hit_sl = False
                if pos["action"] == "BUY":
                    if candle.high >= pos["tp"]: hit_tp = True
                    elif candle.low <= pos["sl"]: hit_sl = True
                else:
                    if candle.low <= pos["tp"]: hit_tp = True
                    elif candle.high >= pos["sl"]: hit_sl = True

                if hit_tp:
                    pnl = pos["reward_ticks"] * meta.tick_value
                    pos["exit"] = pos["tp"]
                    pos["pnl"] = pnl
                    pos["result"] = "TP_HIT"
                    pos["exit_time"] = dt_ct.strftime("%H:%M CT")
                    daily_results[pos["day"]]["trades"].append(pos)
                    daily_results[pos["day"]]["total_pnl"] += pnl
                    daily_results[pos["day"]]["wins"] += 1
                    current_position = None
                elif hit_sl:
                    pnl = -pos["risk_ticks"] * meta.tick_value
                    pos["exit"] = pos["sl"]
                    pos["pnl"] = pnl
                    pos["result"] = "SL_HIT"
                    pos["exit_time"] = dt_ct.strftime("%H:%M CT")
                    daily_results[pos["day"]]["trades"].append(pos)
                    daily_results[pos["day"]]["total_pnl"] += pnl
                    daily_results[pos["day"]]["losses"] += 1
                    current_position = None

        # 2. EVALUAR NUEVA ENTRADA
        if current_position is None and len(session_candles) >= 2:
            # Control de riesgo Topstep: Lockout si el día alcanza -$1,000 (cuenta 150k)
            if daily_results[day_str]["total_pnl"] <= -1000.0:
                continue

            is_allowed, reason, _ = guardian.evaluate_market_status(dt_ct)
            if is_allowed:
                signal = strategy.evaluate_candles(session_candles)
                if signal:
                    current_position = {
                        "day": day_str,
                        "time": dt_ct.strftime("%H:%M CT"),
                        "action": signal.action,
                        "entry": signal.entry_price,
                        "sl": signal.sl_price,
                        "tp": signal.tp_price,
                        "risk_ticks": signal.risk_ticks,
                        "reward_ticks": signal.reward_ticks,
                        "ratio": signal.risk_reward_ratio,
                        "vwap": signal.vwap_at_entry
                    }

    # INFORME
    print("\n" + "="*85)
    print(" INFORME OFICIAL DE RESULTADOS DÍA POR DÍA - DATOS DIRECTOS DE TOPSTEPX API")
    print(f" Contrato: CON.F.US.EP.Z26 (ES) | Cuenta: PRAC-V2-460183-95785678 ($150k)")
    print(f" Usuario Autenticado: {TOPSTEPX_USERNAME}")
    print("="*85)

    total_pnl = 0.0
    total_trades = 0
    total_w = 0
    total_l = 0

    for day, res in sorted(daily_results.items()):
        trades = res["trades"]
        n = len(trades)
        total_trades += n
        total_w += res["wins"]
        total_l += res["losses"]
        total_pnl += res["total_pnl"]

        print(f"\n📅 FECHA: {day} | TRADES: {n} | PnL DEL DÍA: ${res['total_pnl']:+,.2f}")
        print("-" * 85)
        if n == 0:
            print("   (Sin operaciones: Sin test de VWAP con mecha o periodo de bloqueo/mantenimiento)")
        else:
            for idx, t in enumerate(trades, 1):
                icon = "🟢 GANADORA" if t["pnl"] > 0 else "🔴 PERDEDORA"
                print(f"   [{idx}] {t['time']} | {t['action']} @ {t['entry']:.2f} | VWAP: {t['vwap']:.2f} "
                      f"| SL: {t['sl']:.2f} | TP: {t['tp']:.2f} | R:R 1:{t['ratio']} "
                      f"-> Salida: {t['exit']:.2f} ({t['result']}) | PnL: ${t['pnl']:+,.2f} {icon}")

    print("\n" + "="*85)
    print(" RESUMEN TOTAL DE LA PRUEBA (TOPSTEPX API HISTORICAL DATA)")
    print("="*85)
    win_rate = (total_w / total_trades * 100) if total_trades > 0 else 0.0
    print(f" Operaciones Totales: {total_trades}")
    print(f" Ganadoras: {total_w} | Perdedoras: {total_l}")
    print(f" Tasa de Acierto (Win Rate): {win_rate:.1f}%")
    print(f" Beneficio Neto Acumulado (1 Contrato ES): ${total_pnl:+,.2f} USD")
    if total_trades > 0:
        print(f" Promedio de Operaciones por Día: {total_trades / len(daily_results):.1f}")
    print("="*85)

if __name__ == "__main__":
    run_backtest()
