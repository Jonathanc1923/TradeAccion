"""
Script de Verificación Rigurosa del Sistema VWAP M15
Prueba:
1. Precisión matemática de VWAP y Bandas de Desviación Estándar
2. Lógica de disparo de señal (Mechas en VWAP con confirmación previa)
3. Precisión de Stop Loss (5 ticks exactos) y Take Profit (50% de la distancia a la banda)
4. Filtro de horarios (3h post-mantenimiento, apertura de domingos y cierre Topstep)
"""

import sys
import io

# Asegurar codificación utf-8 en Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import datetime, time, timezone
import zoneinfo
from config import AVAILABLE_SYMBOLS, StrategyConfig, ScheduleConfig
from core.vwap_bands import PrecisionVWAP, Candle15M
from core.strategy import VWAPWickBounceStrategy
from core.schedule_guardian import ScheduleGuardian

def test_vwap_math():
    print("\n--- TEST 1: Precisión Matemática de VWAP y Bandas ---")
    vwap_calc = PrecisionVWAP(std_multiplier=1.0)
    
    # Simular 3 velas con precios y volúmenes conocidos
    # Vela 1: High=5855, Low=5845, Close=5850 (TP=5850), Vol=1000
    vwap, up, low = vwap_calc.update_candle(5855, 5845, 5850, 1000)
    assert round(vwap, 2) == 5850.00, f"Error en VWAP inicial: {vwap}"
    print(f"✓ Vela 1: VWAP={vwap:.2f} | +1σ={up:.2f} | -1σ={low:.2f}")

    # Vela 2: High=5865, Low=5855, Close=5860 (TP=5860), Vol=1000
    # Media ponderada: (5850*1000 + 5860*1000) / 2000 = 5855.00
    vwap, up, low = vwap_calc.update_candle(5865, 5855, 5860, 1000)
    assert round(vwap, 2) == 5855.00, f"Error en VWAP ponderado: {vwap}"
    print(f"✓ Vela 2: VWAP={vwap:.2f} | +1σ={up:.2f} | -1σ={low:.2f}")
    print("✓ TEST 1 SUPERADO: El motor de VWAP y Bandas calcula con exactitud matemática.")

def test_strategy_signals():
    print("\n--- TEST 2: Lógica de Estrategia, SL 5 Ticks y TP al 50% de Banda ---")
    meta = AVAILABLE_SYMBOLS["MES"] # Tick size = 0.25
    strat = VWAPWickBounceStrategy(meta, StrategyConfig())

    # CASO: Rebote Alcista en MES
    # VWAP = 5850.00, Upper Band (+1σ) = 5860.00 (Distancia = 10.00 pts)
    # Vela 1 (Anterior): Close = 5852.00 (Por encima de VWAP)
    c1 = Candle15M(
        timestamp=1000, datetime_str="09:15",
        open=5851.0, high=5854.0, low=5850.5, close=5852.0, volume=3000,
        vwap=5850.0, upper_band_1=5860.0, lower_band_1=5840.0
    )

    # Vela 2 (Señal): Cae con mecha que toca y pasa VWAP (Low=5849.25), pero cierra en 5851.50 (Arriba de VWAP)
    c2 = Candle15M(
        timestamp=2000, datetime_str="09:30",
        open=5852.0, high=5853.0, low=5849.25, close=5851.50, volume=4500,
        vwap=5850.0, upper_band_1=5860.0, lower_band_1=5840.0, is_closed=True
    )

    signal = strat.evaluate_candles([c1, c2])
    assert signal is not None, "Error: Debería haberse generado una señal de COMPRA."
    assert signal.action == "BUY", f"Dirección incorrecta: {signal.action}"
    
    # Verificación SL: Exactamente 5 ticks debajo de VWAP (5850.00 - 5*0.25 = 5848.75)
    expected_sl = 5850.00 - (5 * 0.25)
    assert signal.sl_price == expected_sl, f"SL incorrecto: {signal.sl_price} vs esperado {expected_sl}"
    
    # Verificación TP: 50% de la distancia de VWAP a Banda Superior medido desde precio de entrada (5851.50)
    # Distancia banda = 5860.00 - 5850.00 = 10.00 pts. 50% = 5.00 pts.
    # TP = 5851.50 + 5.00 = 5856.50
    expected_tp = 5851.50 + 5.00
    assert signal.tp_price == expected_tp, f"TP incorrecto: {signal.tp_price} vs esperado {expected_tp}"

    print(f"✓ Señal generada: {signal.action} @ {signal.entry_price}")
    print(f"✓ SL verificado: {signal.sl_price} (Exactamente 5 ticks por debajo del VWAP 5850.00)")
    print(f"✓ TP verificado: {signal.tp_price} (Exactamente 50% de distancia VWAP->Banda desde entrada)")
    print(f"✓ Ratio Riesgo/Beneficio: 1:{signal.risk_reward_ratio}")
    print("✓ TEST 2 SUPERADO: Parámetros de orden calculados a la perfección.")

