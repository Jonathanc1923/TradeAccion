"""
Motor de Backtest a 30 Días Multi-Símbolo en Velas de 30 Minutos (M30)
Datos reales de TopstepX API (Octubre 04 a Noviembre 03, 2026)
Símbolos analizados:
1. ES (E-mini S&P 500)
2. NQ (E-mini Nasdaq 100)
3. YM (Micro Dow Jones)
4. CL (Crude Oil)
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

# Metadatos de comisiones y multiplicadores por contrato
SYMBOL_SPECS = {
    "ES": {"file": "cache_m30_30d_ES.json", "tick_size": 0.25, "tick_val": 12.50, "comm": 4.00, "name": "E-mini S&P 500", "max_risk_ticks": 24},
    "NQ": {"file": "cache_m30_30d_NQ.json", "tick_size": 0.25, "tick_val": 5.00, "comm": 4.00, "name": "E-mini Nasdaq 100", "max_risk_ticks": 48},
    "YM": {"file": "cache_m30_30d_YM.json", "tick_size": 1.00, "tick_val": 0.50, "comm": 1.50, "name": "Micro Dow Jones", "max_risk_ticks": 30},
    "CL": {"file": "cache_m30_30d_CL.json", "tick_size": 0.01, "tick_val": 10.00, "comm": 4.00, "name": "Crude Oil (Petróleo)", "max_risk_ticks": 35}
}

def backtest_single_symbol(symbol: str, spec: dict) -> dict:
    if not os.path.exists(spec["file"]):
        return None

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
    tot_comm = sum(t["gross"] - t["net"] for t in trades)
    tot_net = sum(t["net"] for t in trades)
    wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0

    return {
        "symbol": symbol, "name": spec["name"], "trades": tot_trades,
        "wins": tot_wins, "losses": tot_losses, "win_rate": wr,
        "gross_pnl": tot_gross, "commissions": tot_comm, "net_pnl": tot_net,
        "daily_map": daily_map, "trade_list": trades
    }

def run_portfolio_30d():
    print("\n" + "="*95)
    print(" INFORME OFICIAL MULTI-SÍMBOLO A 30 DÍAS EN VELAS DE 30 MINUTOS (M30)")
    print(" Periodo: 04 de Octubre a 03 de Noviembre, 2026 | Datos: TopstepX API Oficial")
    print(" Cuenta: PRAC-V2-460183-95785678 ($150,000) | Comisiones Deducidas en cada trade")
    print("="*95)

    symbols_tested = ["ES", "NQ", "YM", "CL"]
    results = {}
    total_port_net = 0.0
    total_port_trades = 0
    total_port_wins = 0
    total_port_comm = 0.0

    print(f"\n| {'Símbolo':<8} | {'Nombre del Activo':<22} | {'Trades':^6} | {'Win Rate':^9} | {'Bruto (Gross)':^13} | {'Comisiones':^11} | {'NETO FINAL':^13} |")
    print("|" + "-"*10 + "|" + "-"*24 + "|" + "-"*8 + "|" + "-"*11 + "|" + "-"*15 + "|" + "-"*13 + "|" + "-"*15 + "|")

    for s in symbols_tested:
        res = backtest_single_symbol(s, SYMBOL_SPECS[s])
        if res:
            results[s] = res
            total_port_net += res["net_pnl"]
            total_port_trades += res["trades"]
            total_port_wins += res["wins"]
            total_port_comm += res["commissions"]
            print(f"| {res['symbol']:<8} | {res['name']:<22} | {res['trades']:^6} | {res['win_rate']:^8.1f}% | ${res['gross_pnl']:^+11.2f} | -${res['commissions']:^9.2f} | ${res['net_pnl']:^+11.2f} |")

    print("|" + "-"*10 + "|" + "-"*24 + "|" + "-"*8 + "|" + "-"*11 + "|" + "-"*15 + "|" + "-"*13 + "|" + "-"*15 + "|")
    overall_wr = (total_port_wins / total_port_trades * 100) if total_port_trades > 0 else 0
    print(f"| {'TOTAL':<8} | {'PORTAFOLIO GLOBAL':<22} | {total_port_trades:^6} | {overall_wr:^8.1f}% | {'--':^13} | -${total_port_comm:^9.2f} | ${total_port_net:^+11.2f} |")
    print("="*95)

    # Desglose agrupado por semanas a lo largo de los 30 días
    print("\nRESUMEN SEMANA POR SEMANA DEL PORTAFOLIO A 30 DÍAS:")
    print("-" * 75)
    
    # Agrupar todos los trades por fecha
    all_trades_combined = []
    for s, r in results.items():
        all_trades_combined.extend(r["trade_list"])
    all_trades_combined.sort(key=lambda x: x["day"] + " " + x["time"])

    # Agrupar por semanas
    weeks = {}
    for t in all_trades_combined:
        # Calcular semana del mes
        dt = datetime.strptime(t["day"], "%Y-%m-%d")
        week_num = f"Semana {dt.isocalendar()[1]} ({t['day'][:7]})"
        if week_num not in weeks:
            weeks[week_num] = {"trades": 0, "net": 0.0, "wins": 0}
        weeks[week_num]["trades"] += 1
        weeks[week_num]["net"] += t["net"]
        if t["net"] > 0: weeks[week_num]["wins"] += 1

    for w_name, w_stat in sorted(weeks.items()):
        w_wr = (w_stat["wins"] / w_stat["trades"] * 100) if w_stat["trades"] > 0 else 0
        icon = "🟢 POSITIVA" if w_stat["net"] > 0 else "🔴 NEGATIVA"
        print(f"• {w_name:<26} | Trades: {w_stat['trades']:2d} | Win Rate: {w_wr:5.1f}% | PnL Neto: ${w_stat['net']:+9.2f} {icon}")

    print("-" * 75)
    print(f"🎯 BENEFICIO NETO TOTAL DEL PORTAFOLIO A 30 DÍAS: ${total_port_net:+,.2f} USD")
    print("="*75)

if __name__ == "__main__":
    run_portfolio_30d()
