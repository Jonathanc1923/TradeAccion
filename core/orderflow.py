"""
Order Flow & Microstructure Engine
Cálculo en tiempo real de:
- Cumulative Volume Delta (CVD)
- Agresión de compra vs venta (Bid/Ask volume)
- Detección de absorción pasiva (Icebergs y agotamiento en extremos de subasta)
"""

from dataclasses import dataclass
from typing import Optional, List

@dataclass
class TradeTick:
    timestamp: float
    price: float
    size: int
    bid: float
    ask: float

@dataclass
class AbsorptionSignal:
    direction: str          # "BUY" (absorción de vendedores) o "SELL" (absorción de compradores)
    price: float
    divergence_delta: float
    reason: str

class OrderFlowEngine:
    def __init__(self, delta_divergence_threshold: float = 250.0):
        self.cumulative_delta: float = 0.0
        self.total_buy_volume: int = 0
        self.total_sell_volume: int = 0
        self.divergence_threshold = delta_divergence_threshold
        
        # Historial reciente de ticks para análisis de divergencia
        self.recent_ticks: List[TradeTick] = []
        self.delta_history: List[float] = []
        self.price_history: List[float] = []

    def process_tick(self, tick: TradeTick) -> float:
        """
        Clasifica la agresión del tick mediante el algoritmo Lee-Ready / BBO:
        - Si el trade ocurre al Ask o superior: Agresión Compradora (Delta positivo)
        - Si el trade ocurre al Bid o inferior: Agresión Vendedora (Delta negativo)
        - Si ocurre entre el spread: Utiliza la regla del tick (Tick Rule)
        """
        delta = 0.0
        if tick.price >= tick.ask:
            delta = float(tick.size)
            self.total_buy_volume += tick.size
        elif tick.price <= tick.bid:
            delta = -float(tick.size)
            self.total_sell_volume += tick.size
        else:
            # Tick rule
            last_price = self.price_history[-1] if self.price_history else tick.price
            if tick.price > last_price:
                delta = float(tick.size)
                self.total_buy_volume += tick.size
            elif tick.price < last_price:
                delta = -float(tick.size)
                self.total_sell_volume += tick.size

        self.cumulative_delta += delta
        self.price_history.append(tick.price)
        self.delta_history.append(self.cumulative_delta)

        # Mantener búfer de los últimos 500 eventos para agilidad en memoria
        if len(self.price_history) > 500:
            self.price_history.pop(0)
            self.delta_history.pop(0)

        return self.cumulative_delta

    def check_absorption_at_level(self, level: float, is_high_level: bool) -> Optional[AbsorptionSignal]:
        """
        Verifica si se produjo absorción institucional en un extremo de subasta:
        - Si es un High (IBH / Overnight High): El precio penetra el nivel, pero el delta delta no puede
          empujarlo más allá y se desploma, revelando órdenes pasivas gigantes de venta.
        - Si es un Low (IBL / Overnight Low): El precio penetra hacia abajo pero el delta rebota fuertemente.
        """
        if len(self.price_history) < 20:
            return None

        recent_prices = self.price_history[-20:]
        recent_deltas = self.delta_history[-20:]

        current_price = recent_prices[-1]
        delta_change = recent_deltas[-1] - recent_deltas[0]

        if is_high_level:
            # Prueba de absorción en techo: precio intentó romper el nivel pero fue rechazado
            max_recent_p = max(recent_prices)
            if max_recent_p >= level and current_price < level:
                # Compradores agresivos intentaron romper, pero el delta cayó o no hubo continuación
                if delta_change < -self.divergence_threshold:
                    return AbsorptionSignal(
                        direction="SELL",
                        price=current_price,
                        divergence_delta=delta_change,
                        reason="Absorción institucional pasiva detectada en techo de subasta."
                    )
        else:
            # Prueba de absorción en suelo: precio intentó romper abajo pero fue absorbido
            min_recent_p = min(recent_prices)
            if min_recent_p <= level and current_price > level:
                if delta_change > self.divergence_threshold:
                    return AbsorptionSignal(
                        direction="BUY",
                        price=current_price,
                        divergence_delta=delta_change,
                        reason="Absorción institucional pasiva detectada en suelo de subasta."
                    )

        return None
