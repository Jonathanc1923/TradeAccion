"""
Motor de Cálculo Matemático de Alta Precisión: VWAP de Sesión y Bandas de Desviación Estándar
Cálculo incremental sin lag, con anclaje diario por sesión y verificación de desvío.
"""

import math
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class Candle15M:
    timestamp: int           # Epoch milliseconds
    datetime_str: str        # Formato ISO / legible
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float = 0.0
    upper_band_1: float = 0.0
    lower_band_1: float = 0.0
    upper_band_2: float = 0.0
    lower_band_2: float = 0.0
    is_closed: bool = True

class PrecisionVWAP:
    def __init__(self, std_multiplier: float = 1.0):
        self.std_multiplier = std_multiplier
        self.reset()

    def reset(self):
        """Resetea el acumulador para el inicio de una nueva sesión de subasta."""
        self.cum_volume: float = 0.0
        self.cum_vol_price: float = 0.0       # Sum(Vol * TP)
        self.cum_vol_price_sq: float = 0.0    # Sum(Vol * TP^2)
        self.current_vwap: float = 0.0
        self.current_std: float = 0.0
        self.upper_band_1: float = 0.0
        self.lower_band_1: float = 0.0
        self.upper_band_2: float = 0.0
        self.lower_band_2: float = 0.0
        self.candle_count: int = 0

    def update_candle(self, high: float, low: float, close: float, volume: float) -> tuple[float, float, float]:
        """
        Calcula el VWAP y bandas incorporando una vela o tick:
        TP = (High + Low + Close) / 3
        VWAP = Sum(TP * Vol) / Sum(Vol)
        StdDev = Sqrt( max(0, E[TP^2] - (VWAP)^2) )
        Retorna (vwap, upper_band_1, lower_band_1)
        """
        if volume <= 0:
            volume = 1.0 # Evitar división por cero si el volumen reportado es nulo

        typical_price = (high + low + close) / 3.0
        vol_price = typical_price * volume
        vol_price_sq = (typical_price ** 2) * volume

        self.cum_volume += volume
        self.cum_vol_price += vol_price
        self.cum_vol_price_sq += vol_price_sq
        self.candle_count += 1

        # Cálculo de VWAP
        self.current_vwap = self.cum_vol_price / self.cum_volume

        # Cálculo de Desviación Estándar poblacional ponderada por volumen
        mean_sq = self.cum_vol_price_sq / self.cum_volume
        variance = max(0.0, mean_sq - (self.current_vwap ** 2))
        self.current_std = math.sqrt(variance)

        # Si aún no hay suficiente dispersión, usamos un mínimo basado en spread
        if self.current_std < 0.25:
            self.current_std = 0.50

        # Bandas
        self.upper_band_1 = self.current_vwap + (self.std_multiplier * self.current_std)
        self.lower_band_1 = self.current_vwap - (self.std_multiplier * self.current_std)
        self.upper_band_2 = self.current_vwap + (2.0 * self.current_std)
        self.lower_band_2 = self.current_vwap - (2.0 * self.current_std)

        return self.current_vwap, self.upper_band_1, self.lower_band_1

    def get_snapshot(self) -> dict:
        return {
            "vwap": round(self.current_vwap, 4),
            "std": round(self.current_std, 4),
            "upper_band_1": round(self.upper_band_1, 4),
            "lower_band_1": round(self.lower_band_1, 4),
            "upper_band_2": round(self.upper_band_2, 4),
            "lower_band_2": round(self.lower_band_2, 4),
            "candle_count": self.candle_count,
            "cum_volume": int(self.cum_volume)
        }
