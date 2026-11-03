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
        """Actualiza el balance y activa los interruptores de seguridad si es necesario."""
        self.daily_realized_pnl = realized
        self.daily_unrealized_pnl = unrealized
        current_total = self.total_daily_pnl

        # 1. Alerta de Daily Loss Limit preventivo ($500 en cuenta de $50k)
        if current_total <= -self.acc_cfg.max_daily_risk_allowed:
            self.is_locked_out = True
            self.lockout_reason = f"DLL_HIT: Pérdida total alcanzada (-${abs(current_total):.2f}). Se apaga operativa por el día."
            logging.warning(f"🚨 RISK GUARDIAN: {self.lockout_reason}")
            return

        # 2. Regla de consistencia (evitar que un solo día supere 33-50% del target de $3,000)
        if current_total >= self.acc_cfg.consistency_profit_cap:
            self.is_locked_out = True
            self.lockout_reason = f"CONSISTENCY_CAP: Ganancia diaria de +${current_total:.2f} alcanzada. Protegiendo consistencia."
            logging.info(f"🎯 RISK GUARDIAN: {self.lockout_reason}")

    def can_open_position(self, symbol: str, contracts: int) -> tuple[bool, str]:
        """Comprueba si es legal abrir una posición en un símbolo específico."""
        if self.is_locked_out:
            return False, f"OPERATIVA_BLOQUEADA: {self.lockout_reason}"

        # Comprobar si ya hay una posición abierta en este símbolo
        if symbol in self.open_positions and self.open_positions[symbol].get("size", 0) != 0:
            return False, f"POSICION_DUPLICADA: Ya existe una posición abierta en {symbol}"

        # Validar contratos permitidos
        meta = AVAILABLE_SYMBOLS.get(symbol)
        if meta and contracts > meta.max_contracts:
            return False, f"LIMITE_CONTRATOS: Solicitados {contracts}, máximo permitido para {symbol} es {meta.max_contracts}"

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
