// Aplicación Frontend en Tiempo Real para el Dashboard de Trading
let socket = null;
let latestState = null;

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log("Conectado al servidor de trading TopstepX");
  };

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      latestState = data;
      updateUI(data);
    } catch (e) {
      console.error("Error al procesar paquete:", e);
    }
  };

  socket.onclose = () => {
    console.warn("WebSocket cerrado. Reintentando en 3 segundos...");
    setTimeout(connectWebSocket, 3000);
  };
}

function updateUI(state) {
  // 1. Header & Reloj
  document.getElementById("server-time-display").innerText = `Servidor: ${state.server_time}`;

  // 2. Horario & Status
  const statusPill = document.getElementById("trading-status-pill");
  const statusText = document.getElementById("trading-status-text");
  const sched = state.schedule;

  if (sched.is_trading_allowed) {
    statusPill.className = "status-pill";
    statusText.innerText = "OPERATIVA HABILITADA";
  } else {
    statusPill.className = "status-pill blocked";
    statusText.innerText = sched.status_message.replace(/_/g, " ");
  }

  // 3. Métricas Topstep
  const risk = state.risk;
  const pnlEl = document.getElementById("metric-daily-pnl");
  pnlEl.innerText = `$${risk.total_daily_pnl.toFixed(2)}`;
  pnlEl.style.color = risk.total_daily_pnl >= 0 ? "var(--color-buy)" : "var(--color-sell)";

  document.getElementById("metric-pnl-breakdown").innerText = 
    `Realizado: $${risk.daily_realized_pnl.toFixed(2)} | Flotante: $${risk.daily_unrealized_pnl.toFixed(2)}`;

  document.getElementById("metric-ct-time").innerText = 
    sched.details.ct_time ? sched.details.ct_time.split(" ")[1] + " CT" : "--:--:-- CT";

  // Contador de posiciones
  document.getElementById("count-positions").innerText = state.open_positions.length;

  // 4. Tabla Multi-Símbolo
  renderSymbolsTable(state.symbols);

  // 5. Tabla Posiciones
  renderPositionsTable(state.open_positions);

  // 6. Historial
  renderHistoryTable(state.closed_trades);

  // 7. Consola
  renderConsoleLogs(state.logs);

  // 8. Gráfico si la pestaña está activa
  if (document.getElementById("tab-chart").classList.contains("active")) {
    renderActiveChart();
  }
}

function renderSymbolsTable(symbols) {
  const tbody = document.getElementById("symbols-table-body");
  if (!symbols || Object.keys(symbols).length === 0) return;

  let html = "";
  for (const sym in symbols) {
    const s = symbols[sym];
    const vwap = s.vwap_data.vwap;
    const bandUp = s.vwap_data.upper_band_1;
    const bandLow = s.vwap_data.lower_band_1;
    const bandDist = (bandUp - vwap);
    const tpDist = (bandDist * 0.5).toFixed(2);
    const slTicks = (s.tick_size * 5).toFixed(2);

    html += `
      <tr>
        <td style="font-weight: 700; color: var(--accent-blue);">${s.symbol}</td>
        <td>${s.name}</td>
        <td style="font-weight: 600;">${s.current_price.toFixed(2)}</td>
        <td style="color: var(--accent-vwap);">${vwap.toFixed(2)}</td>
        <td style="color: var(--accent-band);">${bandUp.toFixed(2)}</td>
        <td style="color: var(--accent-band);">${bandLow.toFixed(2)}</td>
        <td style="color: var(--color-buy);">+${tpDist} pts</td>
        <td style="color: var(--color-sell);">${slTicks} pts (5t)</td>
        <td><span class="badge-tag badge-buy">Vela M15 Activa</span></td>
      </tr>
    `;
  }
  tbody.innerHTML = html;
}

