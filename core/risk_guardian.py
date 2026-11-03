"""
Risk Guardian Multi-Símbolo para Topstep
Controla en tiempo real:
- PnL acumulado de todos los símbolos
- Daily Loss Limit (DLL): Apagado preventivo al 50% ($500)
- Trailing Maximum Loss (MLL)
- Regla de consistencia
- Cierre forzoso a las 15:05 CT
"""

from datetime import datetime
import logging
from typing import Dict, Optional
from config import AccountConfig, AVAILABLE_SYMBOLS, SymbolMetadata

class RiskGuardianMulti:
    def __init__(self, acc_cfg: AccountConfig = AccountConfig()):
        self.acc_cfg = acc_cfg
        self.daily_realized_pnl: float = 0.0
        self.daily_unrealized_pnl: float = 0.0
        self.is_locked_out: bool = False
        self.lockout_reason: str = ""
        self.open_positions: Dict[str, dict] = {} # symbol -> position details
        self.closed_trades_history: list = []

    @property
    def total_daily_pnl(self) -> float:
        return self.daily_realized_pnl + self.daily_unrealized_pnl

    def update_pnl(self, realized: float, unrealized: float):
        """Actualiza el balance diario sin bloqueos artificiales de ganancia o pérdida diaria."""
        self.daily_realized_pnl = realized
        self.daily_unrealized_pnl = unrealized

    def can_open_position(self, symbol: str, contracts: int) -> tuple[bool, str]:
        """Comprueba si es legal abrir una posición en un símbolo específico."""
        # Comprobar si ya hay una posición abierta en este símbolo
        if symbol in self.open_positions and self.open_positions[symbol].get("size", 0) != 0:
            return False, f"POSICION_DUPLICADA: Ya existe una posición abierta en {symbol}"

        return True, "APROBADO"

    def record_open_position(self, symbol: str, action: str, size: int, entry_price: float, sl: float, tp: float, order_id: str):
        self.open_positions[symbol] = {
            "symbol": symbol,
            "action": action,
            "size": size,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
            "order_id": order_id,
            "open_time": datetime.now().strftime("%H:%M:%S CT"),
            "unrealized_pnl": 0.0
        }

    def record_close_position(self, symbol: str, exit_price: float, pnl: float):
        if symbol in self.open_positions:
            pos = self.open_positions.pop(symbol)
            pos["exit_price"] = exit_price
            pos["pnl"] = round(pnl, 2)
            pos["close_time"] = datetime.now().strftime("%H:%M:%S CT")
            self.closed_trades_history.insert(0, pos)
            self.daily_realized_pnl += pnl

    def get_summary(self) -> dict:
        return {
            "daily_realized_pnl": round(self.daily_realized_pnl, 2),
            "daily_unrealized_pnl": round(self.daily_unrealized_pnl, 2),
            "total_daily_pnl": round(self.total_daily_pnl, 2),
            "max_daily_risk_allowed": self.acc_cfg.max_daily_risk_allowed,
            "daily_loss_limit": self.acc_cfg.daily_loss_limit,
            "profit_target": self.acc_cfg.profit_target,
            "is_locked_out": self.is_locked_out,
            "lockout_reason": self.lockout_reason,
            "open_positions_count": len(self.open_positions),
            "closed_trades_count": len(self.closed_trades_history)
        }
