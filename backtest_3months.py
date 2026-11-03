"""
Backtest Engine a 3 Meses Completos en Velas de 30 Minutos (M30)
Periodo: 03 de Agosto a 03 de Noviembre, 2026
Datos Reales de TopstepX API (1,924 barras M30 continuas para ES y NQ)
Comisión deducida: $4.00 USD Round-Turn por cada trade
"""

import sys
import io
import os
import json
from datetime import datetime, timezone
import zoneinfo
from typing import Dict, List

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from config import AVAILABLE_SYMBOLS
from core.vwap_bands import PrecisionVWAP, Candle15M
from core.schedule_guardian import ScheduleGuardian

SPECS = {
    "ES": {"file": "cache_m30_3months_ES.json", "tick_size": 0.25, "tick_val": 12.50, "comm": 4.00, "name": "E-mini S&P 500", "max_risk_ticks": 24},
    "NQ": {"file": "cache_m30_3months_NQ.json", "tick_size": 0.25, "tick_val": 5.00, "comm": 4.00, "name": "E-mini Nasdaq 100", "max_risk_ticks": 48}
}

def backtest_3m_symbol(symbol: str, spec: dict) -> dict:
    with open(spec["file"], "r", encoding="utf-8") as f:
        raw_bars = json.load(f)

    bars = []
    for b in raw_bars:
        iso_str = b.get("t") or b.get("datetime_iso")
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        bars.append({
            "timestamp": int(dt.timestamp()),
            "open": float(b.get("o") or b.get("open")),
            "high": float(b.get("h") or b.get("high")),
            "low": float(b.get("l") or b.get("low")),
            "close": float(b.get("c") or b.get("close")),
            "volume": float(b.get("v") or b.get("volume") or 1000.0)
        })
    bars.sort(key=lambda x: x["timestamp"])

    guardian = ScheduleGuardian()
    vwap_calc = PrecisionVWAP(std_multiplier=1.0)
    tz_ct = zoneinfo.ZoneInfo("America/Chicago")

    daily_map = {}
    session_candles = []
    current_position = None
    last_session_date = None
    trades = []

    for bar in bars:
        dt_utc = datetime.fromtimestamp(bar["timestamp"], timezone.utc)
        dt_ct = dt_utc.astimezone(tz_ct)
        day_str = dt_ct.strftime("%Y-%m-%d")

        if day_str not in daily_map:
            daily_map[day_str] = {"gross": 0.0, "comm": 0.0, "net": 0.0, "trades": 0, "wins": 0, "losses": 0}

        # Reseteo diario de VWAP a las 17:00 CT
        if last_session_date != day_str and dt_ct.hour >= 17:
            vwap_calc.reset()
            session_candles.clear()
            last_session_date = day_str

        vwap, up, low = vwap_calc.update_candle(bar["high"], bar["low"], bar["close"], bar["volume"])
        c = Candle15M(bar["timestamp"], dt_ct.strftime("%H:%M CT"), bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"], vwap, up, low, is_closed=True)
        session_candles.append(c)

        # 1. Gestionar posición
        if current_position is not None:
            pos = current_position
            if guardian.should_flatten_immediately(dt_ct):
                exit_p = c.close
                ticks = (exit_p - pos["entry"]) / spec["tick_size"] if pos["action"] == "BUY" else (pos["entry"] - exit_p) / spec["tick_size"]
                gross = ticks * spec["tick_val"]
                net = gross - spec["comm"]
                pos.update({"exit": exit_p, "gross": gross, "net": net, "res": "FLATTEN_15:05", "exit_time": dt_ct.strftime("%H:%M CT")})
                trades.append(pos)
                daily_map[pos["day"]]["gross"] += gross
                daily_map[pos["day"]]["comm"] += spec["comm"]
                daily_map[pos["day"]]["net"] += net
                daily_map[pos["day"]]["trades"] += 1
                if net > 0: daily_map[pos["day"]]["wins"] += 1
                else: daily_map[pos["day"]]["losses"] += 1
                current_position = None
            else:
                hit_tp, hit_sl = False, False
                if pos["action"] == "BUY":
                    if c.high >= pos["tp"]: hit_tp = True
                    elif c.low <= pos["sl"]: hit_sl = True
                else:
                    if c.low <= pos["tp"]: hit_tp = True
                    elif c.high >= pos["sl"]: hit_sl = True

                if hit_tp:
                    gross = pos["reward_ticks"] * spec["tick_val"]
                    net = gross - spec["comm"]
                    pos.update({"exit": pos["tp"], "gross": gross, "net": net, "res": "TP_HIT", "exit_time": dt_ct.strftime("%H:%M CT")})
                    trades.append(pos)
                    daily_map[pos["day"]]["gross"] += gross
                    daily_map[pos["day"]]["comm"] += spec["comm"]
                    daily_map[pos["day"]]["net"] += net
                    daily_map[pos["day"]]["trades"] += 1
                    daily_map[pos["day"]]["wins"] += 1
                    current_position = None
                elif hit_sl:
                    gross = -pos["risk_ticks"] * spec["tick_val"]
                    net = gross - spec["comm"]
                    pos.update({"exit": pos["sl"], "gross": gross, "net": net, "res": "SL_HIT", "exit_time": dt_ct.strftime("%H:%M CT")})
                    trades.append(pos)
                    daily_map[pos["day"]]["gross"] += gross
                    daily_map[pos["day"]]["comm"] += spec["comm"]
                    daily_map[pos["day"]]["net"] += net
                    daily_map[pos["day"]]["trades"] += 1
                    daily_map[pos["day"]]["losses"] += 1
                    current_position = None

        # 2. Nueva entrada
        if current_position is None and len(session_candles) >= 2:
            is_allowed, _, _ = guardian.evaluate_market_status(dt_ct)
            if is_allowed and daily_map[day_str]["net"] > -1000.0:
                c_prev = session_candles[-2]
                c_curr = session_candles[-1]

                # BUY
                if c_prev.close >= c_prev.vwap and c_curr.low <= c_curr.vwap and c_curr.close > c_curr.vwap:
                    entry = round(round(c_curr.close / spec["tick_size"]) * spec["tick_size"], 4)
                    sl = round(round((c_curr.vwap - (5 * spec["tick_size"])) / spec["tick_size"]) * spec["tick_size"], 4)
                    tp_dist = (c_curr.upper_band_1 - c_curr.vwap) * 0.5
                    tp = round(round((entry + tp_dist) / spec["tick_size"]) * spec["tick_size"], 4)
                    risk_t = int(round((entry - sl) / spec["tick_size"]))
                    rew_t = int(round((tp - entry) / spec["tick_size"]))
                    if risk_t > 0 and rew_t > 0 and risk_t <= spec["max_risk_ticks"]:
                        current_position = {"symbol": symbol, "day": day_str, "time": dt_ct.strftime("%H:%M CT"), "action": "BUY", "entry": entry, "sl": sl, "tp": tp, "risk_ticks": risk_t, "reward_ticks": rew_t}

                # SELL
                elif c_prev.close <= c_prev.vwap and c_curr.high >= c_curr.vwap and c_curr.close < c_curr.vwap:
                    entry = round(round(c_curr.close / spec["tick_size"]) * spec["tick_size"], 4)
                    sl = round(round((c_curr.vwap + (5 * spec["tick_size"])) / spec["tick_size"]) * spec["tick_size"], 4)
                    tp_dist = (c_curr.vwap - c_curr.lower_band_1) * 0.5
                    tp = round(round((entry - tp_dist) / spec["tick_size"]) * spec["tick_size"], 4)
                    risk_t = int(round((sl - entry) / spec["tick_size"]))
                    rew_t = int(round((entry - tp) / spec["tick_size"]))
                    if risk_t > 0 and rew_t > 0 and risk_t <= spec["max_risk_ticks"]:
                        current_position = {"symbol": symbol, "day": day_str, "time": dt_ct.strftime("%H:%M CT"), "action": "SELL", "entry": entry, "sl": sl, "tp": tp, "risk_ticks": risk_t, "reward_ticks": rew_t}

    tot_trades = len(trades)
    tot_wins = sum(1 for t in trades if t["net"] > 0)
    tot_losses = sum(1 for t in trades if t["net"] <= 0)
    tot_gross = sum(t["gross"] for t in trades)
    tot_comm = sum(spec["comm"] for t in trades)
    tot_net = sum(t["net"] for t in trades)
    wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0

    return {
        "symbol": symbol, "name": spec["name"], "trades": tot_trades,
        "wins": tot_wins, "losses": tot_losses, "win_rate": wr,
        "gross_pnl": tot_gross, "commissions": tot_comm, "net_pnl": tot_net,
        "daily_map": daily_map, "trade_list": trades
    }

def run_3months_analysis():
    print("\n" + "="*95)
    print(" INFORME OFICIAL A 3 MESES COMPLETOS EN VELAS DE 30 MINUTOS (M30)")
    print(" Periodo: 03 de Agosto a 03 de Noviembre, 2026 | Datos: TopstepX API Oficial (Roll U26 + Z26)")
    print(" Cuenta: PRAC-V2-460183-95785678 ($150,000) | Comisiones Deducidas: $4.00 / trade")
    print("="*95)

    results = {}
    for s in ["ES", "NQ"]:
        results[s] = backtest_3m_symbol(s, SPECS[s])

    print(f"\n| {'Símbolo':<8} | {'Nombre del Activo':<22} | {'Trades':^6} | {'Win Rate':^9} | {'Bruto (Gross)':^13} | {'Comisiones':^11} | {'NETO FINAL':^13} |")
    print("|" + "-"*10 + "|" + "-"*24 + "|" + "-"*8 + "|" + "-"*11 + "|" + "-"*15 + "|" + "-"*13 + "|" + "-"*15 + "|")

    port_trades = 0
    port_wins = 0
    port_comm = 0.0
    port_gross = 0.0
    port_net = 0.0

    for s, res in results.items():
        port_trades += res["trades"]
        port_wins += res["wins"]
        port_comm += res["commissions"]
        port_gross += res["gross_pnl"]
        port_net += res["net_pnl"]
        print(f"| {res['symbol']:<8} | {res['name']:<22} | {res['trades']:^6} | {res['win_rate']:^8.1f}% | ${res['gross_pnl']:^+11.2f} | -${res['commissions']:^9.2f} | ${res['net_pnl']:^+11.2f} |")

    print("|" + "-"*10 + "|" + "-"*24 + "|" + "-"*8 + "|" + "-"*11 + "|" + "-"*15 + "|" + "-"*13 + "|" + "-"*15 + "|")
    overall_wr = (port_wins / port_trades * 100) if port_trades > 0 else 0
    print(f"| {'TOTAL':<8} | {'PORTAFOLIO ES + NQ':<22} | {port_trades:^6} | {overall_wr:^8.1f}% | ${port_gross:^+11.2f} | -${port_comm:^9.2f} | ${port_net:^+11.2f} |")
    print("="*95)

    # Desglose Mes a Mes (Agosto, Septiembre, Octubre, Noviembre)
    print("\nDESGLOSE MES A MES (BENEFICIO NETO CONSOLIDADO TRAS COMISIONES):")
    print("-" * 75)

    all_trades = results["ES"]["trade_list"] + results["NQ"]["trade_list"]
    all_trades.sort(key=lambda x: x["day"] + " " + x["time"])

    monthly = {}
    for t in all_trades:
        m_key = t["day"][:7] # YYYY-MM
        if m_key not in monthly:
            monthly[m_key] = {"trades": 0, "wins": 0, "net": 0.0}
        monthly[m_key]["trades"] += 1
        monthly[m_key]["net"] += t["net"]
        if t["net"] > 0: monthly[m_key]["wins"] += 1

    month_names = {
        "2026-08": "Agosto 2026",
        "2026-09": "Septiembre 2026",
        "2026-10": "Octubre 2026",
        "2026-11": "Noviembre 2026 (Primeros 3 días)"
    }

    for m_key, m_stat in sorted(monthly.items()):
        m_wr = (m_stat["wins"] / m_stat["trades"] * 100) if m_stat["trades"] > 0 else 0
        name = month_names.get(m_key, m_key)
        icon = "🟢 POSITIVO" if m_stat["net"] > 0 else "🔴 NEGATIVO"
        print(f"• {name:<35} | Trades: {m_stat['trades']:3d} | Win Rate: {m_wr:5.1f}% | Neto: ${m_stat['net']:+10.2f} {icon}")

    print("-" * 75)
    print(f"🎯 BENEFICIO NETO TOTAL A 3 MESES (ES + NQ): ${port_net:+,.2f} USD")
    print("="*75)

if __name__ == "__main__":
    run_3months_analysis()