function renderPositionsTable(positions) {
  const tbody = document.getElementById("positions-table-body");
  if (!positions || positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 20px; color: var(--text-muted);">Sin posiciones abiertas. Esperando rebote en VWAP.</td></tr>`;
    return;
  }

  let html = "";
  positions.forEach(p => {
    const isBuy = p.action === "BUY";
    const badgeClass = isBuy ? "badge-buy" : "badge-sell";
    html += `
      <tr>
        <td style="font-weight: 700; color: var(--accent-blue);">${p.symbol}</td>
        <td><span class="badge-tag ${badgeClass}">${p.action}</span></td>
        <td>${p.size}</td>
        <td>${p.entry_price.toFixed(2)}</td>
        <td style="color: var(--color-sell); font-weight: 600;">${p.sl.toFixed(2)}</td>
        <td style="color: var(--color-buy); font-weight: 600;">${p.tp.toFixed(2)}</td>
        <td>${p.open_time}</td>
        <td>
          <button class="btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="flattenSymbol('${p.symbol}')">Cerrar</button>
        </td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

function renderHistoryTable(trades) {
  const tbody = document.getElementById("history-table-body");
  if (!trades || trades.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 20px; color: var(--text-muted);">No hay operaciones cerradas registradas.</td></tr>`;
    return;
  }

  let html = "";
  trades.forEach(t => {
    const pnlColor = t.pnl >= 0 ? "var(--color-buy)" : "var(--color-sell)";
    html += `
      <tr>
        <td>${t.close_time}</td>
        <td style="font-weight: 700;">${t.symbol}</td>
        <td><span class="badge-tag ${t.action === 'BUY' ? 'badge-buy' : 'badge-sell'}">${t.action}</span></td>
        <td>${t.size}</td>
        <td>${t.entry_price.toFixed(2)}</td>
        <td>${t.exit_price.toFixed(2)}</td>
        <td style="color: ${pnlColor}; font-weight: 700;">$${t.pnl.toFixed(2)}</td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

function renderConsoleLogs(logs) {
  const container = document.getElementById("console-logs-container");
  if (!logs || logs.length === 0) return;

  let html = "";
  logs.forEach(log => {
    let cls = "INFO";
    if (log.includes("[WARNING]")) cls = "WARNING";
    if (log.includes("[ERROR]")) cls = "ERROR";
    html += `<div class="log-row ${cls}">${log}</div>`;
  });
  container.innerHTML = html;
}

// Visualizador de Gráfico de Velas M15, VWAP y Bandas en HTML5 Canvas
function renderActiveChart() {
  const canvas = document.getElementById("candleChartCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  // Ajustar resolución DPI
  canvas.width = canvas.parentElement.clientWidth - 32;
  canvas.height = 360;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const selectedSym = document.getElementById("chart-symbol-select").value;
  if (!latestState || !latestState.symbols || !latestState.symbols[selectedSym]) {
    ctx.fillStyle = "#64748b";
    ctx.font = "14px JetBrains Mono";
    ctx.fillText("Esperando datos de mercado para " + selectedSym + "...", 40, 180);
    return;
  }

  const sData = latestState.symbols[selectedSym];
  const candles = [];
  // Simular secuencia de velas para el gráfico a partir del snapshot
  const vwap = sData.vwap_data.vwap;
  const bandUp = sData.vwap_data.upper_band_1;
  const bandLow = sData.vwap_data.lower_band_1;
  const currP = sData.current_price;

  // Rango de precios para escalar el gráfico
  const maxP = Math.max(bandUp, currP) + (vwap * 0.003);
  const minP = Math.min(bandLow, currP) - (vwap * 0.003);
  const pRange = maxP - minP;

  function toY(price) {
    return canvas.height - ((price - minP) / pRange * (canvas.height - 60)) - 30;
  }

  // 1. Dibujar líneas de referencia (Grid)
  ctx.strokeStyle = "#1e2538";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const y = 30 + (i * ((canvas.height - 60) / 4));
    ctx.beginPath();
    ctx.moveTo(40, y);
    ctx.lineTo(canvas.width - 60, y);
    ctx.stroke();
  }

  // 2. Dibujar Línea VWAP (Cyan)
  const yVwap = toY(vwap);
  ctx.strokeStyle = "#06b6d4";
  ctx.lineWidth = 2;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(50, yVwap);
  ctx.lineTo(canvas.width - 80, yVwap);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = "#06b6d4";
  ctx.font = "11px JetBrains Mono";
  ctx.fillText(`VWAP: ${vwap.toFixed(2)}`, canvas.width - 75, yVwap + 4);

  // 3. Dibujar Bandas ±1σ (Púrpura)
  const yUp = toY(bandUp);
  const yLow = toY(bandLow);
  ctx.strokeStyle = "#818cf8";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(50, yUp);
  ctx.lineTo(canvas.width - 80, yUp);
  ctx.moveTo(50, yLow);
  ctx.lineTo(canvas.width - 80, yLow);
  ctx.stroke();

  ctx.fillStyle = "#818cf8";
  ctx.fillText(`+1σ: ${bandUp.toFixed(2)}`, canvas.width - 75, yUp + 4);
  ctx.fillText(`-1σ: ${bandLow.toFixed(2)}`, canvas.width - 75, yLow + 4);

  // 4. Dibujar Niveles de la Estrategia (TP al 50% y SL a 5 ticks)
  const tpDist = (bandUp - vwap) * 0.5;
  const tpLevel = currP + tpDist;
  const slLevel = vwap - (sData.tick_size * 5);

  const yTp = toY(tpLevel);
  const ySl = toY(slLevel);

  // Línea TP (Verde)
  ctx.strokeStyle = "#10b981";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([6, 3]);
  ctx.beginPath();
  ctx.moveTo(50, yTp);
  ctx.lineTo(canvas.width - 80, yTp);
  ctx.stroke();
  ctx.fillStyle = "#10b981";
  ctx.fillText(`TP (50%): ${tpLevel.toFixed(2)}`, canvas.width - 75, yTp + 4);

  // Línea SL (Roja)
  ctx.strokeStyle = "#f43f5e";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(50, ySl);
  ctx.lineTo(canvas.width - 80, ySl);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#f43f5e";
  ctx.fillText(`SL (5t): ${slLevel.toFixed(2)}`, canvas.width - 75, ySl + 4);

  // 5. Dibujar Precio Actual
  const yCurr = toY(currP);
  ctx.fillStyle = "#f1f5f9";
  ctx.beginPath();
  ctx.arc(canvas.width - 120, yCurr, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillText(`Spot: ${currP.toFixed(2)}`, canvas.width - 110, yCurr - 8);
}

// Navegación por pestañas
function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

  event.target.classList.add("active");
  document.getElementById(tabId).classList.add("active");

  if (tabId === "tab-chart") {
    setTimeout(renderActiveChart, 50);
  }
}

// Acciones de emergencia
async function flattenAllPositions() {
  if (confirm("¿Estás seguro de cerrar incondicionalmente todas las operaciones y cancelar órdenes?")) {
    await fetch("/api/flatten-all", { method: "POST" });
  }
}

async function flattenSymbol(symbol) {
  if (confirm(`¿Cerrar posición en ${symbol}?`)) {
    await fetch(`/api/flatten/${symbol}`, { method: "POST" });
  }
}

// Inicializar al cargar el DOM
window.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();
});