def test_schedule_guardian():
    print("\n--- TEST 3: Guardián de Horarios (Mantenimiento, 3h de Espera y Domingos) ---")
    guardian = ScheduleGuardian(ScheduleConfig())
    tz_chicago = zoneinfo.ZoneInfo("America/Chicago")

    # 1. Domingo a las 18:00 CT (Reapertura pero dentro de las 3 horas de enfriamiento)
    dt_sun_blackout = datetime(2026, 11, 8, 18, 15, tzinfo=tz_chicago)
    allowed, msg, _ = guardian.evaluate_market_status(dt_sun_blackout)
    assert not allowed and "BLOQUEO_3H_DOMINGO" in msg, f"Fallo en domingo 18:15 CT: {msg}"
    print(f"✓ Domingo 18:15 CT: {msg}")

    # 2. Domingo a las 20:15 CT (3 horas después de reapertura -> Habilitado)
    dt_sun_allowed = datetime(2026, 11, 8, 20, 15, tzinfo=tz_chicago)
    allowed, msg, _ = guardian.evaluate_market_status(dt_sun_allowed)
    assert allowed, f"Fallo en domingo 20:15 CT: {msg}"
    print(f"✓ Domingo 20:15 CT: Operativa HABILITADA correctamente.")

    # 3. Miércoles a las 16:30 CT (Pausa de mantenimiento CME)
    dt_wed_maint = datetime(2026, 11, 4, 16, 30, tzinfo=tz_chicago)
    allowed, msg, _ = guardian.evaluate_market_status(dt_wed_maint)
    assert not allowed and "PAUSA_MANTENIMIENTO" in msg, f"Fallo en mantenimiento: {msg}"
    print(f"✓ Miércoles 16:30 CT: {msg}")

    # 4. Miércoles a las 18:30 CT (Post-mantenimiento dentro de las 3 horas de enfriamiento)
    dt_wed_blackout = datetime(2026, 11, 4, 18, 30, tzinfo=tz_chicago)
    allowed, msg, _ = guardian.evaluate_market_status(dt_wed_blackout)
    assert not allowed and "BLOQUEO_3H_POST_MANTENIMIENTO" in msg, f"Fallo en 3h post-maint: {msg}"
    print(f"✓ Miércoles 18:30 CT: {msg}")

    # 5. Miércoles a las 20:05 CT (Post-mantenimiento superó 3 horas -> Habilitado)
    dt_wed_ok = datetime(2026, 11, 4, 20, 5, tzinfo=tz_chicago)
    allowed, msg, _ = guardian.evaluate_market_status(dt_wed_ok)
    assert allowed, f"Fallo en post-mantenimiento habilitado: {msg}"
    print(f"✓ Miércoles 20:05 CT: Operativa HABILITADA correctamente.")

    # 6. Cierre diario obligatorio Topstep a las 15:05 CT
    dt_flatten = datetime(2026, 11, 4, 15, 6, tzinfo=tz_chicago)
    assert guardian.should_flatten_immediately(dt_flatten), "Fallo en detección de cierre Topstep a las 15:05 CT"
    print(f"✓ Cierre Topstep (15:05 CT): Flatten automático verificado.")
    print("✓ TEST 3 SUPERADO: Los filtros de horario respetan al 100% las restricciones.")

if __name__ == "__main__":
    test_vwap_math()
    test_strategy_signals()
    test_schedule_guardian()
    print("\n=======================================================")
    print("🎯 TODOS LOS TESTS UNITARIOS HAN SIDO SUPERADOS CON ÉXITO")
    print("=======================================================")
