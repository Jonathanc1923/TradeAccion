"""
Configuración Global del Sistema de Trading VWAP M30 para Servidor
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
    max_risk_ticks: int = 30

# Metadatos precisos para todos los futuros CME admitidos en Topstep
AVAILABLE_SYMBOLS: Dict[str, SymbolMetadata] = {
    # Índices Minis Estándar CME (Principales)
    "ES": SymbolMetadata("ES", "E-mini S&P 500", 0.25, 12.50, 2, False, "EQUITIES", 24),
    "NQ": SymbolMetadata("NQ", "E-mini Nasdaq 100", 0.25, 5.00, 2, False, "EQUITIES", 48),
    "YM": SymbolMetadata("YM", "E-mini Dow Jones", 1.00, 5.00, 2, False, "EQUITIES", 30),
    "RTY": SymbolMetadata("RTY", "E-mini Russell 2000", 0.10, 5.00, 2, False, "EQUITIES", 30),
    # Commodities Estándar
    "CL": SymbolMetadata("CL", "Crude Oil (Petróleo)", 0.01, 10.00, 2, False, "COMMODITIES", 35),
    "GC": SymbolMetadata("GC", "Gold (Oro)", 0.10, 10.00, 2, False, "METALS", 30),
    
    # Índices Micros CME
    "MES": SymbolMetadata("MES", "Micro E-mini S&P 500", 0.25, 1.25, 4, True, "EQUITIES", 24),
    "MNQ": SymbolMetadata("MNQ", "Micro E-mini Nasdaq 100", 0.25, 0.50, 4, True, "EQUITIES", 48),
    "MYM": SymbolMetadata("MYM", "Micro E-mini Dow Jones", 1.00, 0.50, 4, True, "EQUITIES", 30),
    "M2K": SymbolMetadata("M2K", "Micro E-mini Russell 2000", 0.10, 0.50, 4, True, "EQUITIES", 30),
    "MCL": SymbolMetadata("MCL", "Micro Crude Oil", 0.01, 1.00, 4, True, "COMMODITIES", 35),
    "MGC": SymbolMetadata("MGC", "Micro Gold", 0.10, 1.00, 4, True, "METALS", 30)
}

# IDs de contratos continuos / activos oficiales en api.topstepx.com
TOPSTEPX_CONTRACT_IDS: Dict[str, str] = {
    "ES": "CON.F.US.EP.Z26",
    "NQ": "CON.F.US.ENQ.Z26",
    "CL": "CON.F.US.CLE.Z26",
    "YM": "CON.F.US.MYM.Z26",
    "MES": "CON.F.US.MES.Z26",
    "MNQ": "CON.F.US.MNQ.Z26",
    "MCL": "CON.F.US.MCL.Z26",
    "MYM": "CON.F.US.MYM.Z26"
}

@dataclass
class StrategyConfig:
    candle_timeframe_minutes: int = 30       # Velas institucionales de 30 minutos (M30)
    contracts_per_trade: int = 2             # 2 Contratos fijos por operación
    sl_ticks_from_vwap: int = 5              # Exactamente 5 ticks detrás de la línea VWAP
    tp_band_distance_ratio: float = 0.50     # 50% de la distancia entre VWAP y la banda siguiente
    band_std_multiplier: float = 1.0         # Banda 1 de desviación estándar
    
    # Símbolos activos por defecto (ES, NQ, CL, YM)
    active_symbols: List[str] = field(default_factory=lambda: ["ES", "NQ", "CL", "YM"])

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
