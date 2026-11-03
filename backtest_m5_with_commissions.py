"""
Backtest Engine: Estrategia en Velas de 5 Minutos (M5) con Deducción de Comisiones
Utiliza 1,629 velas reales de 5m descargadas directamente desde TopstepX API (CON.F.US.EP.Z26).
Comisión aplicada: $4.00 USD por vuelta completa (Round-Turn) en 1 contrato ES.
"""

import sys
import io
import os
import json
from datetime import datetime, timezone
import zoneinfo
from typing import List, Dict

# Codificación UTF-8 en Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from config import AVAILABLE_SYMBOLS
from core.vwap_bands import PrecisionVWAP, Candle15M # Misma estructura de vela
from core.strategy import VWAPWickBounceStrategy
from core.schedule_guardian import ScheduleGuardian

COMMISSION_PER_ROUND_TURN = 4.00 # $4.00 USD por contrato ES en Topstep

def run_backtest_m5():
    cache_file = "cache_bars_topstepx_m5.json"
    if not os.path.exists(cache_file):
        print(f"Error: {cache_file} no encontrado.")
        return

    with open(cache_file, "r", encoding="utf-8") as f:
        raw_bars = json.load(f)

    print(f"[DATA] Procesando {len(raw_bars)} velas de 5 minutos descargadas de TopstepX API...")

    # Formatear y ordenar cronológicamente ascendente
    bars = []
    for b in raw_bars:
        dt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
        bars.append({
            "timestamp": int(dt.timestamp()),
            "datetime_iso": b["t"],
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": float(b["v"]) if b.get("v") else 500.0
        })
    bars.sort(key=lambda x: x["timestamp"])

    meta = AVAILABLE_SYMBOLS["ES"] # Mini ES: $12.50 / tick, tick_size = 0.25
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
                "gross_pnl": 0.0,
                "commissions": 0.0,
                "net_pnl": 0.0
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

        # 1. EVALUAR POSICIÓN VIVA
        if current_position is not None:
            pos = current_position
            # Cierre forzoso Topstep a las 15:05 CT
            if guardian.should_flatten_immediately(dt_ct):
                exit_price = candle.close
                ticks = (exit_price - pos["entry"]) / meta.tick_size
                if pos["action"] == "SELL": ticks = -ticks
                gross_pnl = ticks * meta.tick_value
                net_pnl = gross_pnl - COMMISSION_PER_ROUND_TURN
                
                pos["exit"] = exit_price
                pos["gross_pnl"] = gross_pnl
                pos["commission"] = COMMISSION_PER_ROUND_TURN
                pos["net_pnl"] = net_pnl
                pos["result"] = "FLATTEN_15:05"
                pos["exit_time"] = dt_ct.strftime("%H:%M CT")

                daily_results[pos["day"]]["trades"].append(pos)
                daily_results[pos["day"]]["gross_pnl"] += gross_pnl
                daily_results[pos["day"]]["commissions"] += COMMISSION_PER_ROUND_TURN
                daily_results[pos["day"]]["net_pnl"] += net_pnl
                if net_pnl > 0: daily_results[pos["day"]]["wins"] += 1
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
                    gross_pnl = pos["reward_ticks"] * meta.tick_value
                    net_pnl = gross_pnl - COMMISSION_PER_ROUND_TURN
                    pos["exit"] = pos["tp"]
                    pos["gross_pnl"] = gross_pnl
                    pos["commission"] = COMMISSION_PER_ROUND_TURN
                    pos["net_pnl"] = net_pnl
                    pos["result"] = "TP_HIT"
                    pos["exit_time"] = dt_ct.strftime("%H:%M CT")

                    daily_results[pos["day"]]["trades"].append(pos)
                    daily_results[pos["day"]]["gross_pnl"] += gross_pnl
                    daily_results[pos["day"]]["commissions"] += COMMISSION_PER_ROUND_TURN
                    daily_results[pos["day"]]["net_pnl"] += net_pnl
                    daily_results[pos["day"]]["wins"] += 1
                    current_position = None
                elif hit_sl:
                    gross_pnl = -pos["risk_ticks"] * meta.tick_value
                    net_pnl = gross_pnl - COMMISSION_PER_ROUND_TURN
                    pos["exit"] = pos["sl"]
                    pos["gross_pnl"] = gross_pnl
                    pos["commission"] = COMMISSION_PER_ROUND_TURN
                    pos["net_pnl"] = net_pnl
                    pos["result"] = "SL_HIT"
                    pos["exit_time"] = dt_ct.strftime("%H:%M CT")

                    daily_results[pos["day"]]["trades"].append(pos)
                    daily_results[pos["day"]]["gross_pnl"] += gross_pnl
                    daily_results[pos["day"]]["commissions"] += COMMISSION_PER_ROUND_TURN
                    daily_results[pos["day"]]["net_pnl"] += net_pnl
                    daily_results[pos["day"]]["losses"] += 1
                    current_position = None

        # 2. EVALUAR NUEVA ENTRADA EN M5
        if current_position is None and len(session_candles) >= 2:
            # Control de riesgo preventivo DLL ($1,000 en cuenta 150k)
            if daily_results[day_str]["net_pnl"] <= -1000.0:
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
    print(" INFORME OFICIAL: ESTRATEGIA EN VELAS DE 5 MINUTOS (M5) CON COMISIONES")
    print(f" Contrato: CON.F.US.EP.Z26 (ES) | Comisión: ${COMMISSION_PER_ROUND_TURN:.2f} USD / Round-Turn")
    print(f" Fuente: 1,629 Velas M5 descargadas de TopstepX API | Cuenta: PRAC-V2-460183-95785678")
    print("="*85)

    tot_trades = 0
    tot_wins = 0
    tot_losses = 0
    tot_gross = 0.0
    tot_comm = 0.0
    tot_net = 0.0

    for day, res in sorted(daily_results.items()):
        trades = res["trades"]
        n = len(trades)
        tot_trades += n
        tot_wins += res["wins"]
        tot_losses += res["losses"]
        tot_gross += res["gross_pnl"]
        tot_comm += res["commissions"]
        tot_net += res["net_pnl"]

        print(f"\n📅 FECHA: {day} | TRADES: {n:2d} | BRUTO: ${res['gross_pnl']:+8.2f} | COMISIÓN: -${res['commissions']:6.2f} | NETO: ${res['net_pnl']:+8.2f}")
        print("-" * 85)
        if n == 0:
            print("   (Sin operaciones: Sin test de VWAP con mecha o periodo de bloqueo/mantenimiento)")
        else:
            for idx, t in enumerate(trades[:6], 1): # Mostrar las primeras 6 para legibilidad
                icon = "🟢" if t["net_pnl"] > 0 else "🔴"
                print(f"   [{idx}] {t['time']} | {t['action']} @ {t['entry']:.2f} | SL: {t['sl']:.2f} | TP: {t['tp']:.2f} "
                      f"-> Salida: {t['exit']:.2f} ({t['result']}) | Bruto: ${t['gross_pnl']:+7.2f} | Neto: ${t['net_pnl']:+7.2f} {icon}")
            if n > 6:
                print(f"   ... ({n - 6} operaciones adicionales en el día)")

    print("\n" + "="*85)
    print(" RESUMEN TOTAL DE LA PRUEBA EN 5 MINUTOS (M5)")
    print("="*85)
    win_rate = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    print(f" Total Operaciones Ejecutadas (M5): {tot_trades}")
    print(f" Operaciones Ganadoras: {tot_wins} | Perdedoras: {tot_losses}")
    print(f" Tasa de Acierto (Win Rate): {win_rate:.1f}%")
    print(f" Beneficio Bruto (Gross PnL): ${tot_gross:+,.2f} USD")
    print(f" Total Comisiones Pagadas a Topstep: -${tot_comm:,.2f} USD (${COMMISSION_PER_ROUND_TURN} x {tot_trades})")
    print(f" -------------------------------------------------------------------")
    print(f" BENEFICIO NETO FINAL (Net PnL): ${tot_net:+,.2f} USD")
    if tot_trades > 0:
        print(f" Promedio de Operaciones por Día: {tot_trades / len(daily_results):.1f}")
    print("="*85)

if __name__ == "__main__":
    run_backtest_m5()
