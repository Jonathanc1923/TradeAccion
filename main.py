"""
Orquestador Principal del Sistema de Trading Algorítmico para Topstep
Sincroniza:
1. Clasificación del Régimen de Gamma 0DTE y niveles de subasta (Pre-Market).
2. Procesamiento de Order Flow (CVD y absorción) en tiempo real.
3. Validación estricta con el Risk Guardian (Topstep Rules).
4. Ejecución de órdenes bracket en TopstepX.
"""

import asyncio
from datetime import datetime
import logging
from config import TopstepAccountConfig, InstrumentConfig, TOPSTEPX_API_KEY, TOPSTEPX_API_SECRET, TOPSTEPX_BASE_URL, TOPSTEPX_WS_URL
from core.risk_guardian import RiskGuardian
from core.orderflow import OrderFlowEngine, TradeTick
from core.regime import RegimeClassifier, AuctionLevels, MarketRegime
from adapters.topstepx_client import TopstepXClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class TopstepTradingBot:
    def __init__(self):
        self.acc_cfg = TopstepAccountConfig()
        self.inst_cfg = InstrumentConfig()
        self.risk_guardian = RiskGuardian(self.acc_cfg, self.inst_cfg)
        self.order_flow = OrderFlowEngine()
        self.regime_classifier = RegimeClassifier()
        self.api_client = TopstepXClient(
            TOPSTEPX_API_KEY, TOPSTEPX_API_SECRET, TOPSTEPX_BASE_URL, TOPSTEPX_WS_URL
        )

    async def initialize_session(self):
        """Paso 1: Pre-market setup (09:15 EST / 08:15 CT)"""
        logging.info("--- INICIALIZANDO SESIÓN PRE-MARKET ---")
        await self.api_client.authenticate()

        # En producción estos datos se extraen automáticamente de la sesión nocturna y CBOE/GammaLabs
        premarket_levels = AuctionLevels(
            prior_settlement=5850.00,
            overnight_high=5865.50,
            overnight_low=5838.25,
            overnight_volume=185000,
            overnight_net_inventory_pct=42.0, # Inventario neutral
            initial_balance_high=5870.00,
            initial_balance_low=5842.00,
            gamma_flip_level=5840.00          # Nivel de Gamma Flip calculado
        )
        self.regime_classifier.update_premarket_levels(premarket_levels)
        
        # Clasificar régimen inicial
        spot_price = 5855.00
        current_regime = self.regime_classifier.classify_daily_regime(spot_price, premarket_levels.gamma_flip_level)
        logging.info(f"Régimen de Mercado Determinado: {current_regime.name}")

    async def handle_tick(self, tick: TradeTick):
        """Paso 2: Procesamiento de cada trade entrante desde TopstepX"""
        current_time = datetime.now()
        
        # 1. Reglas de corte de tiempo y de seguridad
        is_safe, msg = self.risk_guardian.check_time_rules(current_time)
        if not is_safe:
            if "FORCED_FLATTEN" in msg:
                await self.api_client.flatten_all_positions(self.inst_cfg.symbol)
            return

        # 2. Actualizar Cumulative Volume Delta
        current_cvd = self.order_flow.process_tick(tick)

        # 3. Evaluar setups según el régimen clasificado
        if self.regime_classifier.regime == MarketRegime.POSITIVE_GAMMA_BALANCED:
            # Setup 1: Mean Reversion en extremos del Initial Balance / Overnight High
            levels = self.regime_classifier.levels
            if levels and levels.initial_balance_high:
                # Comprobar si hay absorción en el techo
                signal = self.order_flow.check_absorption_at_level(levels.initial_balance_high, is_high_level=True)
                if signal and signal.direction == "SELL":
                    await self.execute_trade(signal.direction, tick.price)

    async def execute_trade(self, action: str, current_price: float):
        """Paso 3: Validación con Risk Guardian y envío de orden"""
        can_trade, reason = self.risk_guardian.can_open_position(datetime.now(), requested_contracts=2)
        if not can_trade:
            logging.warning(f"Entrada bloqueada por Risk Guardian: {reason}")
            return

        sl_distance = self.inst_cfg.default_stop_loss_ticks * self.inst_cfg.tick_size
        tp_distance = self.inst_cfg.default_take_profit_ticks * self.inst_cfg.tick_size

        if action == "BUY":
            sl_price = current_price - sl_distance
            tp_price = current_price + tp_distance
        else:
            sl_price = current_price + sl_distance
            tp_price = current_price - tp_distance

        logging.info(f"🚀 SEÑAL CONFIRMADA: {action} @ {current_price} | SL: {sl_price} | TP: {tp_price}")
        await self.api_client.send_bracket_order(
            symbol=self.inst_cfg.symbol,
            action=action,
            quantity=2,
            limit_price=current_price,
            sl_price=sl_price,
            tp_price=tp_price
        )

async def main():
    bot = TopstepTradingBot()
    await bot.initialize_session()
    logging.info("Sistema listo y en escucha de eventos de mercado...")

if __name__ == "__main__":
    asyncio.run(main())
