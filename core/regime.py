"""
Market State Machine & Regime Classifier
Clasifica el entorno diario entre:
1. Régimen de Gamma Positiva (+GEX): Mercado balanceado / Supresión de volatilidad / Fades en extremos.
2. Régimen de Gamma Negativa (-GEX): Expansión direccional / Rupturas de Initial Balance.
3. Desbalance de Inventario Nocturno (Regla del 80%).
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional

class MarketRegime(Enum):
    POSITIVE_GAMMA_BALANCED = "POSITIVE_GAMMA_BALANCED" # Mean Reversion Setup
    NEGATIVE_GAMMA_TRENDING = "NEGATIVE_GAMMA_TRENDING" # Breakout Setup
    UNDEFINED = "UNDEFINED"

@dataclass
class AuctionLevels:
    prior_settlement: float
    overnight_high: float
    overnight_low: float
    overnight_volume: int
    overnight_net_inventory_pct: float # > 80% = Inventario largo desbalanceado, < -80% = Inventario corto
    initial_balance_high: Optional[float] = None
    initial_balance_low: Optional[float] = None
    gamma_flip_level: Optional[float] = None

class RegimeClassifier:
    def __init__(self):
        self.regime: MarketRegime = MarketRegime.UNDEFINED
        self.levels: Optional[AuctionLevels] = None

    def update_premarket_levels(self, levels: AuctionLevels):
        """Carga los niveles estructurales antes de la apertura (RTH 09:30 EST)."""
        self.levels = levels

    def classify_daily_regime(self, current_spot: float, gamma_flip: float) -> MarketRegime:
        """
        Clasifica el régimen según la posición del precio respecto al Gamma Flip de los creadores de mercado:
        - Si Spot > Gamma Flip: Dealers están en Long Gamma (+GEX) -> Media reversión.
        - Si Spot < Gamma Flip: Dealers están en Short Gamma (-GEX) -> Volatilidad / Expansión.
        """
        if current_spot >= gamma_flip:
            self.regime = MarketRegime.POSITIVE_GAMMA_BALANCED
        else:
            self.regime = MarketRegime.NEGATIVE_GAMMA_TRENDING
        return self.regime

    def is_overnight_inventory_imbalanced(self) -> tuple[bool, str]:
        """
        Regla del 80% de Inventario Nocturno:
        Si el 80% o más del volumen overnight ocurrió completamente por encima o por debajo
        del cierre anterior, existe una probabilidad estadística muy alta de reequilibrio hacia el Settlement
        si la apertura no logra generar aceptación inmediata en la primera media hora.
        """
        if not self.levels:
            return False, "Niveles no inicializados"

        if self.levels.overnight_net_inventory_pct >= 80.0:
            return True, "INVENTARIO_NETO_LARGO (Riesgo de corrección a la baja hacia Settlement)"
        elif self.levels.overnight_net_inventory_pct <= -80.0:
            return True, "INVENTARIO_NETO_CORTO (Riesgo de corrección al alza hacia Settlement)"

        return False, "INVENTARIO_EQUILIBRADO"
