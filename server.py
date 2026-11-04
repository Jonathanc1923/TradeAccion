"""
Servidor Web de Trading Asíncrono (FastAPI + WebSockets)
Listo para producción en servidor / VPS.
Ejecuta el MasterTradingEngine en segundo plano y transmite datos en tiempo real al panel web.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from config import SERVER_HOST, SERVER_PORT
from core.engine import MasterTradingEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Instancia global del motor de trading
trading_engine = MasterTradingEngine()

# Gestor de conexiones WebSocket de clientes web
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast_json(self, data: dict):
        if not self.active_connections:
            return
        dead_connections = set()
        msg_str = json.dumps(data)
        for conn in self.active_connections:
            try:
                await conn.send_text(msg_str)
            except Exception:
                dead_connections.add(conn)
        for dead in dead_connections:
            self.active_connections.discard(dead)

manager = ConnectionManager()

# Callback para emitir instantáneamente a la UI cuando cambie el estado
async def push_update_to_ui():
    snapshot = trading_engine.get_full_state_snapshot()
    await manager.broadcast_json(snapshot)

trading_engine.ui_broadcast_callback = push_update_to_ui

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicio: Lanzar el bucle de trading en segundo plano
    logging.info("Iniciando tarea de fondo del Motor de Trading...")
    trading_task = asyncio.create_task(trading_engine.run_loop())
    yield
    # Parada: Detener motor
    trading_engine.is_running = False
    trading_task.cancel()
    logging.info("Servidor detenido limpiamente.")

app = FastAPI(title="Topstep VWAP M30 Bounce Engine", lifespan=lifespan)

# Archivos estáticos y plantillas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "ui", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "ui", "templates"))

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Página principal del panel institucional."""
    return templates.TemplateResponse(request=request, name="index.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Canal WebSocket para streaming en tiempo real sin recarga."""
    await manager.connect(websocket)
    try:
        # Enviar estado inicial inmediato al conectar
        initial_state = trading_engine.get_full_state_snapshot()
        await websocket.send_text(json.dumps(initial_state))
        while True:
            # Mantener la conexión abierta en espera de mensajes
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

@app.get("/api/status")
async def get_status():
    """Endpoint REST para comprobar estado del servidor."""
    return trading_engine.get_full_state_snapshot()

@app.post("/api/flatten-all")
async def flatten_all():
    """Botón de pánico: Cierra todas las posiciones abiertas inmediatamente."""
    logging.warning("⚠️ FLATTEN ALL solicitado desde la interfaz web.")
    for sym in list(trading_engine.risk_guardian.open_positions.keys()):
        await trading_engine.flatten_symbol(sym, reason="Manual desde Web UI")
    await push_update_to_ui()
    return {"status": "SUCCESS", "message": "Todas las posiciones liquidadas"}

@app.post("/api/flatten/{symbol}")
async def flatten_single(symbol: str):
    """Cierra la posición de un símbolo específico."""
    await trading_engine.flatten_symbol(symbol, reason="Manual Web")
    await push_update_to_ui()
    return {"status": "SUCCESS", "symbol": symbol}

if __name__ == "__main__":
    logging.info(f"Iniciando Servidor Web en http://{SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run("server:app", host=SERVER_HOST, port=SERVER_PORT, log_level="info", reload=False)
