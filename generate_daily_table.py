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

specs = {
    'ES': {'file': 'cache_m30_3months_ES.json', 'tick': 0.25, 'val': 12.50 * CONTRACTS, 'comm': 4.00 * CONTRACTS, 'max_risk': 24, 'name': 'E-mini S&P 500'},
    'NQ': {'file': 'cache_m30_3months_NQ.json', 'tick': 0.25, 'val': 5.00 * CONTRACTS, 'comm': 4.00 * CONTRACTS, 'max_risk': 48, 'name': 'E-mini Nasdaq 100'},
    'CL': {'file': 'cache_m30_3months_CL.json', 'tick': 0.01, 'val': 10.00 * CONTRACTS, 'comm': 4.00 * CONTRACTS, 'max_risk': 35, 'name': 'Crude Oil (Petróleo)'},
    'YM': {'file': 'cache_m30_3months_YM.json', 'tick': 1.00, 'val': 0.50 * CONTRACTS, 'comm': 1.50 * CONTRACTS, 'max_risk': 30, 'name': 'Micro Dow Jones'}
}

def run_daily_breakdown():
    guardian = ScheduleGuardian()
    tz = zoneinfo.ZoneInfo('America/Chicago')
    daily_all = {} # day_str -> {'trades': 0, 'wins': 0, 'losses': 0, 'gross_profit': 0.0, 'gross_loss': 0.0, 'commissions': 0.0, 'net_pnl': 0.0, 'day_name': ''}

    for sym, sp in specs.items():
        with open(sp['file'], 'r', encoding='utf-8') as f:
            raw = json.load(f)
        bars = []
        for b in raw:
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

        vwap_calc = PrecisionVWAP(1.0)
        daily_sym = {}
        session_candles = []
        pos = None
        sig = None
        last_day = None

        for b in bars:
            dt_ct = datetime.fromtimestamp(b['timestamp'], timezone.utc).astimezone(tz)
            day_str = dt_ct.strftime('%Y-%m-%d')
            day_name = dt_ct.strftime('%A')

            if day_str not in daily_all:
                daily_all[day_str] = {
                    'trades': 0, 'wins': 0, 'losses': 0,
                    'gross_profit': 0.0, 'gross_loss': 0.0,
                    'commissions': 0.0, 'net_pnl': 0.0,
                    'day_name': day_name
                }
            if day_str not in daily_sym:
                daily_sym[day_str] = {'pnl': 0.0}

            if last_day != day_str and dt_ct.hour >= 17:
                vwap_calc.reset()
                session_candles.clear()
                last_day = day_str
                sig = None

            # Entrada en la apertura de la vela siguiente a la vela que tocó con mecha
            if sig is not None and pos is None:
                is_ok, _, _ = guardian.evaluate_market_status(dt_ct)
                if is_ok and daily_sym[day_str]['pnl'] > -1500.0:
                    entry = round(round(b['open'] / sp['tick']) * sp['tick'], 4)
                    if sig['action'] == 'BUY':
                        sl = round(round((sig['vwap'] - 5 * sp['tick']) / sp['tick']) * sp['tick'], 4)
                        tp = round(round((entry + sig['tp_size']) / sp['tick']) * sp['tick'], 4)
                        risk_t = int(round((entry - sl) / sp['tick']))
                        rew_t = int(round((tp - entry) / sp['tick']))
                    else:
                        sl = round(round((sig['vwap'] + 5 * sp['tick']) / sp['tick']) * sp['tick'], 4)
                        tp = round(round((entry - sig['tp_size']) / sp['tick']) * sp['tick'], 4)
                        risk_t = int(round((sl - entry) / sp['tick']))
                        rew_t = int(round((entry - tp) / sp['tick']))

                    if 0 < risk_t <= sp['max_risk'] and rew_t > 0:
                        pos = {
                            'action': sig['action'], 'entry': entry, 'sl': sl, 'tp': tp,
                            'risk_t': risk_t, 'rew_t': rew_t, 'day': day_str
                        }
                sig = None

            vwap, up, low = vwap_calc.update_candle(b['high'], b['low'], b['close'], b['volume'])
            c = Candle15M(b['timestamp'], '', b['open'], b['high'], b['low'], b['close'], b['volume'], vwap, up, low, True)
            session_candles.append(c)

            # Gestión de la posición abierta
            if pos is not None:
                if guardian.should_flatten_immediately(dt_ct):
                    exit_p = c.close
                    ticks = (exit_p - pos['entry']) / sp['tick'] if pos['action'] == 'BUY' else (pos['entry'] - exit_p) / sp['tick']
                    gross = ticks * sp['val']
                    net = gross - sp['comm']
                    daily_all[pos['day']]['trades'] += 1
                    daily_all[pos['day']]['commissions'] += sp['comm']
                    daily_all[pos['day']]['net_pnl'] += net
                    daily_sym[pos['day']]['pnl'] += net
                    if gross > 0:
                        daily_all[pos['day']]['gross_profit'] += gross
                        daily_all[pos['day']]['wins'] += 1
                    else:
                        daily_all[pos['day']]['gross_loss'] += abs(gross)
                        daily_all[pos['day']]['losses'] += 1
                    pos = None
                else:
                    hit_tp, hit_sl = False, False
                    if pos['action'] == 'BUY':
                        if c.high >= pos['tp']: hit_tp = True
                        elif c.low <= pos['sl']: hit_sl = True
                    else:
                        if c.low <= pos['tp']: hit_tp = True
                        elif c.high >= pos['sl']: hit_sl = True

                    if hit_tp:
                        gross = pos['rew_t'] * sp['val']
                        net = gross - sp['comm']
                        daily_all[pos['day']]['trades'] += 1
                        daily_all[pos['day']]['wins'] += 1
                        daily_all[pos['day']]['gross_profit'] += gross
                        daily_all[pos['day']]['commissions'] += sp['comm']
                        daily_all[pos['day']]['net_pnl'] += net
                        daily_sym[pos['day']]['pnl'] += net
                        pos = None
                    elif hit_sl:
                        gross = -pos['risk_t'] * sp['val']
                        net = gross - sp['comm']
                        daily_all[pos['day']]['trades'] += 1
                        daily_all[pos['day']]['losses'] += 1
                        daily_all[pos['day']]['gross_loss'] += abs(gross)
                        daily_all[pos['day']]['commissions'] += sp['comm']
                        daily_all[pos['day']]['net_pnl'] += net
                        daily_sym[pos['day']]['pnl'] += net
                        pos = None

            # Detección de señal en vela que tocó con mecha
            if pos is None and len(session_candles) >= 2:
                is_ok, _, _ = guardian.evaluate_market_status(dt_ct)
                if is_ok:
                    cp = session_candles[-2]
                    cs = session_candles[-1]
                    if cp.close >= cp.vwap and cs.low <= cs.vwap and cs.close > cs.vwap:
                        sig = {'action': 'BUY', 'vwap': cs.vwap, 'tp_size': (cs.upper_band_1 - cs.vwap) * 0.5}
                    elif cp.close <= cp.vwap and cs.high >= cs.vwap and cs.close < cs.vwap:
                        sig = {'action': 'SELL', 'vwap': cs.vwap, 'tp_size': (cs.vwap - cs.lower_band_1) * 0.5}

    # Imprimir tabla día por día
    sorted_days = sorted([d for d, v in daily_all.items() if v['trades'] > 0])
    
    print("\n" + "="*105)
    print(" INFORME DÍA POR DÍA: 3 MESES COMPLETOS CON 2 CONTRATOS (TP 50% DESDE APERTURA DE VELA)")
    print("="*105)
    print(f"| {'Fecha':<10} | {'Día':<10} | {'Trades':^6} | {'W/L':^7} | {'Ganancia Bruta':^15} | {'Pérdida Bruta':^15} | {'Comisiones':^11} | {'Neto del Día':^14} | {'Balance Acum':^14} |")
    print("|" + "-"*12 + "|" + "-"*12 + "|" + "-"*8 + "|" + "-"*9 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*13 + "|" + "-"*16 + "|" + "-"*16 + "|")

    cum_balance = 0.0
    tot_trades = 0
    tot_wins = 0
    tot_losses = 0
    tot_gp = 0.0
    tot_gl = 0.0
    tot_comm = 0.0

    spanish_days = {
        'Monday': 'Lunes', 'Tuesday': 'Martes', 'Wednesday': 'Miércoles',
        'Thursday': 'Jueves', 'Friday': 'Viernes', 'Saturday': 'Sábado', 'Sunday': 'Domingo'
    }

    winning_days = 0
    losing_days = 0

    for d in sorted_days:
        data = daily_all[d]
        cum_balance += data['net_pnl']
        tot_trades += data['trades']
        tot_wins += data['wins']
        tot_losses += data['losses']
        tot_gp += data['gross_profit']
        tot_gl += data['gross_loss']
        tot_comm += data['commissions']

        if data['net_pnl'] >= 0:
            winning_days += 1
        else:
            losing_days += 1

        d_name_es = spanish_days.get(data['day_name'], data['day_name'])
        wl_str = f"{data['wins']}/{data['losses']}"
        sign_net = "+" if data['net_pnl'] >= 0 else "-"
        abs_net = abs(data['net_pnl'])
        sign_cum = "+" if cum_balance >= 0 else "-"
        abs_cum = abs(cum_balance)

        print(f"| {d:<10} | {d_name_es:<10} | {data['trades']:^6} | {wl_str:^7} | $ {data['gross_profit']:>11.2f}   | -$ {data['gross_loss']:>11.2f}   | -$ {data['commissions']:>7.2f} | {sign_net}$ {abs_net:>10.2f}   | {sign_cum}$ {abs_cum:>10.2f}   |")

    print("|" + "-"*12 + "|" + "-"*12 + "|" + "-"*8 + "|" + "-"*9 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*13 + "|" + "-"*16 + "|" + "-"*16 + "|")
    print(f"| {'TOTALES':<10} | {'48 Días':<10} | {tot_trades:^6} | {tot_wins}/{tot_losses} | $ {tot_gp:>11.2f}   | -$ {tot_gl:>11.2f}   | -$ {tot_comm:>7.2f} | +$ {cum_balance:>10.2f}   | +$ {cum_balance:>10.2f}   |")
    print("="*105)
    print(f"\nResumen Estadístico:")
    print(f"• Días Positivos: {winning_days} ({winning_days/len(sorted_days)*100:.1f}%)")
    print(f"• Días Negativos: {losing_days} ({losing_days/len(sorted_days)*100:.1f}%)")
    print(f"• Ganancia Bruta Total: ${tot_gp:.2f}")
    print(f"• Pérdida Bruta Total: -${tot_gl:.2f}")
    print(f"• Comisiones Totales Topstep: -${tot_comm:.2f}")
    print(f"• Beneficio Neto Final: +${cum_balance:.2f} USD")

if __name__ == '__main__':
    run_daily_breakdown()
