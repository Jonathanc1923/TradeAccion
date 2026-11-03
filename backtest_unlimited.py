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

def run_unlimited():
    guardian = ScheduleGuardian()
    tz = zoneinfo.ZoneInfo('America/Chicago')
    daily_all = {}
    symbol_summaries = {}

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
        session_candles = []
        pos = None
        sig = None
        last_day = None
        sym_trades = []

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

            if last_day != day_str and dt_ct.hour >= 17:
                vwap_calc.reset()
                session_candles.clear()
                last_day = day_str
                sig = None

            # Entrada en la apertura de la vela siguiente al toque de mecha (SIN LÍMITE DE PÉRDIDAS NI GANANCIAS)
            if sig is not None and pos is None:
                is_ok, _, _ = guardian.evaluate_market_status(dt_ct)
                # SOLO control de horario de mercado (no límite de pérdida diaria ni de ganancia)
                if is_ok:
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
                            'symbol': sym, 'action': sig['action'], 'entry': entry, 'sl': sl, 'tp': tp,
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
                    if gross > 0:
                        daily_all[pos['day']]['gross_profit'] += gross
                        daily_all[pos['day']]['wins'] += 1
                    else:
                        daily_all[pos['day']]['gross_loss'] += abs(gross)
                        daily_all[pos['day']]['losses'] += 1
                    sym_trades.append({'net': net, 'gross': gross, 'comm': sp['comm']})
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
                        sym_trades.append({'net': net, 'gross': gross, 'comm': sp['comm']})
                        pos = None
                    elif hit_sl:
                        gross = -pos['risk_t'] * sp['val']
                        net = gross - sp['comm']
                        daily_all[pos['day']]['trades'] += 1
                        daily_all[pos['day']]['losses'] += 1
                        daily_all[pos['day']]['gross_loss'] += abs(gross)
                        daily_all[pos['day']]['commissions'] += sp['comm']
                        daily_all[pos['day']]['net_pnl'] += net
                        sym_trades.append({'net': net, 'gross': gross, 'comm': sp['comm']})
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

        # Resumen por símbolo
        tot_t = len(sym_trades)
        wins = sum(1 for t in sym_trades if t['net'] > 0)
        losses = sum(1 for t in sym_trades if t['net'] <= 0)
        gp = sum(t['gross'] for t in sym_trades if t['gross'] > 0)
        gl = sum(abs(t['gross']) for t in sym_trades if t['gross'] <= 0)
        comm = sum(t['comm'] for t in sym_trades)
        net = gp - gl - comm
        wr = (wins / tot_t * 100) if tot_t > 0 else 0
        symbol_summaries[sym] = {
            'name': sp['name'], 'trades': tot_t, 'wins': wins, 'losses': losses,
            'win_rate': wr, 'gross_profit': gp, 'gross_loss': gl, 'commissions': comm,
            'net_pnl': net
        }

    # Imprimir resumen por activo
    print("\n" + "="*105)
    print(" INFORME SIN LÍMITES DIARIOS (TODAS LAS SEÑALES EJECUTADAS CON 2 CONTRATOS)")
    print(" Periodo: 3 Meses (M30) | TP 50% desde Apertura | SL 5 Ticks VWAP | 2 Contratos por Trade")
    print("="*105)
    print(f"| {'Símbolo':<8} | {'Nombre del Activo':<20} | {'Trades':^6} | {'Win Rate':^9} | {'Ganancia Bruta':^15} | {'Pérdida Bruta':^15} | {'Comisiones':^12} | {'NETO FINAL':^14} |")
    print("|" + "-"*10 + "|" + "-"*22 + "|" + "-"*8 + "|" + "-"*9 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*14 + "|" + "-"*16 + "|")

    for sym, s in symbol_summaries.items():
        print(f"| {sym:<8} | {s['name']:<20} | {s['trades']:^6} | {s['win_rate']:>6.1f}  % | $ {s['gross_profit']:>11.2f}   | -$ {s['gross_loss']:>11.2f}   | -$ {s['commissions']:>8.2f}   | $ {s['net_pnl']:>10.2f}   |")

    tot_trades_all = sum(s['trades'] for s in symbol_summaries.values())
    tot_wins_all = sum(s['wins'] for s in symbol_summaries.values())
    tot_losses_all = sum(s['losses'] for s in symbol_summaries.values())
    tot_gp_all = sum(s['gross_profit'] for s in symbol_summaries.values())
    tot_gl_all = sum(s['gross_loss'] for s in symbol_summaries.values())
    tot_comm_all = sum(s['commissions'] for s in symbol_summaries.values())
    tot_net_all = sum(s['net_pnl'] for s in symbol_summaries.values())
    avg_wr_all = (tot_wins_all / tot_trades_all * 100) if tot_trades_all > 0 else 0

    print("|" + "-"*10 + "|" + "-"*22 + "|" + "-"*8 + "|" + "-"*9 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*14 + "|" + "-"*16 + "|")
    print(f"| {'TOTAL':<8} | {'PORTAFOLIO (2 CTRS)':<20} | {tot_trades_all:^6} | {avg_wr_all:>6.1f}  % | $ {tot_gp_all:>11.2f}   | -$ {tot_gl_all:>11.2f}   | -$ {tot_comm_all:>8.2f}   | $ {tot_net_all:>10.2f}   |")
    print("="*105)

    # Imprimir desglose día por día
    sorted_days = sorted([d for d, v in daily_all.items() if v['trades'] > 0])
    print(f"\n| {'Fecha':<10} | {'Día':<10} | {'Trades':^6} | {'W/L':^7} | {'Ganancia Bruta':^15} | {'Pérdida Bruta':^15} | {'Comisiones':^11} | {'Neto del Día':^14} | {'Balance Acum':^14} |")
    print("|" + "-"*12 + "|" + "-"*12 + "|" + "-"*8 + "|" + "-"*9 + "|" + "-"*17 + "|" + "-"*17 + "|" + "-"*13 + "|" + "-"*16 + "|" + "-"*16 + "|")

    cum_balance = 0.0
    spanish_days = {
        'Monday': 'Lunes', 'Tuesday': 'Martes', 'Wednesday': 'Miércoles',
        'Thursday': 'Jueves', 'Friday': 'Viernes', 'Saturday': 'Sábado', 'Sunday': 'Domingo'
    }
    winning_days = 0
    losing_days = 0

    for d in sorted_days:
        data = daily_all[d]
        cum_balance += data['net_pnl']
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
    print(f"| {'TOTALES':<10} | {'48 Días':<10} | {tot_trades_all:^6} | {tot_wins_all}/{tot_losses_all} | $ {tot_gp_all:>11.2f}   | -$ {tot_gl_all:>11.2f}   | -$ {tot_comm_all:>7.2f} | +$ {tot_net_all:>10.2f}   | +$ {tot_net_all:>10.2f}   |")
    print("="*105)
    print(f"\nResumen Estadístico Sin Límites:")
    print(f"• Días Positivos: {winning_days} ({winning_days/len(sorted_days)*100:.1f}%)")
    print(f"• Días Negativos: {losing_days} ({losing_days/len(sorted_days)*100:.1f}%)")
    print(f"• Ganancia Bruta Total: ${tot_gp_all:.2f}")
    print(f"• Pérdida Bruta Total: -${tot_gl_all:.2f}")
    print(f"• Comisiones Totales Topstep: -${tot_comm_all:.2f}")
    print(f"• Beneficio Neto Final: +${tot_net_all:.2f} USD")

if __name__ == '__main__':
    run_unlimited()
