"""
Motor Principal de Trading Multi-Símbolo (VWAP M30 Engine)
Idéntico a la especificación cuantitativa oficial del backtest de 3 meses:
- Velas oficiales de 30 Minutos (M30).
- Anclaje y reseteo diario del Session VWAP a las 17:00 CT.
- Rebote de mecha en VWAP con confirmación previa.
- Entrada en la apertura (Open) de la vela siguiente con 2 contratos fijos.
- Take Profit al 50% de la distancia VWAP-Banda proyectada desde la apertura.
- Stop Loss blindado a 5 ticks del VWAP.
- Cierre forzoso de seguridad antes del corte de Topstep (15:05 CT).
- Transmisión en tiempo real de velas reales y curvas de VWAP al Dashboard Web.
"""

import asyncio
import json
import logging
import os
import time
import zoneinfo
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable

from config import (
    AVAILABLE_SYMBOLS, TOPSTEPX_CONTRACT_IDS, SymbolMetadata, StrategyConfig, 
    ScheduleConfig, AccountConfig, SIMULATION_MODE, TOPSTEPX_USERNAME, 
    TOPSTEPX_API_KEY, TOPSTEPX_BASE_URL
)
from core.vwap_bands import PrecisionVWAP, CandleBar
from core.strategy import VWAPWickBounceStrategy, TradeSignal
from core.schedule_guardian import ScheduleGuardian
from core.risk_guardian import RiskGuardianMulti
from adapters.topstepx_client import TopstepXClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def get_cme_session_start_time(now_ct: datetime) -> datetime:
    """
    Retorna el datetime exacto de inicio de la sesión CME actual (anclada a las 17:00 CT).
    Si la hora actual es >= 17:00 CT, la sesión inició hoy a las 17:00 CT.
    Si la hora actual es < 17:00 CT, la sesión inició ayer a las 17:00 CT.
    """
    if now_ct.hour >= 17:
        d = now_ct.date()
    else:
        d = (now_ct - timedelta(days=1)).date()
    return datetime(d.year, d.month, d.day, 17, 0, 0, tzinfo=now_ct.tzinfo)

def get_cme_session_key(dt_ct: datetime) -> str:
    """
    Calcula la fecha identificadora de la sesión CME.
    La sesión inicia a las 17:00 CT. Toda vela entre las 17:00 CT del día D
    y las 16:00 CT del día D+1 pertenece a la misma sesión diaria.
    """
    if dt_ct.hour >= 17:
        return dt_ct.strftime("%Y-%m-%d")
    else:
        return (dt_ct - timedelta(days=1)).strftime("%Y-%m-%d")

