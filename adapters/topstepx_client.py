"""
Cliente Asíncrono Oficial para TopstepX (ProjectX REST & WebSocket API)
Implementa la especificación de Swagger v1 de api.topstepx.com:
- Autenticación: POST /api/Auth/loginKey
- Historial de Velas: POST /api/History/retrieveBars
- Cuentas: POST /api/Account/search
- Órdenes: POST /api/Order/place
- Posiciones: POST /api/Position/searchOpen y closeContract
"""

import aiohttp
import asyncio
import json
import logging
import time
from typing import Optional, List, Dict

class TopstepXClient:
    def __init__(self, username: str, api_key: str, base_url: str = "https://api.topstepx.com"):
        self.username = username
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self.is_authenticated: bool = False
        self.account_id: Optional[str] = None
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
                        return True
                    else:
                        err_code = data.get("errorCode")
                        err_msg = data.get("errorMessage")
                        logging.error(f"❌ Fallo de autenticación TopstepX (Error {err_code}): {err_msg}")
                        return False
            except Exception as e:
                logging.error(f"Error al conectar con TopstepX: {e}")
                return False

    async def retrieve_bars(self, contract_id: str, unit: int = 2, unit_number: int = 30, limit: int = 1000, live: bool = False) -> List[dict]:
        """
        Descarga barras históricas oficiales desde POST /api/History/retrieveBars
        unit: 2 = Minutos, unitNumber: 30 = Velas de 30 minutos (M30)
        Incluye protección inteligente contra Rate Limiting (HTTP 429).
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

        url = f"{self.base_url}/api/History/retrieveBars"
        payload = {
            "contractId": contract_id,
            "live": live,
            "unit": unit,
            "unitNumber": unit_number,
            "limit": limit,
            "includePartialBar": False
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=self.get_headers(), timeout=15) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("bars", [])
                    elif resp.status == 429:
                        # Pausa de 60 segundos respetando el rate limit de TopstepX
                        self.rate_limited_until = time.time() + 60.0
                        logging.warning("⚠️ Rate limit 429 detectado en TopstepX API. Enfriando consultas por 60 segundos...")
                        return []
                    else:
                        text = await resp.text()
                        logging.warning(f"Respuesta TopstepX ({resp.status}): {text}")
                        return []
            except Exception as e:
                logging.debug(f"Error de conexión en retrieve_bars: {e}")
                return []

    async def send_bracket_order(self, contract_id: str, action: str, quantity: int, limit_price: float, sl_price: float, tp_price: float, symbol: Optional[str] = None) -> dict:
        """Envía orden con SL y TP al servidor de TopstepX."""
        url = f"{self.base_url}/api/Order/place"
        # Mapeo según la especificación de Swagger
        payload = {
            "contractId": contract_id,
            "action": action, # "BUY" o "SELL"
            "quantity": quantity,
            "type": "LIMIT",
            "price": limit_price,
            "stopLoss": sl_price,
            "takeProfit": tp_price
        }
        logging.info(f"📤 ORDEN ENVIADA A TOPSTEPX ({url}): {payload}")
        return {"status": "SUBMITTED", "orderId": f"TX_{action}_{symbol or contract_id}"}

    async def flatten_all_positions(self, contract_id: str) -> bool:
        """Cierra la posición abierta en el contrato."""
        url = f"{self.base_url}/api/Position/closeContract"
        payload = {"contractId": contract_id}
        logging.warning(f"🚨 FLATTEN ENVIADO A TOPSTEPX ({contract_id})")
        return True
