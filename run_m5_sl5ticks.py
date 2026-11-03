"""
Cálculo Detallado: Estrategia en Velas de 5 Minutos (M5) con SL a 5 Ticks de VWAP
Datos directos de TopstepX API (1,629 velas reales)
Comisión: $4.00 USD por vuelta (Round-Turn)
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

COMMISSION = 4.00

def run_m5_sl5():
    with open('cache_bars_topstepx_m5.json', 'r', encoding='utf-8') as f:
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

    meta = AVAILABLE_SYMBOLS['ES'] # $12.50 / tick, tick_size = 0.25
    guardian = ScheduleGuardian()
    vwap_calc = PrecisionVWAP(std_multiplier=1.0)
    tz_ct = zoneinfo.ZoneInfo('America/Chicago')

    daily_results = {}
    session_candles = []
    current_position = None
    last_session_date = None
    trades = []

    for bar in bars:
        dt_utc = datetime.fromtimestamp(bar['timestamp'], timezone.utc)
        dt_ct = dt_utc.astimezone(tz_ct)
        day_str = dt_ct.strftime('%Y-%m-%d')
        if day_str not in daily_results:
            daily_results[day_str] = {
                'date': day_str, 'trades': [], 'wins': 0, 'losses': 0,
                'gross_pnl': 0.0, 'commissions': 0.0, 'net_pnl': 0.0
            }

        if last_session_date != day_str and dt_ct.hour >= 17:
            vwap_calc.reset()
            session_candles.clear()
            last_session_date = day_str

        vwap, up, low = vwap_calc.update_candle(bar['high'], bar['low'], bar['close'], bar['volume'])
        c = Candle15M(bar['timestamp'], dt_ct.strftime('%H:%M CT'), bar['open'], bar['high'], bar['low'], bar['close'], bar['volume'], vwap, up, low, is_closed=True)
        session_candles.append(c)

        # 1. Gestionar posición abierta
        if current_position is not None:
            pos = current_position
            if guardian.should_flatten_immediately(dt_ct):
                exit_p = c.close
                ticks = (exit_p - pos['entry']) / meta.tick_size if pos['action'] == 'BUY' else (pos['entry'] - exit_p) / meta.tick_size
                gross_pnl = ticks * meta.tick_value
                net_pnl = gross_pnl - COMMISSION
                pos.update({'exit': exit_p, 'gross_pnl': gross_pnl, 'net_pnl': net_pnl, 'result': 'FLATTEN_15:05', 'exit_time': dt_ct.strftime('%H:%M CT')})
                trades.append(pos)
                daily_results[pos['day']]['trades'].append(pos)
                daily_results[pos['day']]['gross_pnl'] += gross_pnl
                daily_results[pos['day']]['commissions'] += COMMISSION
                daily_results[pos['day']]['net_pnl'] += net_pnl
                if net_pnl > 0: daily_results[pos['day']]['wins'] += 1
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
                    gross_pnl = pos['reward_ticks'] * meta.tick_value
                    net_pnl = gross_pnl - COMMISSION
                    pos.update({'exit': pos['tp'], 'gross_pnl': gross_pnl, 'net_pnl': net_pnl, 'result': 'TP_HIT', 'exit_time': dt_ct.strftime('%H:%M CT')})
                    trades.append(pos)
                    daily_results[pos['day']]['trades'].append(pos)
                    daily_results[pos['day']]['gross_pnl'] += gross_pnl
                    daily_results[pos['day']]['commissions'] += COMMISSION
                    daily_results[pos['day']]['net_pnl'] += net_pnl
                    daily_results[pos['day']]['wins'] += 1
                    current_position = None
                elif hit_sl:
                    gross_pnl = -pos['risk_ticks'] * meta.tick_value
                    net_pnl = gross_pnl - COMMISSION
                    pos.update({'exit': pos['sl'], 'gross_pnl': gross_pnl, 'net_pnl': net_pnl, 'result': 'SL_HIT', 'exit_time': dt_ct.strftime('%H:%M CT')})
                    trades.append(pos)
                    daily_results[pos['day']]['trades'].append(pos)
                    daily_results[pos['day']]['gross_pnl'] += gross_pnl
                    daily_results[pos['day']]['commissions'] += COMMISSION
                    daily_results[pos['day']]['net_pnl'] += net_pnl
                    daily_results[pos['day']]['losses'] += 1
                    current_position = None

        # 2. Nueva entrada
        if current_position is None and len(session_candles) >= 2:
            if daily_results[day_str]['net_pnl'] <= -1000.0:
                continue

            is_allowed, _, _ = guardian.evaluate_market_status(dt_ct)
            if is_allowed:
                c_prev = session_candles[-2]
                c_curr = session_candles[-1]

                # SEÑAL BUY: anterior >= vwap, mecha actual toca vwap (low <= vwap), cierre actual > vwap
                if c_prev.close >= c_prev.vwap and c_curr.low <= c_curr.vwap and c_curr.close > c_curr.vwap:
                    entry = round(round(c_curr.close / 0.25) * 0.25, 2)
                    # SL EXACTO A 5 TICKS DEBAJO DE VWAP
                    sl = round(round((c_curr.vwap - (5 * 0.25)) / 0.25) * 0.25, 2)
                    tp_dist = (c_curr.upper_band_1 - c_curr.vwap) * 0.5
                    tp = round(round((entry + tp_dist) / 0.25) * 0.25, 2)

                    risk_ticks = int(round((entry - sl) / 0.25))
                    reward_ticks = int(round((tp - entry) / 0.25))

                    if risk_ticks > 0 and reward_ticks > 0:
                        current_position = {
                            'day': day_str, 'time': dt_ct.strftime('%H:%M CT'),
                            'action': 'BUY', 'entry': entry, 'sl': sl, 'tp': tp,
                            'risk_ticks': risk_ticks, 'reward_ticks': reward_ticks,
                            'vwap': c_curr.vwap
                        }

                # SEÑAL SELL: anterior <= vwap, mecha actual toca vwap (high >= vwap), cierre actual < vwap
                elif c_prev.close <= c_prev.vwap and c_curr.high >= c_curr.vwap and c_curr.close < c_curr.vwap:
                    entry = round(round(c_curr.close / 0.25) * 0.25, 2)
                    # SL EXACTO A 5 TICKS ENCIMA DE VWAP
                    sl = round(round((c_curr.vwap + (5 * 0.25)) / 0.25) * 0.25, 2)
                    tp_dist = (c_curr.vwap - c_curr.lower_band_1) * 0.5
                    tp = round(round((entry - tp_dist) / 0.25) * 0.25, 2)

                    risk_ticks = int(round((sl - entry) / 0.25))
                    reward_ticks = int(round((entry - tp) / 0.25))

                    if risk_ticks > 0 and reward_ticks > 0:
                        current_position = {
                            'day': day_str, 'time': dt_ct.strftime('%H:%M CT'),
                            'action': 'SELL', 'entry': entry, 'sl': sl, 'tp': tp,
                            'risk_ticks': risk_ticks, 'reward_ticks': reward_ticks,
                            'vwap': c_curr.vwap
                        }

    # INFORME
    print("\n" + "="*85)
    print(" RESULTADOS DÍA POR DÍA: VELAS DE 5 MINUTOS (M5) | SL A 5 TICKS DE VWAP")
    print(" Contrato: CON.F.US.EP.Z26 (ES) | Comisión: $4.00 USD Round-Turn por trade")
    print(" Datos: 1,629 Velas de 5m de TopstepX API | Cuenta: PRAC-V2-460183-95785678")
    print("="*85)

    tot_trades = 0
    tot_wins = 0
    tot_losses = 0
    tot_gross = 0.0
    tot_comm = 0.0
    tot_net = 0.0

    for day, res in sorted(daily_results.items()):
        trades = res['trades']
        n = len(trades)
        tot_trades += n
        tot_wins += res['wins']
        tot_losses += res['losses']
        tot_gross += res['gross_pnl']
        tot_comm += res['commissions']
        tot_net += res['net_pnl']

        print(f"\n📅 FECHA: {day} | TRADES: {n:2d} | BRUTO: ${res['gross_pnl']:+8.2f} | COMISIÓN: -${res['commissions']:6.2f} | NETO: ${res['net_pnl']:+8.2f}")
        print("-" * 85)
        if n == 0:
            print("   (Sin operaciones: Sin test de VWAP con mecha o periodo de bloqueo/mantenimiento)")
        else:
            for idx, t in enumerate(trades[:5], 1):
                icon = "🟢" if t['net_pnl'] > 0 else "🔴"
                print(f"   [{idx}] {t['time']} | {t['action']} @ {t['entry']:.2f} | VWAP: {t['vwap']:.2f} | SL: {t['sl']:.2f} | TP: {t['tp']:.2f} "
                      f"-> Salida: {t['exit']:.2f} ({t['result']}) | Bruto: ${t['gross_pnl']:+7.2f} | Neto: ${t['net_pnl']:+7.2f} {icon}")
            if n > 5:
                print(f"   ... ({n - 5} operaciones adicionales en el día)")

    print("\n" + "="*85)
    print(" RESUMEN TOTAL DE LA PRUEBA EN 5 MINUTOS (M5) CON SL A 5 TICKS DE VWAP")
    print("="*85)
    win_rate = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    print(f" Total Operaciones Ejecutadas (M5): {tot_trades}")
    print(f" Ganadoras: {tot_wins} | Perdedoras: {tot_losses}")
    print(f" Tasa de Acierto (Win Rate): {win_rate:.1f}%")
    print(f" Beneficio Bruto (Gross PnL): ${tot_gross:+,.2f} USD")
    print(f" Total Comisiones Pagadas a Topstep: -${tot_comm:,.2f} USD (${COMMISSION} x {tot_trades})")
    print(f" -------------------------------------------------------------------")
    print(f" BENEFICIO NETO FINAL (Net PnL): ${tot_net:+,.2f} USD")
    if tot_trades > 0:
        print(f" Promedio de Operaciones por Día: {tot_trades / len(daily_results):.1f}")
    print("="*85)

if __name__ == "__main__":
    run_m5_sl5()