class SymbolWorker:
    """Gestiona el cálculo de velas M30, Session VWAP y señales de un símbolo."""
    def __init__(self, meta: SymbolMetadata, strategy_cfg: StrategyConfig):
        self.meta = meta
        self.strategy_cfg = strategy_cfg
        self.vwap_engine = PrecisionVWAP(std_multiplier=strategy_cfg.band_std_multiplier)
        self.strategy = VWAPWickBounceStrategy(meta, strategy_cfg)
        self.candles: List[CandleBar] = []
        self.last_signal: Optional[TradeSignal] = None
        self.pending_signal: Optional[dict] = None
        self.current_price: float = 0.0
        self.last_session_key: Optional[str] = None

    def process_m30_bar(self, bar: dict, dt_ct: datetime):
        """
        Procesa una vela M30 completa con la regla exacta de anclaje de sesión CME (17:00 CT).
        """
        session_key = get_cme_session_key(dt_ct)

        # 1. Reseteo oficial de sesión al detectar una nueva sesión CME (17:00 CT)
        if self.last_session_key is not None and session_key != self.last_session_key:
            self.vwap_engine.reset()
            self.candles.clear()
            self.pending_signal = None
        self.last_session_key = session_key

        # 2. Actualizar VWAP y Bandas con precio típico y volumen real
        vwap, upper, lower = self.vwap_engine.update_candle(
            bar["high"], bar["low"], bar["close"], bar["volume"]
        )

        candle = CandleBar(
            timestamp=bar["timestamp"],
            datetime_str=dt_ct.strftime("%H:%M CT"),
            open=bar["open"],
            high=bar["high"],
            low=bar["low"],
            close=bar["close"],
            volume=bar["volume"],
            vwap=vwap,
            upper_band_1=upper,
            lower_band_1=lower,
            is_closed=True
        )

        self.candles.append(candle)
        self.current_price = candle.close

        # Mantener las últimas 100 velas M30 en memoria
        if len(self.candles) > 100:
            self.candles.pop(0)

        # 3. Detección de Señal (Toque de mecha y cierre confirmando rebote)
        if len(self.candles) >= 2:
            cp = self.candles[-2]
            cs = self.candles[-1]

            # SEÑAL BUY: Vela previa confirma lado alcista, vela actual toca VWAP y cierra por encima
            if cp.close >= cp.vwap and cs.low <= cs.vwap and cs.close > cs.vwap:
                tp_size = (cs.upper_band_1 - cs.vwap) * 0.5
                self.pending_signal = {
                    "symbol": self.meta.symbol,
                    "action": "BUY",
                    "vwap": cs.vwap,
                    "tp_size": tp_size,
                    "signal_time": dt_ct.strftime("%H:%M CT")
                }

            # SEÑAL SELL: Vela previa confirma lado bajista, vela actual toca VWAP y cierra por debajo
            elif cp.close <= cp.vwap and cs.high >= cs.vwap and cs.close < cs.vwap:
                tp_size = (cs.vwap - cs.lower_band_1) * 0.5
                self.pending_signal = {
                    "symbol": self.meta.symbol,
                    "action": "SELL",
                    "vwap": cs.vwap,
                    "tp_size": tp_size,
                    "signal_time": dt_ct.strftime("%H:%M CT")
                }

