import json
import zoneinfo
import sys
import io
from datetime import datetime, timezone
from core.vwap_bands import PrecisionVWAP, Candle15M
from core.schedule_guardian import ScheduleGuardian

# Set UTF-8 encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CONTRACTS = 2

SYMBOLS_SPECS = {
    "ES": {
        "file": "cache_m30_3months_ES.json",
        "tick_size": 0.25,
        "tick_val_total": 12.50 * CONTRACTS, # $25.00 por tick para 2 contratos
        "comm_total": 8.00, # $4.00 RT * 2 contratos
        "name": "E-mini S&P 500",
        "max_risk_ticks": 24
    },
    "NQ": {
        "file": "cache_m30_3months_NQ.json",
        "tick_size": 0.25,
        "tick_val_total": 5.00 * CONTRACTS, # $10.00 por tick para 2 contratos
        "comm_total": 8.00,
        "name": "E-mini Nasdaq 100",
        "max_risk_ticks": 30
    },
    "CL": {
        "file": "cache_m30_3months_CL.json",
        "tick_size": 0.01,
        "tick_val_total": 10.00 * CONTRACTS, # $20.00 por tick para 2 contratos
        "comm_total": 8.00,
        "name": "Crude Oil (Petróleo)",
        "max_risk_ticks": 30
    },
    "YM": {
        "file": "cache_m30_3months_YM.json",
        "tick_size": 1.0,
        "tick_val_total": 0.50 * CONTRACTS, # $1.00 por tick para 2 contratos (Micro)
        "comm_total": 3.00, # $1.50 RT * 2 contratos
        "name": "Micro Dow Jones",
        "max_risk_ticks": 30
    }
}

