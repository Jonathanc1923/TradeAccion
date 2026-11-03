"""
Motor Principal de Trading Multi-Símbolo (VWAP M15 Engine)
Gestiona:
- Agregación y cálculo de velas M15 y VWAP en paralelo para cada símbolo
- Monitoreo del ScheduleGuardian (3h post-mantenimiento, domingos, cierre Topstep)
- Disparo de señales con Bracket Orders
- Emisión de estado en tiempo real hacia la Web UI vía WebSockets
"""

import asyncio
import time
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Callable
import random

from config import (
    AVAILABLE_SYMBOLS, SymbolMetadata, StrategyConfig, 
    ScheduleConfig, AccountConfig, SIMULATION_MODE
)
from core.vwap_bands import PrecisionVWAP, Candle15M
from core.strategy import VWAPWickBounceStrategy, TradeSignal
from core.schedule_guardian import ScheduleGuardian
from core.risk_guardian import RiskGuardianMulti
from adapters.topstepx_client import TopstepXClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class SymbolWorker:
    """Gestiona el estado, cálculo de velas M15 y estrategia de un símbolo específico."""
    def __init__(self, meta: SymbolMetadata, strategy_cfg: StrategyConfig):
        self.meta = meta
        self.strategy_cfg = strategy_cfg
        self.vwap_engine = PrecisionVWAP(std_multiplier=strategy_cfg.band_std_multiplier)
        self.strategy = VWAPWickBounceStrategy(meta, strategy_cfg)
        self.candles: List[Candle15M] = []
        self.current_forming_candle: Optional[Candle15M] = None
        self.last_signal: Optional[TradeSignal] = None
        self.current_price: float = 0.0

    def reset_session(self):
        """Resetea el VWAP para una nueva sesión de subasta."""
        self.vwap_engine.reset()
        self.candles.clear()
        self.current_forming_candle = None
        self.last_signal = None
        logging.info(f"🔄 VWAP de Sesión reseteado para {self.meta.symbol}")

    def on_candle_closed(self, candle: Candle15M) -> Optional[TradeSignal]:
        """Procesa una vela de 15 minutos cerrada y calcula el nuevo VWAP y bandas."""
        vwap, upper, lower = self.vwap_engine.update_candle(
            candle.high, candle.low, candle.close, candle.volume
        )
        candle.vwap = vwap
        candle.upper_band_1 = upper
        candle.lower_band_1 = lower
        candle.upper_band_2 = self.vwap_engine.upper_band_2
        candle.lower_band_2 = self.vwap_engine.lower_band_2
        candle.is_closed = True

        self.candles.append(candle)
        # Mantener historial en memoria para el gráfico
        if len(self.candles) > 100:
            self.candles.pop(0)

        # Evaluar estrategia de mecha en VWAP
        signal = self.strategy.evaluate_candles(self.candles)
        if signal:
            self.last_signal = signal
        return signal

