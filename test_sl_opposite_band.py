"""
Prueba Cuantitativa: ¿Qué pasa si colocamos el Stop Loss en la Banda Contraria?
Compara el SL original (5 ticks de VWAP) vs SL en Banda Contraria (Banda ±1σ opuesta).
Ambos con comisiones de $4.00 round-turn en TopstepX.
"""

import sys
import io
import os
import json
from datetime import datetime, timezone
import zoneinfo

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from config import AVAILABLE_SYMBOLS
from core.vwap_bands import PrecisionVWAP, Candle15M
from core.schedule_guardian import ScheduleGuardian

COMMISSION = 4.00 # $4 USD por trade

def evaluate_strategy_with_sl_mode(cache_file: str, timeframe_label: str, sl_mode: str):
    """
    sl_mode: '5_TICKS_VWAP' o 'OPPOSITE_BAND'
    """
    with open(cache_file, 'r', encoding='utf-8') as f:
        raw_bars = json.load(f)

    bars = []
    for b in raw_bars:
        iso_str = b.get('t') or b.get('datetime_iso')
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        bars.append({
            'timestamp': int(dt.timestamp()),
            'open': float(b.get('o') or b.get('open')), 
            'high': float(b.get('h') or b.get('high')), 
            'low': float(b.get('l') or b.get('low')), 
            'close': float(b.get('c') or b.get('close')),
            'volume': float(b.get('v') or b.get('volume') or 1000.0)
        })
    bars.sort(key=lambda x: x['timestamp'])

    meta = AVAILABLE_SYMBOLS['ES']
    guardian = ScheduleGuardian()
    vwap_calc = PrecisionVWAP(std_multiplier=1.0)
    tz_ct = zoneinfo.ZoneInfo('America/Chicago')

    session_candles = []
    current_position = None
    last_session_date = None

    trades = []
    daily_results = {}

    for bar in bars:
        dt_utc = datetime.fromtimestamp(bar['timestamp'], timezone.utc)
        dt_ct = dt_utc.astimezone(tz_ct)
        day_str = dt_ct.strftime('%Y-%m-%d')
        if day_str not in daily_results:
            daily_results[day_str] = {'pnl': 0.0, 'trades': 0, 'wins': 0, 'losses': 0}

        if last_session_date != day_str and dt_ct.hour >= 17:
            vwap_calc.reset()
            session_candles.clear()
            last_session_date = day_str

        vwap, up, low = vwap_calc.update_candle(bar['high'], bar['low'], bar['close'], bar['volume'])
        c = Candle15M(bar['timestamp'], dt_ct.strftime('%H:%M'), bar['open'], bar['high'], bar['low'], bar['close'], bar['volume'], vwap, up, low, is_closed=True)
        session_candles.append(c)

        # 1. Gestionar posición abierta
        if current_position is not None:
            pos = current_position
            if guardian.should_flatten_immediately(dt_ct):
                exit_p = c.close
                ticks = (exit_p - pos['entry']) / meta.tick_size if pos['action'] == 'BUY' else (pos['entry'] - exit_p) / meta.tick_size
                pnl = ticks * meta.tick_value - COMMISSION
                pos.update({'exit': exit_p, 'pnl': pnl, 'res': 'FLATTEN_15:05'})
                trades.append(pos)
                daily_results[pos['day']]['pnl'] += pnl
                daily_results[pos['day']]['trades'] += 1
                if pnl > 0: daily_results[pos['day']]['wins'] += 1
                else: daily_results[pos['day']]['losses'] += 1
                current_position = None
            else:
                hit_tp, hit_sl = False, False
                if pos['action'] == 'BUY':
                    if c.high >= pos['tp']: hit_tp = True
                    elif c.low <= pos['sl']: hit_sl = True
                else:
                    if c.low <= pos['tp']: hit_tp = True
                    elif c.high >= pos['sl']: hit_sl = True

                if hit_tp:
                    pnl = pos['reward_ticks'] * meta.tick_value - COMMISSION
                    pos.update({'exit': pos['tp'], 'pnl': pnl, 'res': 'TP_HIT'})
                    trades.append(pos)
                    daily_results[pos['day']]['pnl'] += pnl
                    daily_results[pos['day']]['trades'] += 1
                    daily_results[pos['day']]['wins'] += 1
                    current_position = None
                elif hit_sl:
                    pnl = -pos['risk_ticks'] * meta.tick_value - COMMISSION
                    pos.update({'exit': pos['sl'], 'pnl': pnl, 'res': 'SL_HIT'})
                    trades.append(pos)
                    daily_results[pos['day']]['pnl'] += pnl
                    daily_results[pos['day']]['trades'] += 1
                    daily_results[pos['day']]['losses'] += 1
                    current_position = None

        # 2. Nueva entrada
        if current_position is None and len(session_candles) >= 2:
            is_allowed, _, _ = guardian.evaluate_market_status(dt_ct)
            if is_allowed and daily_results[day_str]['pnl'] > -1000.0:
                c_prev = session_candles[-2]
                c_curr = session_candles[-1]

                # SEÑAL BUY
                if c_prev.close >= c_prev.vwap and c_curr.low <= c_curr.vwap and c_curr.close > c_curr.vwap:
                    entry = round(round(c_curr.close / 0.25) * 0.25, 2)
                    tp_dist = (c_curr.upper_band_1 - c_curr.vwap) * 0.5
                    tp = round(round((entry + tp_dist) / 0.25) * 0.25, 2)

                    if sl_mode == '5_TICKS_VWAP':
                        sl = round(round((c_curr.vwap - (5 * 0.25)) / 0.25) * 0.25, 2)
                    else:
                        # BANDA OPUESTA INFERIOR (-1σ)
                        sl = round(round(c_curr.lower_band_1 / 0.25) * 0.25, 2)

                    risk_ticks = int(round((entry - sl) / 0.25))
                    reward_ticks = int(round((tp - entry) / 0.25))

                    if risk_ticks > 0 and reward_ticks > 0:
                        # Filtro prudencial en modo 5 ticks
                        if sl_mode == '5_TICKS_VWAP' and risk_ticks > 24:
                            continue
                        current_position = {
                            'day': day_str, 'time': dt_ct.strftime('%H:%M'),
                            'action': 'BUY', 'entry': entry, 'sl': sl, 'tp': tp,
                            'risk_ticks': risk_ticks, 'reward_ticks': reward_ticks
                        }

                # SEÑAL SELL
                elif c_prev.close <= c_prev.vwap and c_curr.high >= c_curr.vwap and c_curr.close < c_curr.vwap:
                    entry = round(round(c_curr.close / 0.25) * 0.25, 2)
                    tp_dist = (c_curr.vwap - c_curr.lower_band_1) * 0.5
                    tp = round(round((entry - tp_dist) / 0.25) * 0.25, 2)

                    if sl_mode == '5_TICKS_VWAP':
                        sl = round(round((c_curr.vwap + (5 * 0.25)) / 0.25) * 0.25, 2)
                    else:
                        # BANDA OPUESTA SUPERIOR (+1σ)
                        sl = round(round(c_curr.upper_band_1 / 0.25) * 0.25, 2)

                    risk_ticks = int(round((sl - entry) / 0.25))
                    reward_ticks = int(round((entry - tp) / 0.25))

                    if risk_ticks > 0 and reward_ticks > 0:
                        if sl_mode == '5_TICKS_VWAP' and risk_ticks > 24:
                            continue
                        current_position = {
                            'day': day_str, 'time': dt_ct.strftime('%H:%M'),
                            'action': 'SELL', 'entry': entry, 'sl': sl, 'tp': tp,
                            'risk_ticks': risk_ticks, 'reward_ticks': reward_ticks
                        }

    tot_trades = len(trades)
    tot_wins = sum(1 for t in trades if t['pnl'] > 0)
    tot_losses = sum(1 for t in trades if t['pnl'] <= 0)
    tot_pnl = sum(t['pnl'] for t in trades)
    wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0
    avg_win = (sum(t['pnl'] for t in trades if t['pnl'] > 0) / tot_wins) if tot_wins > 0 else 0
    avg_loss = (sum(t['pnl'] for t in trades if t['pnl'] <= 0) / tot_losses) if tot_losses > 0 else 0

    print(f"| {timeframe_label:<8} | {sl_mode:<16} | {tot_trades:^6} | {wr:^8.1f}% | ${avg_win:^+9.2f} | ${avg_loss:^+9.2f} | ${tot_pnl:^+11.2f} |")
    return tot_pnl, wr, tot_trades

