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
        <td><span class="badge-tag badge-buy">Vela M30 Activa</span></td>
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

// Visualizador de Gráfico de Velas M30, Session VWAP y Bandas ±1σ en HTML5 Canvas
function renderActiveChart() {
  const canvas = document.getElementById("candleChartCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  // Ajustar resolución DPI
  canvas.width = canvas.parentElement.clientWidth - 32;
  canvas.height = 420;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const selectedSym = document.getElementById("chart-symbol-select").value;
  if (!latestState || !latestState.symbols || !latestState.symbols[selectedSym]) {
    ctx.fillStyle = "#64748b";
    ctx.font = "14px JetBrains Mono";
    ctx.fillText("Esperando datos de mercado para " + selectedSym + "...", 40, 180);
    return;
  }

  const sData = latestState.symbols[selectedSym];
  const candles = sData.candles || [];

  if (candles.length === 0) {
    ctx.fillStyle = "#64748b";
    ctx.font = "14px JetBrains Mono";
    ctx.fillText("Cargando historial de velas M30 para " + selectedSym + "...", 40, 180);
    return;
  }

  const vwap = sData.vwap_data.vwap;
  const bandUp = sData.vwap_data.upper_band_1;
  const bandLow = sData.vwap_data.lower_band_1;
  const currP = sData.current_price;

  // Calcular rango min / max exacto
  let minP = Infinity;
  let maxP = -Infinity;
  candles.forEach(c => {
    if (c.low < minP) minP = c.low;
    if (c.high > maxP) maxP = c.high;
    if (c.lower && c.lower < minP) minP = c.lower;
    if (c.upper && c.upper > maxP) maxP = c.upper;
  });

  if (currP > maxP) maxP = currP;
  if (currP < minP) minP = currP;

  const pRange = (maxP - minP) || 1.0;
  minP -= pRange * 0.05;
  maxP += pRange * 0.05;
  const totalRange = maxP - minP;

  const chartLeft = 50;
  const chartRight = canvas.width - 95;
  const chartWidth = chartRight - chartLeft;
  const chartTop = 45;
  const chartBottom = canvas.height - 35;
  const chartHeight = chartBottom - chartTop;

  function toY(price) {
    return chartBottom - ((price - minP) / totalRange * chartHeight);
  }

  function toX(idx) {
    return chartLeft + (idx + 0.5) * (chartWidth / candles.length);
  }

  // 1. Grid y Etiquetas de Precio en el Eje Derecho
  ctx.strokeStyle = "#1e2538";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#64748b";
  ctx.font = "11px JetBrains Mono";

  const numGridLines = 6;
  for (let i = 0; i <= numGridLines; i++) {
    const p = minP + (i * (totalRange / numGridLines));
    const y = toY(p);
    ctx.beginPath();
    ctx.moveTo(chartLeft, y);
    ctx.lineTo(chartRight, y);
    ctx.stroke();

    ctx.fillText(p.toFixed(2), chartRight + 8, y + 4);
  }

  // 2. Curvas continuas de Bandas ±1σ (Púrpura)
  ctx.strokeStyle = "#a855f7";
  ctx.lineWidth = 1.2;
  ctx.setLineDash([4, 4]);

  // Banda Superior
  ctx.beginPath();
  candles.forEach((c, i) => {
    const x = toX(i);
    const y = toY(c.upper);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Banda Inferior
  ctx.beginPath();
  candles.forEach((c, i) => {
    const x = toX(i);
    const y = toY(c.lower);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.setLineDash([]);

  // 3. Curva continua de Session VWAP (Cyan)
  ctx.strokeStyle = "#06b6d4";
  ctx.lineWidth = 2.0;
  ctx.beginPath();
  candles.forEach((c, i) => {
    const x = toX(i);
    const y = toY(c.vwap);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // 4. Dibujar Velas Japonesas M30 Reales (Mechas y Cuerpos)
  const candleW = Math.max(3, Math.min(16, (chartWidth / candles.length) * 0.65));

  candles.forEach((c, i) => {
    const x = toX(i);
    const isBull = c.close >= c.open;
    const color = isBull ? "#10b981" : "#f43f5e";

    // Mecha (High a Low)
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(x, toY(c.high));
    ctx.lineTo(x, toY(c.low));
    ctx.stroke();

    // Cuerpo (Open a Close)
    const yTop = toY(Math.max(c.open, c.close));
    const yBottom = toY(Math.min(c.open, c.close));
    const bodyHeight = Math.max(2, yBottom - yTop);

    ctx.fillStyle = color;
    ctx.fillRect(x - candleW / 2, yTop, candleW, bodyHeight);
  });

  // 5. Etiquetas de Tiempo (Eje X)
  ctx.fillStyle = "#64748b";
  ctx.font = "10px JetBrains Mono";
  const stepTime = Math.max(1, Math.floor(candles.length / 7));
  candles.forEach((c, i) => {
    if (i % stepTime === 0 || i === candles.length - 1) {
      const x = toX(i);
      ctx.fillText(c.time, x - 20, chartBottom + 18);
    }
  });

  // 6. Niveles de Estrategia Activa (SL 5 ticks VWAP y TP 50% Banda)
  const tpDist = (bandUp - vwap) * 0.5;
  const tpLevel = currP + tpDist;
  const slLevel = vwap - (sData.tick_size * 5);

  const yTp = toY(tpLevel);
  const ySl = toY(slLevel);

  // Línea TP (Verde Discontinua)
  ctx.strokeStyle = "#10b981";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 4]);
  ctx.beginPath();
  ctx.moveTo(chartLeft, yTp);
  ctx.lineTo(chartRight, yTp);
  ctx.stroke();
  ctx.fillStyle = "#10b981";
  ctx.fillText(`TP: ${tpLevel.toFixed(2)}`, chartRight + 8, yTp + 4);

  // Línea SL (Roja Discontinua)
  ctx.strokeStyle = "#f43f5e";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(chartLeft, ySl);
  ctx.lineTo(chartRight, ySl);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#f43f5e";
  ctx.fillText(`SL: ${slLevel.toFixed(2)}`, chartRight + 8, ySl + 4);

  // 7. Encabezado con Información en Tiempo Real de la Sesión
  ctx.fillStyle = "#f8fafc";
  ctx.font = "bold 13px JetBrains Mono";
  ctx.fillText(
    `${selectedSym} (M30) | Spot: ${currP.toFixed(2)} | VWAP Sesión: ${vwap.toFixed(2)} | +1σ: ${bandUp.toFixed(2)} | -1σ: ${bandLow.toFixed(2)}`,
    chartLeft, 24
  );
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