class MasterTradingEngine:
    def __init__(self, strategy_cfg: StrategyConfig = StrategyConfig(), 
                 schedule_cfg: ScheduleConfig = ScheduleConfig(), 
                 account_cfg: AccountConfig = AccountConfig()):
        self.strategy_cfg = strategy_cfg
        self.schedule_guardian = ScheduleGuardian(schedule_cfg)
        self.risk_guardian = RiskGuardianMulti(account_cfg)
        from config import TOPSTEPX_USERNAME, TOPSTEPX_API_KEY, TOPSTEPX_BASE_URL
        self.api_client = TopstepXClient(TOPSTEPX_USERNAME, TOPSTEPX_API_KEY, TOPSTEPX_BASE_URL)
        
        # Trabajadores por cada símbolo activo
        self.workers: Dict[str, SymbolWorker] = {}
        for sym in self.strategy_cfg.active_symbols:
            if sym in AVAILABLE_SYMBOLS:
                self.workers[sym] = SymbolWorker(AVAILABLE_SYMBOLS[sym], self.strategy_cfg)

        self.is_running: bool = False
        self.ui_broadcast_callback: Optional[Callable] = None
        self.logs_history: List[str] = []

    def log(self, message: str, level: str = "INFO"):
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}"
        self.logs_history.insert(0, entry)
        if len(self.logs_history) > 200:
            self.logs_history.pop()
        logging.info(message)

    def initialize_with_sample_market_data(self):
        """Genera datos sintéticos iniciales de alta calidad para probar de inmediato en el servidor."""
        base_prices = {
            "MES": 5850.00, "ES": 5850.00,
            "MNQ": 20400.00, "NQ": 20400.00,
            "MYM": 43200.00, "YM": 43200.00,
            "M2K": 2240.00, "RTY": 2240.00,
            "MCL": 71.50, "CL": 71.50,
            "MGC": 2740.0, "GC": 2740.0
        }

        now_ms = int(time.time() * 1000) - (15 * 60 * 1000 * 10)
        for sym, worker in self.workers.items():
            price = base_prices.get(sym, 1000.0)
            worker.current_price = price
            # Crear 8 velas de 15 minutos históricas
            for i in range(8):
                c_time = now_ms + (i * 15 * 60 * 1000)
                dt_str = datetime.fromtimestamp(c_time / 1000).strftime("%H:%M")
                
                # Variación controlada
                delta_p = random.uniform(-2.5, 2.5) * worker.meta.tick_size * 4
                o = price
                c = price + delta_p
                h = max(o, c) + (random.uniform(1, 4) * worker.meta.tick_size)
                l = min(o, c) - (random.uniform(1, 4) * worker.meta.tick_size)
                vol = random.randint(2500, 8000)

                candle = Candle15M(
                    timestamp=c_time,
                    datetime_str=dt_str,
                    open=round(o, 4),
                    high=round(h, 4),
                    low=round(l, 4),
                    close=round(c, 4),
                    volume=vol
                )
                worker.on_candle_closed(candle)
                price = c

    async def run_loop(self):
        """Bucle principal asíncrono para servidor."""
        self.is_running = True
        self.log("🚀 Motor VWAP M15 para Servidor Inicializado.")
        self.initialize_with_sample_market_data()

        while self.is_running:
            try:
                now_dt = datetime.now(timezone.utc)
                is_allowed, status_msg, schedule_info = self.schedule_guardian.evaluate_market_status(now_dt)

                # 1. Comprobar si corresponde Cierre Forzoso de Topstep (15:05 CT)
                if self.schedule_guardian.should_flatten_immediately(now_dt):
                    if len(self.risk_guardian.open_positions) > 0:
                        self.log("🚨 CIERRE FORZOSO DE TOPSTEP (15:05 CT) ACTIVADO. Liquidando posiciones...", "WARNING")
                        for sym in list(self.risk_guardian.open_positions.keys()):
                            await self.flatten_symbol(sym, reason="Cierre forzoso de sesión Topstep")

                # 2. Si la operativa está habilitada por horario, evaluar símbolos
                if is_allowed and not self.risk_guardian.is_locked_out:
                    await self._evaluate_all_symbols()

                # 3. Notificar a la interfaz web si hay clientes WebSocket conectados
                if self.ui_broadcast_callback:
                    await self.ui_broadcast_callback()

                # Pausa de 3 segundos entre ciclos de verificación
                await asyncio.sleep(3)

            except Exception as e:
                self.log(f"Error en bucle de trading: {str(e)}", "ERROR")
                await asyncio.sleep(5)

    async def _evaluate_all_symbols(self):
        """Evalúa cada símbolo activo para detectar cierres de vela M15 y señales."""
        for sym, worker in self.workers.items():
            # Simular tick sutil para mantener el precio vivo en simulación
            if SIMULATION_MODE:
                tick_delta = random.choice([-1, 0, 1]) * worker.meta.tick_size
                worker.current_price = round(worker.current_price + tick_delta, 4)

            # Comprobar si hay señal pendiente
            signal = worker.last_signal
            if signal and sym not in self.risk_guardian.open_positions:
                # Validar con el guardián de riesgo
                can_open, risk_reason = self.risk_guardian.can_open_position(sym, contracts=2)
                if can_open:
                    await self._execute_signal(signal)
                    # Limpiar señal para no repetir
                    worker.last_signal = None
                else:
                    self.log(f"Señal en {sym} bloqueada por riesgo: {risk_reason}", "WARNING")

    async def _execute_signal(self, signal: TradeSignal):
        """Ejecuta una señal autorizada enviando la orden Bracket a TopstepX."""
        meta = AVAILABLE_SYMBOLS[signal.symbol]
        contracts = self.strategy_cfg.contracts_per_trade

        self.log(
            f"🎯 DISPARO ORDEN: {signal.action} {contracts}x {signal.symbol} @ {signal.entry_price} "
            f"| SL: {signal.sl_price} ({signal.risk_ticks} ticks) | TP: {signal.tp_price} ({signal.reward_ticks} ticks) "
            f"| Ratio: 1:{signal.risk_reward_ratio}"
        )

        order_res = await self.api_client.send_bracket_order(
            symbol=signal.symbol,
            action=signal.action,
            quantity=contracts,
            limit_price=signal.entry_price,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price
        )

        order_id = order_res.get("orderId", f"SIM_{int(time.time())}")
        self.risk_guardian.record_open_position(
            symbol=signal.symbol,
            action=signal.action,
            size=contracts,
            entry_price=signal.entry_price,
            sl=signal.sl_price,
            tp=signal.tp_price,
            order_id=order_id
        )

    async def flatten_symbol(self, symbol: str, reason: str = "Manual"):
        """Liquida una posición abierta inmediatamente."""
        if symbol in self.risk_guardian.open_positions:
            pos = self.risk_guardian.open_positions[symbol]
            worker = self.workers.get(symbol)
            exit_price = worker.current_price if worker else pos["entry_price"]
            
            # Calcular PnL aproximado
            meta = AVAILABLE_SYMBOLS[symbol]
            ticks = (exit_price - pos["entry_price"]) / meta.tick_size
            if pos["action"] == "SELL":
                ticks = -ticks
            pnl = ticks * meta.tick_value * pos["size"]

            self.risk_guardian.record_close_position(symbol, exit_price, pnl)
            await self.api_client.flatten_all_positions(symbol)
            self.log(f"🛑 Posición en {symbol} cerrada ({reason}). PnL: ${pnl:.2f}")

    def get_full_state_snapshot(self) -> dict:
        """Devuelve el estado completo del sistema para la API / Web UI."""
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
            "closed_trades": self.risk_guardian.closed_trades_history[:15],
            "symbols": symbols_data,
            "active_symbols": self.strategy_cfg.active_symbols,
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "logs": self.logs_history[:30]
        }