if __name__ == "__main__":
    print("\n" + "="*85)
    print(" COMPARATIVA RIGUROSA: SL A 5 TICKS DE VWAP VS SL EN BANDA CONTRARIA")
    print(" Datos: Barras Oficiales de TopstepX API | Comisiones incluidas: $4/trade")
    print("="*85)
    print(f"| {'TF':<8} | {'Modo de Stop Loss':<16} | {'Trades':^6} | {'Win Rate':^8} | {'Avg Win':^10} | {'Avg Loss':^10} | {'Net PnL Final':^13} |")
    print("|" + "-"*10 + "|" + "-"*18 + "|" + "-"*8 + "|" + "-"*10 + "|" + "-"*12 + "|" + "-"*12 + "|" + "-"*15 + "|")

    # M30
    evaluate_strategy_with_sl_mode('cache_bars_topstepx_m30.json', 'M30', '5_TICKS_VWAP')
    evaluate_strategy_with_sl_mode('cache_bars_topstepx_m30.json', 'M30', 'OPPOSITE_BAND')
    print("|" + "-"*10 + "|" + "-"*18 + "|" + "-"*8 + "|" + "-"*10 + "|" + "-"*12 + "|" + "-"*12 + "|" + "-"*15 + "|")

    # M15
    evaluate_strategy_with_sl_mode('cache_bars_topstepx.json', 'M15', '5_TICKS_VWAP')
    evaluate_strategy_with_sl_mode('cache_bars_topstepx.json', 'M15', 'OPPOSITE_BAND')
    print("="*85)