class MasterTradingEngine:
    def __init__(self, strategy_cfg: StrategyConfig = StrategyConfig(), 
                 schedule_cfg: ScheduleConfig = ScheduleConfig(), 
                 account_cfg: AccountConfig = AccountConfig()):
        self.strategy_cfg = strategy_cfg
        self.schedule_guardian = ScheduleGuardian(schedule_cfg)
        self.risk_guardian = RiskGuardianMulti(account_cfg)
        self.api_client = TopstepXClient(
            TOPSTEPX_USERNAME, 
            TOPSTEPX_API_KEY, 
            TOPSTEPX_BASE_URL,
            target_account_id=account_cfg.account_id
        )
        self.tz_ct = zoneinfo.ZoneInfo("America/Chicago")

        # Inicializar trabajadores para cada símbolo activo (ES, NQ, CL, YM)
        self.workers: Dict[str, SymbolWorker] = {}
        for sym in self.strategy_cfg.active_symbols:
            if sym in AVAILABLE_SYMBOLS:
                self.workers[sym] = SymbolWorker(AVAILABLE_SYMBOLS[sym], self.strategy_cfg)

        self.is_running: bool = False
        self.ui_broadcast_callback: Optional[Callable] = None
        self.logs_history: List[str] = []
        
        # Cargar de inmediato los datos históricos oficiales y anclar el VWAP
        self.load_historical_cme_data()

    def log(self, message: str, level: str = "INFO"):
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}"
        self.logs_history.insert(0, entry)
        if len(self.logs_history) > 200:
            self.logs_history.pop()
        logging.info(message)

    def load_historical_cme_data(self):
        """
        Carga EXCLUSIVAMENTE las velas de la sesión CME actual (ancladas a las 17:00 CT).
        No procesa meses anteriores: 100% enfocado en la sesión activa en tiempo real.
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        now_dt = datetime.now(timezone.utc)
        now_ct = now_dt.astimezone(self.tz_ct)
        session_start_ct = get_cme_session_start_time(now_ct)

        for sym, worker in self.workers.items():
            worker.vwap_engine.reset()
            worker.candles.clear()
            worker.last_session_key = get_cme_session_key(now_ct)

            cache_path = os.path.join(base_dir, f"cache_m30_3months_{sym}.json")
            if not os.path.exists(cache_path):
                cache_path = os.path.join(base_dir, f"cache_m30_30d_{sym}.json")

            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        raw_bars = json.load(f)

                    # Filtrar únicamente las barras pertenecientes a la sesión actual
                    session_bars = []
                    for b in raw_bars:
                        iso_str = b.get("t") or b.get("datetime_iso")
                        if not iso_str: continue
                        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
                        dt_ct = dt.astimezone(self.tz_ct)
                        if dt_ct >= session_start_ct:
                            session_bars.append({
                                "timestamp": int(dt.timestamp() * 1000),
                                "dt_ct": dt_ct,
                                "open": float(b.get("o") or b.get("open")),
                                "high": float(b.get("h") or b.get("high")),
                                "low": float(b.get("l") or b.get("low")),
                                "close": float(b.get("c") or b.get("close")),
                                "volume": float(b.get("v") or b.get("volume") or 1000.0)
                            })
                    session_bars.sort(key=lambda x: x["timestamp"])

                    for b in session_bars:
                        worker.process_m30_bar(b, b["dt_ct"])
                    worker.pending_signal = None

                    snap = worker.vwap_engine.get_snapshot()
                    self.log(
                        f"📊 {sym}: {len(worker.candles)} velas de sesión cargadas ({session_start_ct.strftime('%H:%M CT')} a actual). "
                        f"Spot: {worker.current_price:.2f} | VWAP Sesión: {snap['vwap']:.2f} "
                        f"| +1σ: {snap['upper_band_1']:.2f} | -1σ: {snap['lower_band_1']:.2f}"
                    )
                except Exception as e:
                    self.log(f"Error al cargar sesión para {sym}: {e}", "ERROR")

    async def _update_single_symbol_live_bars(self, sym: str):
        """Consulta las barras recientes de UN SOLO símbolo a TopstepX con estricto respeto anti-429."""
        if time.time() < self.api_client.rate_limited_until:
            return

        worker = self.workers.get(sym)
        contract_id = TOPSTEPX_CONTRACT_IDS.get(sym)
        if not worker or not contract_id:
            return

        if not self.api_client.is_authenticated:
            auth_ok = await self.api_client.authenticate()
            if not auth_ok:
                return

        now_dt = datetime.now(timezone.utc)
        now_ct = now_dt.astimezone(self.tz_ct)
        session_start_ct = get_cme_session_start_time(now_ct)
        start_utc = session_start_ct.astimezone(timezone.utc)

        try:
            raw_bars = await self.api_client.retrieve_bars(
                contract_id=contract_id,
                unit=2,
                unit_number=30,
                limit=48, # Máximo 48 velas M30 por sesión diaria CME (24h)
                start_time=start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time=now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                include_partial_bar=True
            )
            if raw_bars:
                sorted_bars = []
                for b in raw_bars:
                    iso_str = b.get("t") or b.get("datetime_iso")
                    if not iso_str: continue
                    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
                    dt_ct = dt.astimezone(self.tz_ct)
                    # Solo procesar barras de la sesión CME actual
                    if dt_ct >= session_start_ct:
                        sorted_bars.append({
                            "timestamp": int(dt.timestamp() * 1000),
                            "dt_ct": dt_ct,
                            "open": float(b.get("o") or b.get("open")),
                            "high": float(b.get("h") or b.get("high")),
                            "low": float(b.get("l") or b.get("low")),
                            "close": float(b.get("c") or b.get("close")),
                            "volume": float(b.get("v") or b.get("volume") or 1000.0)
                        })
                sorted_bars.sort(key=lambda x: x["timestamp"])

                for bar_data in sorted_bars:
                    # Si no hay velas en memoria o la barra es estrictamente más nueva que la última
                    if not worker.candles or bar_data["timestamp"] > worker.candles[-1].timestamp:
                        worker.process_m30_bar(bar_data, bar_data["dt_ct"])
                        self.log(f"📥 Barra M30 ({sym}) cargada: {bar_data['dt_ct'].strftime('%H:%M CT')} | C={bar_data['close']} | VWAP={worker.candles[-1].vwap:.2f}")
                    elif worker.candles and bar_data["timestamp"] == worker.candles[-1].timestamp:
                        # Actualizar en tiempo real la vela en formación y el precio Spot
                        worker.candles[-1].high = max(worker.candles[-1].high, bar_data["high"])
                        worker.candles[-1].low = min(worker.candles[-1].low, bar_data["low"])
                        worker.candles[-1].close = bar_data["close"]
                        worker.current_price = bar_data["close"]

        except Exception as e:
            logging.debug(f"Live bar fetch {sym}: {e}")

    async def update_live_bars_from_topstepx(self):
        """Consulta barras de todos los símbolos secuencialmente con pausas protectoras."""
        for sym in self.strategy_cfg.active_symbols:
            if sym in self.workers:
                await self._update_single_symbol_live_bars(sym)
                await asyncio.sleep(3.0)

    async def run_loop(self):
        """Bucle principal asíncrono con sondeo Round-Robin anti-429."""
        self.is_running = True
        self.log(f"🚀 Motor Cuantitativo VWAP M30 Inicializado (Cuenta PRÁCTICA TopstepX {self.risk_guardian.acc_cfg.account_name}).")
        self.load_historical_cme_data()

        # Autenticación inmediata
        await self.api_client.authenticate()
        await self.api_client.get_active_account_id()

        # Sondeo inicial inmediato de los símbolos al arrancar para tener la sesión de hoy lista
        symbol_list = [s for s in self.strategy_cfg.active_symbols if s in self.workers]
        self.log("📡 Sincronizando barras de la sesión actual en vivo con TopstepX API...")
        for sym in symbol_list:
            await self._update_single_symbol_live_bars(sym)
            await asyncio.sleep(2.0)

        poll_index = 0
        tick_counter = 0

        while self.is_running:
            try:
                now_dt = datetime.now(timezone.utc)
                now_ct = now_dt.astimezone(self.tz_ct)
                is_allowed, status_msg, schedule_info = self.schedule_guardian.evaluate_market_status(now_dt)

                # 1. Comprobar si corresponde Cierre Forzoso de Topstep (15:05 CT)
                if self.schedule_guardian.should_flatten_immediately(now_dt):
                    if len(self.risk_guardian.open_positions) > 0:
                        self.log("🚨 CIERRE FORZOSO DE TOPSTEP (15:05 CT) ACTIVADO. Liquidando posiciones...", "WARNING")
                        for sym in list(self.risk_guardian.open_positions.keys()):
                            await self.flatten_symbol(sym, reason="Cierre forzoso de sesión Topstep")

                # 2. Sondeo Round-Robin: Consulta UN SOLO símbolo cada 10 segundos (4 ticks x 2.5s)
                tick_counter += 1
                if tick_counter % 4 == 0 and symbol_list:
                    target_sym = symbol_list[poll_index]
                    poll_index = (poll_index + 1) % len(symbol_list)
                    await self._update_single_symbol_live_bars(target_sym)

                # 3. Evaluar señales de apertura de vela en cada símbolo
                if is_allowed:
                    await self._evaluate_all_symbols(now_ct)

                # 4. Gestionar posiciones abiertas (evaluar TP y SL de forma idéntica al backtest)
                await self._manage_open_positions()

                # 5. Notificar a la interfaz web vía WebSocket
                if self.ui_broadcast_callback:
                    await self.ui_broadcast_callback()

                await asyncio.sleep(2.5)

            except Exception as e:
                self.log(f"Error en bucle de trading: {str(e)}", "ERROR")
                await asyncio.sleep(5)

    async def _evaluate_all_symbols(self, now_ct: datetime):
        """Evalúa las señales pendientes para entrar en la APERTURA de la vela operativa."""
        for sym, worker in self.workers.items():
            if worker.pending_signal is not None and sym not in self.risk_guardian.open_positions:
                sig = worker.pending_signal
                worker.pending_signal = None

                meta = worker.meta
                entry = worker.current_price

                if sig["action"] == "BUY":
                    sl = round(round((sig["vwap"] - (5 * meta.tick_size)) / meta.tick_size) * meta.tick_size, 4)
                    tp = round(round((entry + sig["tp_size"]) / meta.tick_size) * meta.tick_size, 4)
                    risk_t = int(round((entry - sl) / meta.tick_size))
                    rew_t = int(round((tp - entry) / meta.tick_size))
                else:
                    sl = round(round((sig["vwap"] + (5 * meta.tick_size)) / meta.tick_size) * meta.tick_size, 4)
                    tp = round(round((entry - sig["tp_size"]) / meta.tick_size) * meta.tick_size, 4)
                    risk_t = int(round((sl - entry) / meta.tick_size))
                    rew_t = int(round((entry - tp) / meta.tick_size))

                # Filtro de riesgo institucional (exacto al backtest)
                if 0 < risk_t <= meta.max_risk_ticks and rew_t > 0:
                    rr = round(rew_t / risk_t, 2)
                    trade_sig = TradeSignal(
                        symbol=sym,
                        action=sig["action"],
                        entry_price=entry,
                        sl_price=sl,
                        tp_price=tp,
                        vwap_at_entry=round(sig["vwap"], 4),
                        band_next_at_entry=0.0,
                        risk_ticks=risk_t,
                        reward_ticks=rew_t,
                        risk_reward_ratio=rr,
                        timestamp=int(now_ct.timestamp() * 1000),
                        datetime_str=now_ct.strftime("%H:%M CT"),
                        reason=f"Rebote con mecha en VWAP ({sig['vwap']:.2f}) confirmado."
                    )
                    await self._execute_signal(trade_sig)

    async def _manage_open_positions(self):
        """Monitorea las posiciones abiertas para detectar si alcanzaron TP o SL, exacto al backtest."""
        for sym, pos in list(self.risk_guardian.open_positions.items()):
            worker = self.workers.get(sym)
            if not worker:
                continue
            curr_p = worker.current_price
            meta = AVAILABLE_SYMBOLS[sym]

            hit_tp = False
            hit_sl = False

            if pos["action"] == "BUY":
                if curr_p >= pos["tp"]:
                    hit_tp = True
                elif curr_p <= pos["sl"]:
                    hit_sl = True
            else: # SELL
                if curr_p <= pos["tp"]:
                    hit_tp = True
                elif curr_p >= pos["sl"]:
                    hit_sl = True

            if hit_tp:
                ticks = round(abs(pos["tp"] - pos["entry_price"]) / meta.tick_size)
                gross = ticks * meta.tick_value * pos["size"]
                self.risk_guardian.record_close_position(sym, pos["tp"], gross)
                self.log(f"🎉 TAKE PROFIT ALCANZADO en {sym} @ {pos['tp']:.2f}! Ganancia: +${gross:.2f}", "INFO")
            elif hit_sl:
                ticks = round(abs(pos["entry_price"] - pos["sl"]) / meta.tick_size)
                loss = -ticks * meta.tick_value * pos["size"]
                self.risk_guardian.record_close_position(sym, pos["sl"], loss)
                self.log(f"🛡️ STOP LOSS EJECUTADO en {sym} @ {pos['sl']:.2f} (5 ticks VWAP). Pérdida: ${loss:.2f}", "WARNING")

    async def _execute_signal(self, signal: TradeSignal):
        """Ejecuta una señal autorizada enviando la orden Bracket con 2 contratos a TopstepX."""
        contracts = self.strategy_cfg.contracts_per_trade

        self.log(
            f"🎯 DISPARO ORDEN BRACKET: {signal.action} {contracts}x {signal.symbol} @ {signal.entry_price} "
            f"| SL (5 ticks VWAP): {signal.sl_price} ({signal.risk_ticks} ticks) "
            f"| TP (50% Banda): {signal.tp_price} ({signal.reward_ticks} ticks) | R:R 1:{signal.risk_reward_ratio}"
        )

        contract_id = TOPSTEPX_CONTRACT_IDS.get(signal.symbol, signal.symbol)
        order_res = await self.api_client.send_bracket_order(
            contract_id=contract_id,
            action=signal.action,
            quantity=contracts,
            limit_price=signal.entry_price,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price,
            symbol=signal.symbol,
            risk_ticks=signal.risk_ticks,
            reward_ticks=signal.reward_ticks
        )

        order_id = order_res.get("orderId", f"TX_{signal.action}_{int(time.time())}")
        if order_res.get("status") == "SUBMITTED":
            self.risk_guardian.record_open_position(
                symbol=signal.symbol,
                action=signal.action,
                size=contracts,
                entry_price=signal.entry_price,
                sl=signal.sl_price,
                tp=signal.tp_price,
                order_id=order_id
            )
            self.log(f"✅ Posición abierta en {signal.symbol}: {signal.action} {contracts} contratos @ {signal.entry_price} (Order ID: {order_id})")
        else:
            self.log(f"⚠️ Orden TopstepX no completada: {order_res.get('errorMessage', 'Error de envío')}", "WARNING")

    async def flatten_symbol(self, symbol: str, reason: str = "Manual"):
        """Liquida una posición abierta inmediatamente."""
        if symbol in self.risk_guardian.open_positions:
            pos = self.risk_guardian.open_positions[symbol]
            worker = self.workers.get(symbol)
            exit_price = worker.current_price if worker else pos["entry_price"]
            
            meta = AVAILABLE_SYMBOLS[symbol]
            ticks = (exit_price - pos["entry_price"]) / meta.tick_size
            if pos["action"] == "SELL":
                ticks = -ticks
            pnl = ticks * meta.tick_value * pos["size"]

            self.risk_guardian.record_close_position(symbol, exit_price, pnl)
            contract_id = TOPSTEPX_CONTRACT_IDS.get(symbol, symbol)
            await self.api_client.flatten_all_positions(contract_id)
            self.log(f"🛑 Posición en {symbol} cerrada ({reason}). PnL: ${pnl:.2f}")

    def get_full_state_snapshot(self) -> dict:
        """Devuelve el estado completo del sistema con velas reales M30 y curvas de VWAP."""
        now_dt = datetime.now(timezone.utc)
        allowed, status_msg, sched_details = self.schedule_guardian.evaluate_market_status(now_dt)

        symbols_data = {}
        for sym, worker in self.workers.items():
            symbols_data[sym] = {
                "symbol": sym,
                "name": worker.meta.name,
                "category": worker.meta.category,
                "tick_size": worker.meta.tick_size,
                "current_price": worker.current_price,
                "vwap_data": worker.vwap_engine.get_snapshot(),
                "last_candle": worker.candles[-1].__dict__ if worker.candles else None,
                "candles": [
                    {
                        "time": c.datetime_str,
                        "timestamp": c.timestamp,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                        "vwap": round(c.vwap, 2),
                        "upper": round(c.upper_band_1, 2),
                        "lower": round(c.lower_band_1, 2)
                    }
                    for c in worker.candles[-45:] # Últimas 45 velas M30 para el gráfico interactivo
                ],
                "candles_count": len(worker.candles)
            }

        return {
            "schedule": {
                "is_trading_allowed": allowed,
                "status_message": status_msg,
                "details": sched_details
            },
            "risk": self.risk_guardian.get_summary(),
            "open_positions": list(self.risk_guardian.open_positions.values()),
            "closed_trades": self.risk_guardian.closed_trades_history[:20],
            "symbols": symbols_data,
            "active_symbols": self.strategy_cfg.active_symbols,
            "server_time": datetime.now(self.tz_ct).strftime("%Y-%m-%d %H:%M:%S CT"),
            "logs": self.logs_history[:40]
        }
