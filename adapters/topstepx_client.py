"""
Cliente Asíncrono Oficial para TopstepX (ProjectX REST API)
Implementa la especificación estricta de Swagger v1 de api.topstepx.com:
- Autenticación: POST /api/Auth/loginKey
- Historial de Velas: POST /api/History/retrieveBars (con startTime y endTime obligatorios)
- Cuentas: POST /api/Account/search
- Órdenes Bracket Reales: POST /api/Order/place
- Liquidación de Posiciones: POST /api/Position/closeContract
"""

import aiohttp
import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict

from config import AVAILABLE_SYMBOLS

class TopstepXClient:
    def __init__(self, username: str, api_key: str, base_url: str = "https://api.topstepx.com"):
        self.username = username
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self.is_authenticated: bool = False
        self.account_id: Optional[int] = None
        self.rate_limited_until: float = 0.0

    def get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def authenticate(self) -> bool:
        """Autenticación con TopstepX mediante POST /api/Auth/loginKey."""
        if not self.api_key:
            logging.warning("No se proporcionó API Key de TopstepX.")
            return False

        url = f"{self.base_url}/api/Auth/loginKey"
        payload = {
            "userName": self.username,
            "apiKey": self.api_key
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=self.get_headers(), timeout=15) as resp:
                    data = await resp.json()
                    if data.get("success") and data.get("token"):
                        self.token = data["token"]
                        self.is_authenticated = True
                        logging.info("✅ Conexión con TopstepX API autenticada con éxito.")
                        # Precargar la cuenta activa
                        await self.get_active_account_id()
                        return True
                    else:
                        err_code = data.get("errorCode")
                        err_msg = data.get("errorMessage")
                        logging.error(f"❌ Fallo de autenticación TopstepX (Error {err_code}): {err_msg}")
                        return False
            except Exception as e:
                logging.error(f"Error al conectar con TopstepX: {e}")
                return False

    async def search_accounts(self) -> List[dict]:
        """Consulta las cuentas activas desde POST /api/Account/search."""
        if not self.token:
            return []

        url = f"{self.base_url}/api/Account/search"
        payload = {"onlyActiveAccounts": True}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=self.get_headers(), timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("accounts", [])
            except Exception as e:
                logging.error(f"Error al consultar cuentas: {e}")
        return []

    async def get_active_account_id(self) -> Optional[int]:
        """Obtiene el ID numérico de la cuenta habilitada para operar."""
        if self.account_id:
            return self.account_id

        accounts = await self.search_accounts()
        # Priorizar cuentas con permiso de trading activo
        for acc in accounts:
            if acc.get("canTrade") and acc.get("isVisible"):
                self.account_id = int(acc["id"])
                logging.info(f"💼 Cuenta TopstepX seleccionada: ID={self.account_id} ({acc.get('name')})")
                return self.account_id

        if accounts:
            self.account_id = int(accounts[0]["id"])
            logging.info(f"💼 Cuenta TopstepX fallback: ID={self.account_id} ({accounts[0].get('name')})")
            return self.account_id

        return None

    async def retrieve_bars(
        self,
        contract_id: str,
        unit: int = 2,
        unit_number: int = 30,
        limit: int = 100,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        include_partial_bar: bool = False,
        live: bool = False
    ) -> List[dict]:
        """
        Descarga barras desde POST /api/History/retrieveBars.
        Cumple estrictamente el contrato de Swagger con startTime y endTime obligatorios.
        Protección inteligente contra Rate Limiting (HTTP 429).
        """
        now = time.time()
        if now < self.rate_limited_until:
            wait_rem = int(self.rate_limited_until - now)
            logging.debug(f"Pausa activa por rate limit TopstepX ({wait_rem}s restantes).")
            return []

        if not self.is_authenticated:
            auth_ok = await self.authenticate()
            if not auth_ok:
                return []

        now_utc = datetime.now(timezone.utc)
        if not end_time:
            end_time = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        if not start_time:
            # Ventana de 48 horas para asegurar solapamiento de sesión CME
            start_time = (now_utc - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = f"{self.base_url}/api/History/retrieveBars"
        payload = {
            "contractId": contract_id,
            "live": live,
            "startTime": start_time,
            "endTime": end_time,
            "unit": unit,
            "unitNumber": unit_number,
            "limit": limit,
            "includePartialBar": include_partial_bar
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=self.get_headers(), timeout=15) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("bars", [])
                    elif resp.status == 429:
                        self.rate_limited_until = time.time() + 90.0
                        logging.warning("⚠️ Rate limit 429 detectado en TopstepX API. Enfriando consultas por 90 segundos...")
                        return []
                    else:
                        text = await resp.text()
                        logging.warning(f"Respuesta TopstepX ({resp.status}): {text}")
                        return []
            except Exception as e:
                logging.debug(f"Error de conexión en retrieve_bars: {e}")
                return []

    async def send_bracket_order(
        self,
        contract_id: str,
        action: str, # "BUY" o "SELL"
        quantity: int,
        limit_price: float,
        sl_price: float,
        tp_price: float,
        symbol: Optional[str] = None,
        risk_ticks: Optional[int] = None,
        reward_ticks: Optional[int] = None
    ) -> dict:
        """
        Envía orden Market con Take Profit y Stop Loss automáticos (Brackets en ticks) a TopstepX.
        Utiliza POST /api/Order/place según la especificación Swagger v1.
        """
        if not self.is_authenticated:
            auth_ok = await self.authenticate()
            if not auth_ok:
                return {"status": "ERROR", "errorMessage": "Fallo de autenticación"}

        account_id = await self.get_active_account_id()
        if not account_id:
            logging.error("❌ No se encontró una cuenta activa en TopstepX para operar.")
            return {"status": "ERROR", "errorMessage": "Sin accountId activo"}

        # side: 0 = Bid (Buy), 1 = Ask (Sell)
        side = 0 if action.upper() == "BUY" else 1

        # Calcular ticks si no se recibieron directamente
        meta = AVAILABLE_SYMBOLS.get(symbol) if symbol else None
        tick_size = meta.tick_size if meta else 0.25
        if risk_ticks is None or risk_ticks <= 0:
            risk_ticks = max(1, int(round(abs(limit_price - sl_price) / tick_size)))
        if reward_ticks is None or reward_ticks <= 0:
            reward_ticks = max(1, int(round(abs(tp_price - limit_price) / tick_size)))

        payload = {
            "accountId": int(account_id),
            "contractId": contract_id,
            "type": 2, # 2 = Market (ejecución garantizada al Open de la vela M30)
            "side": side,
            "size": int(quantity),
            "customTag": f"TradeAccion_{symbol or ''}",
            "stopLossBracket": {
                "ticks": int(risk_ticks),
                "type": 4 # 4 = Stop
            },
            "takeProfitBracket": {
                "ticks": int(reward_ticks),
                "type": 1 # 1 = Limit
            }
        }

        url = f"{self.base_url}/api/Order/place"
        logging.info(f"📤 ENVIANDO ORDEN REAL A TOPSTEPX ({url}): {payload}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=self.get_headers(), timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        order_id = data.get("orderId", f"TX_{action}_{int(time.time())}")
                        logging.info(f"✅ ORDEN EJECUTADA CON ÉXITO EN TOPSTEPX: OrderId={order_id}")
                        return {"status": "SUBMITTED", "orderId": str(order_id), "data": data}
                    else:
                        err_text = await resp.text()
                        logging.error(f"❌ Error al enviar orden TopstepX ({resp.status}): {err_text}")
                        return {"status": "ERROR", "errorCode": resp.status, "errorMessage": err_text}
            except Exception as e:
                logging.error(f"❌ Excepción al conectar para enviar orden: {e}")
                return {"status": "ERROR", "errorMessage": str(e)}

    async def flatten_all_positions(self, contract_id: str) -> bool:
        """Cierra la posición abierta en el contrato en el servidor de TopstepX vía POST /api/Position/closeContract."""
        account_id = await self.get_active_account_id()
        if not account_id:
            return False

        url = f"{self.base_url}/api/Position/closeContract"
        payload = {
            "accountId": int(account_id),
            "contractId": contract_id
        }
        logging.warning(f"🚨 ENVIANDO CIERRE / FLATTEN A TOPSTEPX ({contract_id}): {payload}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=self.get_headers(), timeout=15) as resp:
                    if resp.status == 200:
                        logging.info(f"✅ Posición cerrada exitosamente en TopstepX para {contract_id}")
                        return True
                    else:
                        err_text = await resp.text()
                        logging.error(f"❌ Fallo al cerrar posición en TopstepX ({resp.status}): {err_text}")
                        return False
            except Exception as e:
                logging.error(f"❌ Error de red en flatten: {e}")
                return False