def backtest_symbol_corrected(symbol: str, spec: dict) -> dict:
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
    pending_signal = None
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
            pending_signal = None

        # 1. Si había una señal pendiente de la vela anterior, ENTRAR EN LA APERTURA DE ESTA VELA
        if pending_signal is not None and current_position is None:
            sig = pending_signal
            pending_signal = None

            is_allowed, _, _ = guardian.evaluate_market_status(dt_ct)
            # Control de riesgo diario preventivo Topstep
            if is_allowed and daily_map[day_str]["net_pnl"] > -1500.0:
                entry = round(round(bar["open"] / spec["tick_size"]) * spec["tick_size"], 4)
                
                if sig["action"] == "BUY":
                    # SL a 5 ticks del VWAP de la vela de señal
                    sl = round(round((sig["vwap"] - (5 * spec["tick_size"])) / spec["tick_size"]) * spec["tick_size"], 4)
                    # TP = 50% de la distancia del VWAP a la banda superior, aplicado desde la APERTURA
                    tp_size = sig["band_dist"] * 0.5
                    tp = round(round((entry + tp_size) / spec["tick_size"]) * spec["tick_size"], 4)
                    risk_t = int(round((entry - sl) / spec["tick_size"]))
                    rew_t = int(round((tp - entry) / spec["tick_size"]))
                    if risk_t > 0 and rew_t > 0 and risk_t <= spec["max_risk_ticks"]:
                        current_position = {
                            "symbol": symbol, "day": day_str, "time": dt_ct.strftime("%H:%M CT"),
                            "action": "BUY", "entry": entry, "sl": sl, "tp": tp,
                            "risk_ticks": risk_t, "reward_ticks": rew_t, "contracts": CONTRACTS
                        }

                elif sig["action"] == "SELL":
                    # SL a 5 ticks del VWAP de la vela de señal
                    sl = round(round((sig["vwap"] + (5 * spec["tick_size"])) / spec["tick_size"]) * spec["tick_size"], 4)
                    # TP = 50% de la distancia del VWAP a la banda inferior, aplicado desde la APERTURA
                    tp_size = sig["band_dist"] * 0.5
                    tp = round(round((entry - tp_size) / spec["tick_size"]) * spec["tick_size"], 4)
                    risk_t = int(round((sl - entry) / spec["tick_size"]))
                    rew_t = int(round((entry - tp) / spec["tick_size"]))
                    if risk_t > 0 and rew_t > 0 and risk_t <= spec["max_risk_ticks"]:
                        current_position = {
                            "symbol": symbol, "day": day_str, "time": dt_ct.strftime("%H:%M CT"),
                            "action": "SELL", "entry": entry, "sl": sl, "tp": tp,
                            "risk_ticks": risk_t, "reward_ticks": rew_t, "contracts": CONTRACTS
                        }

        # Actualizar VWAP con la vela actual
        vwap, up, low = vwap_calc.update_candle(bar["high"], bar["low"], bar["close"], bar["volume"])
        c = Candle15M(bar["timestamp"], dt_ct.strftime("%H:%M CT"), bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"], vwap, up, low, is_closed=True)
        session_candles.append(c)

        # 2. Gestionar posición abierta durante la vela
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
                    if c.low <= pos["sl"]: hit_sl = True
                else:
                    if c.low <= pos["tp"]: hit_tp = True
                    if c.high >= pos["sl"]: hit_sl = True

                # Si toca ambos en la misma vela, asumimos SL por conservadurismo institucional
                if hit_tp and hit_sl:
                    hit_tp = False

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

        # 3. Detectar si esta vela cerrada genera señal para operar en la APERTURA de la siguiente vela
        if current_position is None and len(session_candles) >= 2:
            is_allowed, _, _ = guardian.evaluate_market_status(dt_ct)
            if is_allowed:
                c_prev = session_candles[-2]
                c_signal = session_candles[-1]

                # SEÑAL BUY: Vela previa confirma lado alcista, vela actual toca VWAP con mecha y cierra por encima
                if c_prev.close >= c_prev.vwap and c_signal.low <= c_signal.vwap and c_signal.close > c_signal.vwap:
                    pending_signal = {
                        "action": "BUY",
                        "vwap": c_signal.vwap,
                        "band_dist": c_signal.upper_band_1 - c_signal.vwap,
                        "signal_time": dt_ct.strftime("%H:%M CT")
                    }

                # SEÑAL SELL: Vela previa confirma lado bajista, vela actual toca VWAP con mecha y cierra por debajo
                elif c_prev.close <= c_prev.vwap and c_signal.high >= c_signal.vwap and c_signal.close < c_signal.vwap:
                    pending_signal = {
                        "action": "SELL",
                        "vwap": c_signal.vwap,
                        "band_dist": c_signal.vwap - c_signal.lower_band_1,
                        "signal_time": dt_ct.strftime("%H:%M CT")
                    }

    tot_trades = len(trades)
    tot_wins = sum(1 for t in trades if t["net"] > 0)
    tot_losses = sum(1 for t in trades if t["net"] <= 0)
    gross_p = sum(t["gross"] for t in trades if t["gross"] > 0)
    gross_l = sum(abs(t["gross"]) for t in trades if t["gross"] <= 0)
    comm = sum(t["comm"] for t in trades)
    net_pnl = gross_p - gross_l - comm
    win_rate = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0

    return {
        "symbol": symbol,
        "name": spec["name"],
        "total_trades": tot_trades,
        "wins": tot_wins,
        "losses": tot_losses,
        "win_rate": win_rate,
        "gross_profit": gross_p,
        "gross_loss": gross_l,
        "commissions": comm,
        "net_pnl": net_pnl,
        "trades": trades,
        "daily_map": daily_map
    }

if __name__ == "__main__":
    results = {}
    print("\nCalculando con la especificación exacta de TP y Apertura de vela...")
    for sym, spec in SYMBOLS_SPECS.items():
        results[sym] = backtest_symbol_corrected(sym, spec)

    print("\n" + "="*105)
    print(" INFORME AJUSTADO: TP = 50% DE DISTANCIA VWAP->BANDA APLICADO DESDE APERTURA DE VELA SIGUIENTE")
    print(" Periodo: 3 Meses (M30) | 2 Contratos por Trade | Comisiones Deducidas")
    print("="*105)
    print(f"| {'Símbolo':<8} | {'Nombre del Activo':<20} | {'Trades':<6} | {'Win Rate':<9} | {'Ganancia Bruta':<15} | {'Pérdida Bruta':<15} | {'Comisiones':<12} | {'NETO FINAL':<14} |")
    print("|" + "-"*10 + "+" + "-"*22 + "+" + "-"*8 + "+" + "-"*11 + "+" + "-"*17 + "+" + "-"*17 + "+" + "-"*14 + "+" + "-"*16 + "|")

    tot_trades_all = sum(r["total_trades"] for r in results.values())
    tot_wins_all = sum(r["wins"] for r in results.values())
    tot_losses_all = sum(r["losses"] for r in results.values())
    tot_gp_all = sum(r["gross_profit"] for r in results.values())
    tot_gl_all = sum(r["gross_loss"] for r in results.values())
    tot_comm_all = sum(r["commissions"] for r in results.values())
    tot_net_all = sum(r["net_pnl"] for r in results.values())
    avg_wr_all = (tot_wins_all / tot_trades_all * 100) if tot_trades_all > 0 else 0.0

    for sym, r in results.items():
        print(f"| {sym:<8} | {r['name']:<20} | {r['total_trades']:^6} | {r['win_rate']:>6.1f}  % | $ {r['gross_profit']:>10.2f}    | -$ {r['gross_loss']:>10.2f}    | -$ {r['commissions']:>7.2f}   | $ {r['net_pnl']:>10.2f}   |")

    print("|" + "-"*10 + "+" + "-"*22 + "+" + "-"*8 + "+" + "-"*11 + "+" + "-"*17 + "+" + "-"*17 + "+" + "-"*14 + "+" + "-"*16 + "|")
    print(f"| {'TOTAL':<8} | {'PORTAFOLIO (2 CTRS)':<20} | {tot_trades_all:^6} | {avg_wr_all:>6.1f}  % | $ {tot_gp_all:>10.2f}   | -$ {tot_gl_all:>10.2f}   | -$ {tot_comm_all:>7.2f}   | $ {tot_net_all:>10.2f}   |")
    print("="*105)
