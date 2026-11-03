"""
Informe Oficial Detallado Trade por Trade: 7 Días en Velas de 30 Minutos (M30)
Datos de TopstepX API | Cuenta PRAC-V2-460183-95785678 ($150,000)
Comisión: $4.00 USD Round-Turn por cada trade
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

def generate_report():
    with open('cache_bars_topstepx_m30.json', 'r', encoding='utf-8') as f:
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

    daily_stats = {}
    session_candles = []
    current_position = None
    last_session_date = None
    all_trades = []

    for bar in bars:
        dt_utc = datetime.fromtimestamp(bar['timestamp'], timezone.utc)
        dt_ct = dt_utc.astimezone(tz_ct)
        day_str = dt_ct.strftime('%Y-%m-%d')
        if day_str not in daily_stats:
            daily_stats[day_str] = {'trades': [], 'wins': 0, 'losses': 0, 'gross': 0.0, 'comm': 0.0, 'net': 0.0}

        if last_session_date != day_str and dt_ct.hour >= 17:
            vwap_calc.reset()
            session_candles.clear()
            last_session_date = day_str

        vwap, up, low = vwap_calc.update_candle(bar['high'], bar['low'], bar['close'], bar['volume'])
        c = Candle15M(bar['timestamp'], dt_ct.strftime('%H:%M CT'), bar['open'], bar['high'], bar['low'], bar['close'], bar['volume'], vwap, up, low, is_closed=True)
        session_candles.append(c)

        # 1. Gestionar posición
        if current_position is not None:
            pos = current_position
            if guardian.should_flatten_immediately(dt_ct):
                exit_p = c.close
                ticks = (exit_p - pos['entry']) / 0.25 if pos['action'] == 'BUY' else (pos['entry'] - exit_p) / 0.25
                gross = ticks * 12.50
                net = gross - COMMISSION
                pos.update({'exit': exit_p, 'gross': gross, 'net': net, 'res': 'FLATTEN_15:05', 'exit_time': dt_ct.strftime('%H:%M CT')})
                all_trades.append(pos)
                daily_stats[pos['day']]['trades'].append(pos)
                daily_stats[pos['day']]['gross'] += gross
                daily_stats[pos['day']]['comm'] += COMMISSION
                daily_stats[pos['day']]['net'] += net
                if net > 0: daily_stats[pos['day']]['wins'] += 1
                else: daily_stats[pos['day']]['losses'] += 1
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
                    gross = pos['reward_ticks'] * 12.50
                    net = gross - COMMISSION
                    pos.update({'exit': pos['tp'], 'gross': gross, 'net': net, 'res': 'TP_HIT', 'exit_time': dt_ct.strftime('%H:%M CT')})
                    all_trades.append(pos)
                    daily_stats[pos['day']]['trades'].append(pos)
                    daily_stats[pos['day']]['gross'] += gross
                    daily_stats[pos['day']]['comm'] += COMMISSION
                    daily_stats[pos['day']]['net'] += net
                    daily_stats[pos['day']]['wins'] += 1
                    current_position = None
                elif hit_sl:
                    gross = -pos['risk_ticks'] * 12.50
                    net = gross - COMMISSION
                    pos.update({'exit': pos['sl'], 'gross': gross, 'net': net, 'res': 'SL_HIT', 'exit_time': dt_ct.strftime('%H:%M CT')})
                    all_trades.append(pos)
                    daily_stats[pos['day']]['trades'].append(pos)
                    daily_stats[pos['day']]['gross'] += gross
                    daily_stats[pos['day']]['comm'] += COMMISSION
                    daily_stats[pos['day']]['net'] += net
                    daily_stats[pos['day']]['losses'] += 1
                    current_position = None

        # 2. Nueva entrada
        if current_position is None and len(session_candles) >= 2:
            is_allowed, _, _ = guardian.evaluate_market_status(dt_ct)
            if is_allowed and daily_stats[day_str]['net'] > -1000.0:
                c_prev = session_candles[-2]
                c_curr = session_candles[-1]

                # BUY
                if c_prev.close >= c_prev.vwap and c_curr.low <= c_curr.vwap and c_curr.close > c_curr.vwap:
                    entry = round(round(c_curr.close / 0.25) * 0.25, 2)
                    sl = round(round((c_curr.vwap - (5 * 0.25)) / 0.25) * 0.25, 2)
                    tp_dist = (c_curr.upper_band_1 - c_curr.vwap) * 0.5
                    tp = round(round((entry + tp_dist) / 0.25) * 0.25, 2)
                    risk_t = int(round((entry - sl) / 0.25))
                    rew_t = int(round((tp - entry) / 0.25))
                    if risk_t > 0 and rew_t > 0 and risk_t <= 24:
                        current_position = {
                            'day': day_str, 'time': dt_ct.strftime('%H:%M CT'),
                            'action': 'BUY', 'entry': entry, 'sl': sl, 'tp': tp,
                            'risk_ticks': risk_t, 'reward_ticks': rew_t,
                            'vwap': round(c_curr.vwap, 2)
                        }

                # SELL
                elif c_prev.close <= c_prev.vwap and c_curr.high >= c_curr.vwap and c_curr.close < c_curr.vwap:
                    entry = round(round(c_curr.close / 0.25) * 0.25, 2)
                    sl = round(round((c_curr.vwap + (5 * 0.25)) / 0.25) * 0.25, 2)
                    tp_dist = (c_curr.vwap - c_curr.lower_band_1) * 0.5
                    tp = round(round((entry - tp_dist) / 0.25) * 0.25, 2)
                    risk_t = int(round((sl - entry) / 0.25))
                    rew_t = int(round((entry - tp) / 0.25))
                    if risk_t > 0 and rew_t > 0 and risk_t <= 24:
                        current_position = {
                            'day': day_str, 'time': dt_ct.strftime('%H:%M CT'),
                            'action': 'SELL', 'entry': entry, 'sl': sl, 'tp': tp,
                            'risk_ticks': risk_t, 'reward_ticks': rew_t,
                            'vwap': round(c_curr.vwap, 2)
                        }

    print("\n" + "="*95)
    print(" INFORME OFICIAL COMPLETO: 7 DÍAS EN VELAS DE 30 MINUTOS (M30) CON COMISIONES")
    print(" Instrumento: E-Mini S&P 500 (CON.F.US.EP.Z26) | Fuente: TopstepX API Oficial")
    print(f" Cuenta: PRAC-V2-460183-95785678 ($150k) | Comisión: ${COMMISSION:.2f} Round-Turn")
    print("="*95)

    cumulative_net = 0.0
    tot_trades_all = 0
    tot_wins_all = 0
    tot_losses_all = 0
    tot_comm_all = 0.0
    tot_gross_all = 0.0

    for d, s in sorted(daily_stats.items()):
        n = len(s['trades'])
        tot_trades_all += n
        tot_wins_all += s['wins']
        tot_losses_all += s['losses']
        tot_comm_all += s['comm']
        tot_gross_all += s['gross']
        cumulative_net += s['net']

        print(f"\n📅 FECHA: {d} | TRADES: {n:2d} | W: {s['wins']} L: {s['losses']} | BRUTO: ${s['gross']:+8.2f} | COMISIÓN: -${s['comm']:5.2f} | NETO DEL DÍA: ${s['net']:+8.2f} | BALANCE ACUM: ${cumulative_net:+8.2f}")
        print("-" * 95)
        if n == 0:
            print("   (Sin operaciones: El mercado no tocó el VWAP con mecha o estuvo en bloqueo/mantenimiento)")
        else:
            for idx, t in enumerate(s['trades'], 1):
                icon = "🟢 GANADORA" if t['net'] > 0 else "🔴 PERDEDORA"
                print(f"   [{idx}] {t['time']} | {t['action']} @ {t['entry']:.2f} | VWAP: {t['vwap']:.2f} "
                      f"| SL: {t['sl']:.2f} | TP: {t['tp']:.2f} -> Salida: {t['exit']:.2f} ({t['res']}) "
                      f"| Bruto: ${t['gross']:+7.2f} | Neto: ${t['net']:+7.2f} {icon}")

    print("\n" + "="*95)
    print(" RESUMEN TOTAL DE LA PRUEBA A 7 DÍAS (M30)")
    print("="*95)
    wr = (tot_wins_all / tot_trades_all * 100) if tot_trades_all > 0 else 0
    print(f" Total Operaciones Realizadas: {tot_trades_all}")
    print(f" Operaciones Ganadoras: {tot_wins_all} | Perdedoras: {tot_losses_all}")
    print(f" Tasa de Acierto (Win Rate): {wr:.1f}%")
    print(f" Beneficio Bruto Total (Gross PnL): ${tot_gross_all:+,.2f} USD")
    print(f" Total Comisiones Pagadas a Topstep: -${tot_comm_all:,.2f} USD")
    print(f" -------------------------------------------------------------------")
    print(f" BENEFICIO NETO FINAL (Net PnL): ${cumulative_net:+,.2f} USD")
    print(f" Promedio de Operaciones por Día: {tot_trades_all / len(daily_stats):.1f}")
    print("="*95)

if __name__ == "__main__":
    generate_report()
