"""
Configuración Global del Sistema de Trading VWAP M15 para Servidor
Multi-símbolo, horarios de mantenimiento y parámetros de Topstep.
"""

from dataclasses import dataclass, field
from typing import Dict, List
import os

@dataclass(frozen=True)
class SymbolMetadata:
    symbol: str
    name: str
    tick_size: float
    tick_value: float
    max_contracts: int
    is_micro: bool
    category: str

# Metadatos precisos para todos los futuros CME admitidos en Topstep
AVAILABLE_SYMBOLS: Dict[str, SymbolMetadata] = {
    # Índices (Micros y Minis)
    "MES": SymbolMetadata("MES", "Micro E-mini S&P 500", 0.25, 1.25, 4, True, "EQUITIES"),
    "ES": SymbolMetadata("ES", "E-mini S&P 500", 0.25, 12.50, 1, False, "EQUITIES"),
    "MNQ": SymbolMetadata("MNQ", "Micro E-mini Nasdaq 100", 0.25, 0.50, 4, True, "EQUITIES"),
    "NQ": SymbolMetadata("NQ", "E-mini Nasdaq 100", 0.25, 5.00, 1, False, "EQUITIES"),
    "MYM": SymbolMetadata("MYM", "Micro E-mini Dow Jones", 1.00, 0.50, 4, True, "EQUITIES"),
    "YM": SymbolMetadata("YM", "E-mini Dow Jones", 1.00, 5.00, 1, False, "EQUITIES"),
    "M2K": SymbolMetadata("M2K", "Micro E-mini Russell 2000", 0.10, 0.50, 4, True, "EQUITIES"),
    "RTY": SymbolMetadata("RTY", "E-mini Russell 2000", 0.10, 5.00, 1, False, "EQUITIES"),
    # Commodities
    "MCL": SymbolMetadata("MCL", "Micro Crude Oil", 0.01, 1.00, 4, True, "COMMODITIES"),
    "CL": SymbolMetadata("CL", "Crude Oil", 0.01, 10.00, 1, False, "COMMODITIES"),
    "MGC": SymbolMetadata("MGC", "Micro Gold", 0.10, 1.00, 4, True, "METALS"),
    "GC": SymbolMetadata("GC", "Gold", 0.10, 10.00, 1, False, "METALS")
}

@dataclass
class StrategyConfig:
    candle_timeframe_minutes: int = 15
    sl_ticks_from_vwap: int = 5              # Exactamente 5 ticks detrás de la línea VWAP
    tp_band_distance_ratio: float = 0.50     # 50% de la distancia entre VWAP y la banda siguiente
    band_std_multiplier: float = 1.0         # Banda 1 de desviación estándar
    
    # Símbolos activos por defecto (se pueden alternar desde la UI)
    active_symbols: List[str] = field(default_factory=lambda: ["MES", "MNQ", "MCL", "MGC"])

@dataclass
class ScheduleConfig:
    # Horarios en Central Time (CT) - Horario estándar de CME y Topstep
    maintenance_start_ct: str = "16:00"     # Inicio pausa de mantenimiento diario
    maintenance_end_ct: str = "17:00"       # Fin pausa mantenimiento (Reapertura mercado)
    post_maintenance_blackout_hours: float = 3.0 # Esperar 3 horas exactas tras reapertura (20:00 CT)
    
    sunday_open_ct: str = "17:00"           # Apertura domingos
    post_sunday_open_blackout_hours: float = 3.0 # Esperar 3 horas exactas domingo (20:00 CT)
    
    daily_forced_flatten_ct: str = "15:05"  # Cierre incondicional antes de regla 15:10 CT de Topstep
    session_anchor_reset_ct: str = "17:00"  # Hora en que se resetea el VWAP de la nueva sesión

@dataclass
class AccountConfig:
    account_name: str = "PRAC-V2-460183-95785678"
    account_id: str = "29324973"
    account_size: float = 150_000.0         # Cuenta de Prácticas de $150k de Topstep
    daily_loss_limit: float = 2_000.0        # Límite diario asignado para cuenta 150k
    max_daily_risk_allowed: float = 1_000.0  # Apagado automático preventivo ($1,000)
    max_trailing_drawdown: float = 4_500.0   # MLL exacto ($145,500.00 según captura)
    profit_target: float = 9_000.0           # Target de ganancia para cuenta 150k
    consistency_profit_cap: float = 3_000.0  # Lockout de ganancia diaria para consistencia

# Cargar variables de entorno desde .env si existe
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# Configuración del Servidor Web & API
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PORT", os.getenv("SERVER_PORT", "8000")))
TOPSTEPX_BASE_URL = os.getenv("TOPSTEPX_BASE_URL", "https://api.topstepx.com")
TOPSTEPX_WS_URL = os.getenv("TOPSTEPX_WS_URL", "wss://live.topstepx.com/stream")
TOPSTEPX_API_KEY = os.getenv("TOPSTEPX_API_KEY", "")
TOPSTEPX_API_SECRET = os.getenv("TOPSTEPX_API_SECRET", "")
TOPSTEPX_USERNAME = os.getenv("TOPSTEPX_USERNAME", "")
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "false").lower() == "true"
