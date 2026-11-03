"""
Schedule Guardian
Gestiona con máxima precisión los horarios de trading, pausas de mantenimiento y periodos de bloqueo:
- Apertura de domingos: 3 horas de espera (sin operar de 17:00 CT a 20:00 CT).
- Pausa de mantenimiento diaria (Lunes a Jueves 16:00 a 17:00 CT): 3 horas de espera tras reapertura (sin operar de 17:00 CT a 20:00 CT).
- Cierre diario forzoso Topstep: 15:05 CT.
- Cierre fin de semana: Viernes 15:05 CT a Domingo 20:00 CT.
"""

from datetime import datetime, time, timezone
import zoneinfo
from config import ScheduleConfig

class ScheduleGuardian:
    def __init__(self, config: ScheduleConfig = ScheduleConfig()):
        self.cfg = config
        # Zona horaria oficial de CME y Topstep (Chicago - Central Time)
        try:
            self.tz_ct = zoneinfo.ZoneInfo("America/Chicago")
        except Exception:
            # Fallback en Windows si no está tzdata instalado
            self.tz_ct = timezone.utc

    def get_current_ct_time(self, dt: datetime = None) -> datetime:
        """Retorna la hora actual convertida a Central Time (CT)."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            return dt.astimezone(self.tz_ct)
        except Exception:
            return dt

    def evaluate_market_status(self, dt: datetime = None) -> tuple[bool, str, dict]:
        """
        Evalúa si es legal operar en el segundo actual según las reglas del usuario y de Topstep.
        Retorna (allowed_to_trade: bool, status_message: str, details: dict)
        """
        ct_now = self.get_current_ct_time(dt)
        weekday = ct_now.weekday() # 0=Lunes, 1=Martes, ..., 5=Sábado, 6=Domingo
        current_time = ct_now.time()

        t_flatten = time(15, 5)
        t_maint_start = time(16, 0)
        t_maint_end = time(17, 0)
        t_blackout_end = time(20, 0) # 17:00 + 3 horas = 20:00 CT

        details = {
            "ct_time": ct_now.strftime("%Y-%m-%d %H:%M:%S CT"),
            "weekday": ct_now.strftime("%A"),
            "is_weekend": False,
            "is_maintenance": False,
            "is_3h_blackout": False,
            "is_forced_flatten": False
        }

        # 1. SÁBADO: Mercado completamente cerrado
        if weekday == 5:
            details["is_weekend"] = True
            return False, "MERCADO_CERRADO: Fin de semana (Sábado)", details

        # 2. DOMINGO:
        # Abre a las 17:00 CT, pero exigimos 3 horas de espera -> Inicia análisis a las 20:00 CT
        if weekday == 6:
            if current_time < t_maint_end:
                details["is_weekend"] = True
                return False, "MERCADO_CERRADO: Domingo previo a reapertura de las 17:00 CT", details
            if t_maint_end <= current_time < t_blackout_end:
                details["is_3h_blackout"] = True
                return False, f"BLOQUEO_3H_DOMINGO: Espera obligatoria de 3 horas post-apertura (Opera desde las 20:00 CT)", details

        # 3. VIERNES: Cierre de semana a las 15:05 CT
        if weekday == 4:
            if current_time >= t_flatten:
                details["is_forced_flatten"] = True
                return False, "CIERRE_SEMANAL_TOPSTEP: Fuera de horario viernes tras 15:05 CT", details

        # 4. LUNES A JUEVES:
        # A) Regla de Topstep: Cierre diario obligatorio antes de 15:10 CT
        if current_time >= t_flatten and current_time < t_maint_start:
            details["is_forced_flatten"] = True
            return False, "CIERRE_DIARIO_TOPSTEP: Horario de cierre obligatorio (15:05 a 16:00 CT)", details

        # B) Pausa oficial de mantenimiento CME (16:00 a 17:00 CT)
        if t_maint_start <= current_time < t_maint_end:
            details["is_maintenance"] = True
            return False, "PAUSA_MANTENIMIENTO_CME: Mercado en mantenimiento (16:00 a 17:00 CT)", details

        # C) Regla de usuario: 3 horas sin operar tras el fin del mantenimiento (17:00 a 20:00 CT)
        if t_maint_end <= current_time < t_blackout_end:
            details["is_3h_blackout"] = True
            return False, "BLOQUEO_3H_POST_MANTENIMIENTO: Enfriamiento de 3h (Sin operar de 17:00 a 20:00 CT)", details

        # Si supera todos los filtros, el mercado está 100% habilitado para operar
        return True, "OPERATIVA_HABILITADA", details

    def should_flatten_immediately(self, dt: datetime = None) -> bool:
        """Determina si se debe liquidar cualquier posición abierta por corte horario."""
        ct_now = self.get_current_ct_time(dt)
        weekday = ct_now.weekday()
        current_time = ct_now.time()

        # Si es viernes después de las 15:05 CT
        if weekday == 4 and current_time >= time(15, 5):
            return True

        # Lunes a Jueves entre las 15:05 CT y las 16:00 CT
        if weekday in (0, 1, 2, 3) and (time(15, 5) <= current_time < time(16, 0)):
            return True

        return False
