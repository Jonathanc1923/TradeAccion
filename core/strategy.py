"""
Estrategia: Rebote de Mechas en VWAP de Sesión (15 Minutos)
Lógica cuantitativa exacta solicitada por el usuario:
- Vela M15 cerrada con mecha que tocó o traspasó la línea VWAP.
- Vela anterior confirmada del lado del rebote esperado.
- Stop Loss: Exactamente 5 ticks por debajo/encima de la línea VWAP.
- Take Profit: 50% de la distancia exacta entre VWAP y la banda siguiente, proyectada desde el precio de entrada.
"""

from dataclasses import dataclass
from typing import Optional, List
import math
from config import SymbolMetadata, StrategyConfig
from core.vwap_bands import Candle15M

@dataclass
class TradeSignal:
    symbol: str
    action: str              # "BUY" o "SELL"
    entry_price: float
    sl_price: float
    tp_price: float
    vwap_at_entry: float
    band_next_at_entry: float
    risk_ticks: int
    reward_ticks: int
    risk_reward_ratio: float
    timestamp: int
    datetime_str: str
    reason: str

class VWAPWickBounceStrategy:
    def __init__(self, symbol_meta: SymbolMetadata, config: StrategyConfig = StrategyConfig()):
        self.meta = symbol_meta
        self.cfg = config
        self.candles: List[Candle15M] = []

    def round_to_tick(self, price: float) -> float:
        """Redondea estrictamente el precio al tamaño de tick del símbolo."""
        ticks = round(price / self.meta.tick_size)
        return round(ticks * self.meta.tick_size, 4)

    def evaluate_candles(self, candles: List[Candle15M]) -> Optional[TradeSignal]:
        """
        Evalúa el cierre de la última vela de 15 minutos completada.
        Requiere un mínimo de 2 velas completas de la sesión actual.
        """
        if len(candles) < 2:
            return None

        # Vela actual completada (señal) y vela anterior (filtro de procedencia)
        c_prev = candles[-2]
        c_curr = candles[-1]

        if not c_curr.is_closed:
            return None

        vwap = c_curr.vwap
        upper_band = c_curr.upper_band_1
        lower_band = c_curr.lower_band_1

        # -------------------------------------------------------------
        # CASO 1: SEÑAL DE COMPRA (Rebote Alcista en VWAP)
        # 1. Vela anterior estaba en el lado alcista: c_prev.close >= c_prev.vwap
        # 2. Vela actual tuvo mecha que tocó o pasó el VWAP: c_curr.low <= vwap
        # 3. Vela actual cerró por encima del VWAP: c_curr.close > vwap
        # -------------------------------------------------------------
        if c_prev.close >= c_prev.vwap and c_curr.low <= vwap and c_curr.close > vwap:
            entry_price = self.round_to_tick(c_curr.close)

            # SL: Exactamente 5 ticks por debajo de la línea VWAP
            sl_price = self.round_to_tick(vwap - (self.cfg.sl_ticks_from_vwap * self.meta.tick_size))

            # TP: 50% de la distancia entre la línea VWAP y la banda superior (+1 StdDev),
            # medido desde el precio de entrada de la operación.
            band_distance = upper_band - vwap
            tp_distance = band_distance * self.cfg.tp_band_distance_ratio
            tp_price = self.round_to_tick(entry_price + tp_distance)

            # Verificación de validez de precios y límites de riesgo institucional
            risk_dist = entry_price - sl_price
            reward_dist = tp_price - entry_price

            if risk_dist > 0 and reward_dist > 0:
                risk_ticks = int(round(risk_dist / self.meta.tick_size))
                reward_ticks = int(round(reward_dist / self.meta.tick_size))
                rr_ratio = round(reward_dist / risk_dist, 2)

                # Filtro de proximidad máxima según el símbolo (ES: 24, NQ: 48, CL: 35, YM: 30 ticks)
                if risk_ticks > self.meta.max_risk_ticks:
                    return None

                return TradeSignal(
                    symbol=self.meta.symbol,
                    action="BUY",
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    vwap_at_entry=round(vwap, 4),
                    band_next_at_entry=round(upper_band, 4),
                    risk_ticks=risk_ticks,
                    reward_ticks=reward_ticks,
                    risk_reward_ratio=rr_ratio,
                    timestamp=c_curr.timestamp,
                    datetime_str=c_curr.datetime_str,
                    reason=f"Rebote alcista con mecha en VWAP ({vwap:.2f}) confirmado por vela anterior alcista."
                )

        # -------------------------------------------------------------
        # CASO 2: SEÑAL DE VENTA (Rebote Bajista en VWAP)
        # 1. Vela anterior estaba en el lado bajista: c_prev.close <= c_prev.vwap
        # 2. Vela actual tuvo mecha que tocó o pasó el VWAP: c_curr.high >= vwap
        # 3. Vela actual cerró por debajo del VWAP: c_curr.close < vwap
        # -------------------------------------------------------------
        if c_prev.close <= c_prev.vwap and c_curr.high >= vwap and c_curr.close < vwap:
            entry_price = self.round_to_tick(c_curr.close)

            # SL: Exactamente 5 ticks por encima de la línea VWAP
            sl_price = self.round_to_tick(vwap + (self.cfg.sl_ticks_from_vwap * self.meta.tick_size))

            # TP: 50% de la distancia entre la línea VWAP y la banda inferior (-1 StdDev),
            # medido desde el precio de entrada de la operación.
            band_distance = vwap - lower_band
            tp_distance = band_distance * self.cfg.tp_band_distance_ratio
            tp_price = self.round_to_tick(entry_price - tp_distance)

            risk_dist = sl_price - entry_price
            reward_dist = entry_price - tp_price

            if risk_dist > 0 and reward_dist > 0:
                risk_ticks = int(round(risk_dist / self.meta.tick_size))
                reward_ticks = int(round(reward_dist / self.meta.tick_size))
                rr_ratio = round(reward_dist / risk_dist, 2)

                # Filtro de proximidad máxima según el símbolo (ES: 24, NQ: 48, CL: 35, YM: 30 ticks)
                if risk_ticks > self.meta.max_risk_ticks:
                    return None

                return TradeSignal(
                    symbol=self.meta.symbol,
                    action="SELL",
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    vwap_at_entry=round(vwap, 4),
                    band_next_at_entry=round(lower_band, 4),
                    risk_ticks=risk_ticks,
                    reward_ticks=reward_ticks,
                    risk_reward_ratio=rr_ratio,
                    timestamp=c_curr.timestamp,
                    datetime_str=c_curr.datetime_str,
                    reason=f"Rebote bajista con mecha en VWAP ({vwap:.2f}) confirmado por vela anterior bajista."
                )

        return None
