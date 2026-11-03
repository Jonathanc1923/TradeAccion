"""
Backtest Engine a 3 Meses con 2 Contratos por Operación (Multi-Símbolo)
Periodo: 03 de Agosto a 03 de Noviembre de 2026
Datos: TopstepX API Oficial (1,900+ barras M30 continuas para ES, NQ, CL, YM)
Tamaño de posición: 2 CONTRATOS POR OPERACIÓN
Comisiones: Deducidas por cada contrato (x2) en cada operación
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

# Especificaciones con 2 CONTRATOS
CONTRACTS = 2

SPECS_2C = {
    "ES": {
        "file": "cache_m30_3months_ES.json",
        "tick_size": 0.25,
        "tick_val_single": 12.50,
        "tick_val_total": 12.50 * CONTRACTS, # $25.00 / tick con 2 contratos
        "comm_total": 4.00 * CONTRACTS,     # $8.00 round-turn con 2 contratos
        "name": "E-mini S&P 500",
        "max_risk_ticks": 24
    },
    "NQ": {
        "file": "cache_m30_3months_NQ.json",
        "tick_size": 0.25,
        "tick_val_single": 5.00,
        "tick_val_total": 5.00 * CONTRACTS,  # $10.00 / tick con 2 contratos
        "comm_total": 4.00 * CONTRACTS,     # $8.00 round-turn con 2 contratos
        "name": "E-mini Nasdaq 100",
        "max_risk_ticks": 48
    },
    "CL": {
        "file": "cache_m30_3months_CL.json",
        "tick_size": 0.01,
        "tick_val_single": 10.00,
        "tick_val_total": 10.00 * CONTRACTS, # $20.00 / tick con 2 contratos
        "comm_total": 4.00 * CONTRACTS,      # $8.00 round-turn con 2 contratos
        "name": "Crude Oil (Petróleo)",
        "max_risk_ticks": 35
    },
    "YM": {
        "file": "cache_m30_3months_YM.json",
        "tick_size": 1.00,
        "tick_val_single": 0.50,
        "tick_val_total": 0.50 * CONTRACTS,  # $1.00 / tick con 2 contratos
        "comm_total": 1.50 * CONTRACTS,      # $3.00 round-turn con 2 contratos
        "name": "Micro Dow Jones",
        "max_risk_ticks": 30
    }
}

def backtest_symbol_2c(symbol: str, spec: dict) -> dict:
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
            daily_map[day_str] = {
                "trades": [], "wins": 0, "losses": 0,
                "gross_profit": 0.0, "gross_loss": 0.0,
                "commissions": 0.0, "net_pnl": 0.0
            }

        # Reseteo diario de VWAP a las 17:00 CT
        if last_session_date != day_str and dt_ct.hour >= 17:
            vwap_calc.reset()
            session_candles.clear()
            last_session_date = day_str

        vwap, up, low = vwap_calc.update_candle(bar["high"], bar["low"], bar["close"], bar["volume"])
        c = Candle15M(bar["timestamp"], dt_ct.strftime("%H:%M CT"), bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"], vwap, up, low, is_closed=True)
        session_candles.append(c)

        # 1. Gestionar posición abierta con 2 contratos
        if current_position is not None:
            pos = current_position
            # Cierre forzoso Topstep a las 15:05 CT
            if guardian.should_flatten_immediately(dt_ct):
                exit_p = c.close
                ticks = (exit_p - pos["entry"]) / spec["tick_size"] if pos["action"] == "BUY" else (pos["entry"] - exit_p) / spec["tick_size"]
                gross = ticks * spec["tick_val_total"]
                net = gross - spec["comm_total"]
                pos.update({"exit": exit_p, "gross": gross, "comm": spec["comm_total"], "net": net, "res": "FLATTEN_15:05", "exit_time": dt_ct.strftime("%H:%M CT")})
                trades.append(pos)
                daily_map[pos["day"]]["trades"].append(pos)
                daily_map[pos["day"]]["commissions"] += spec["comm_total"]
                daily_map[pos["day"]]["net_pnl"] += net
                if gross > 0:
                    daily_map[pos["day"]]["gross_profit"] += gross
                    daily_map[pos["day"]]["wins"] += 1
                else:
                    daily_map[pos["day"]]["gross_loss"] += abs(gross)
                    daily_map[pos["day"]]["losses"] += 1
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
                    gross = pos["reward_ticks"] * spec["tick_val_total"]
                    net = gross - spec["comm_total"]
                    pos.update({"exit": pos["tp"], "gross": gross, "comm": spec["comm_total"], "net": net, "res": "TP_HIT", "exit_time": dt_ct.strftime("%H:%M CT")})
                    trades.append(pos)
                    daily_map[pos["day"]]["trades"].append(pos)
                    daily_map[pos["day"]]["gross_profit"] += gross
                    daily_map[pos["day"]]["commissions"] += spec["comm_total"]
                    daily_map[pos["day"]]["net_pnl"] += net
                    daily_map[pos["day"]]["wins"] += 1
                    current_position = None
                elif hit_sl:
                    gross = -pos["risk_ticks"] * spec["tick_val_total"]
                    net = gross - spec["comm_total"]
                    pos.update({"exit": pos["sl"], "gross": gross, "comm": spec["comm_total"], "net": net, "res": "SL_HIT", "exit_time": dt_ct.strftime("%H:%M CT")})
                    trades.append(pos)
                    daily_map[pos["day"]]["trades"].append(pos)
                    daily_map[pos["day"]]["gross_loss"] += abs(gross)
                    daily_map[pos["day"]]["commissions"] += spec["comm_total"]
                    daily_map[pos["day"]]["net_pnl"] += net
                    daily_map[pos["day"]]["losses"] += 1
                    current_position = None

        # 2. Nueva entrada
        if current_position is None and len(session_candles) >= 2:
            is_allowed, _, _ = guardian.evaluate_market_status(dt_ct)
            # Control de riesgo preventivo Topstep para 2 contratos
            if is_allowed and daily_map[day_str]["net_pnl"] > -1500.0:
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
                        current_position = {"symbol": symbol, "day": day_str, "time": dt_ct.strftime("%H:%M CT"), "action": "BUY", "entry": entry, "sl": sl, "tp": tp, "risk_ticks": risk_t, "reward_ticks": rew_t, "contracts": CONTRACTS}

                # SELL
                elif c_prev.close <= c_prev.vwap and c_curr.high >= c_curr.vwap and c_curr.close < c_curr.vwap:
                    entry = round(round(c_curr.close / spec["tick_size"]) * spec["tick_size"], 4)
                    sl = round(round((c_curr.vwap + (5 * spec["tick_size"])) / spec["tick_size"]) * spec["tick_size"], 4)
                    tp_dist = (c_curr.vwap - c_curr.lower_band_1) * 0.5
                    tp = round(round((entry - tp_dist) / spec["tick_size"]) * spec["tick_size"], 4)
                    risk_t = int(round((sl - entry) / spec["tick_size"]))
                    rew_t = int(round((entry - tp) / spec["tick_size"]))
                    if risk_t > 0 and rew_t > 0 and risk_t <= spec["max_risk_ticks"]:
                        current_position = {"symbol": symbol, "day": day_str, "time": dt_ct.strftime("%H:%M CT"), "action": "SELL", "entry": entry, "sl": sl, "tp": tp, "risk_ticks": risk_t, "reward_ticks": rew_t, "contracts": CONTRACTS}

    tot_trades = len(trades)
    tot_wins = sum(1 for t in trades if t["net"] > 0)
    tot_losses = sum(1 for t in trades if t["net"] <= 0)
    tot_gross_profit = sum(t["gross"] for t in trades if t["gross"] > 0)
    tot_gross_loss = sum(abs(t["gross"]) for t in trades if t["gross"] <= 0)
    tot_comm = sum(t["comm"] for t in trades)
    tot_net = sum(t["net"] for t in trades)
    wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0

    return {
        "symbol": symbol, "name": spec["name"], "trades": tot_trades,
        "wins": tot_wins, "losses": tot_losses, "win_rate": wr,
        "gross_profit": tot_gross_profit, "gross_loss": tot_gross_loss,
        "commissions": tot_comm, "net_pnl": tot_net,
        "daily_map": daily_map, "trade_list": trades
    }

def run_3months_2contracts_analysis():
    print("\n" + "="*105)
    print(" INFORME OFICIAL: 3 MESES COMPLETOS CON 2 CONTRATOS POR OPERACIÓN (M30)")
    print(" Periodo: 03 de Agosto a 03 de Noviembre, 2026 | Datos: TopstepX API Oficial")
    print(" Cuenta: PRAC-V2-460183-95785678 ($150,000) | Posición: 2 Contratos por Trade | Comisiones Deducidas")
    print("="*105)

    symbols = ["ES", "NQ", "CL", "YM"]
    results = {}
    for s in symbols:
        results[s] = backtest_symbol_2c(s, SPECS_2C[s])

    # 1. TABLA RESUMEN POR SÍMBOLO
    print(f"\n| {'Símbolo':<8} | {'Nombre del Activo':<20} | {'Trades':^6} | {'Win Rate':^9} | {'Ganancia Bruta':^15} | {'Pérdida Bruta':^15} | {'Comisiones':^12} | {'NETO FINAL':^14} |")
    print("|" + "-"*10 + "|" + "-"*22 + "|" + "-"*8 + "|" + "-"*11 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*14 + "|" + "-"*16 + "|")

    port_trades = 0
    port_wins = 0
    port_losses = 0
    port_gp = 0.0
    port_gl = 0.0
    port_comm = 0.0
    port_net = 0.0

    for s, res in results.items():
        port_trades += res["trades"]
        port_wins += res["wins"]
        port_losses += res["losses"]
        port_gp += res["gross_profit"]
        port_gl += res["gross_loss"]
        port_comm += res["commissions"]
        port_net += res["net_pnl"]
        print(f"| {res['symbol']:<8} | {res['name']:<20} | {res['trades']:^6} | {res['win_rate']:^8.1f}% | ${res['gross_profit']:^+13.2f} | -${res['gross_loss']:^13.2f} | -${res['commissions']:^10.2f} | ${res['net_pnl']:^+12.2f} |")

    print("|" + "-"*10 + "|" + "-"*22 + "|" + "-"*8 + "|" + "-"*11 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*14 + "|" + "-"*16 + "|")
    overall_wr = (port_wins / port_trades * 100) if port_trades > 0 else 0
    print(f"| {'TOTAL':<8} | {'PORTAFOLIO (2 CTRS)':<20} | {port_trades:^6} | {overall_wr:^8.1f}% | ${port_gp:^+13.2f} | -${port_gl:^13.2f} | -${port_comm:^10.2f} | ${port_net:^+12.2f} |")
    print("="*105)

    # 2. CONSOLIDAR DÍA POR DÍA DE LOS 3 MESES
    print("\nRESULTADOS CONSOLIDADOS DÍA POR DÍA A LO LARGO DE LOS 3 MESES (TODOS LOS SÍMBOLOS):")
    print("-" * 105)
    print(f"| {'Fecha':<12} | {'Día':<10} | {'Trades':^6} | {'W/L':^7} | {'Ganancia Bruta':^15} | {'Pérdida Bruta':^15} | {'Comisiones':^11} | {'Neto del Día':^14} | {'Balance Acum':^14} |")
    print("|" + "-"*14 + "|" + "-"*12 + "|" + "-"*8 + "|" + "-"*9 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*13 + "|" + "-"*16 + "|" + "-"*16 + "|")

    # Obtener todas las fechas ordenadas
    all_dates = set()
    for s in symbols:
        all_dates.update(results[s]["daily_map"].keys())

    cumulative_pnl = 0.0
    winning_days = 0
    losing_days = 0
    zero_days = 0

    for day in sorted(all_dates):
        d_trades = 0
        d_wins = 0
        d_losses = 0
        d_gp = 0.0
        d_gl = 0.0
        d_comm = 0.0
        d_net = 0.0

        for s in symbols:
            if day in results[s]["daily_map"]:
                sm = results[s]["daily_map"][day]
                d_trades += len(sm["trades"])
                d_wins += sm["wins"]
                d_losses += sm["losses"]
                d_gp += sm["gross_profit"]
                d_gl += sm["gross_loss"]
                d_comm += sm["commissions"]
                d_net += sm["net_pnl"]

        cumulative_pnl += d_net
        dt_obj = datetime.strptime(day, "%Y-%m-%d")
        day_name = dt_obj.strftime("%A")

        if d_trades == 0:
            zero_days += 1
            continue

        if d_net > 0: winning_days += 1
        elif d_net < 0: losing_days += 1

        print(f"| {day:<12} | {day_name:<10} | {d_trades:^6} | {d_wins}/{d_losses} | ${d_gp:^+13.2f} | -${d_gl:^13.2f} | -${d_comm:^9.2f} | ${d_net:^+12.2f} | ${cumulative_pnl:^+12.2f} |")

    print("|" + "-"*14 + "|" + "-"*12 + "|" + "-"*8 + "|" + "-"*9 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*13 + "|" + "-"*16 + "|" + "-"*16 + "|")
    print(f"\n📊 RESUMEN FINAL A 3 MESES (2 CONTRATOS):")
    print(f"• Días Operativos Positivos: {winning_days} días")
    print(f"• Días Operativos Negativos: {losing_days} días")
    print(f"• Tasa de Acierto de Días: {(winning_days / (winning_days + losing_days) * 100):.1f}% de los días con beneficio neto.")
    print(f"• Comisiones Totales Pagadas a Topstep (2 ctrs): -${port_comm:,.2f} USD")
    print(f"• Pérdida Bruta Total Absorbida: -${port_gl:,.2f} USD")
    print(f"• Ganancia Bruta Total Generada: +${port_gp:,.2f} USD")
    print(f"🎯 BENEFICIO NETO TOTAL EN EL BOLSILLO: ${port_net:+,.2f} USD")
    print("="*105)

if __name__ == "__main__":
    run_3months_2contracts_analysis()
