const canvas = document.querySelector("#graph-canvas");
const panel = document.querySelector("#graph");
const context = canvas.getContext("2d");

const state = {
  topology: null,
  nodes: [],
  nodeById: new Map(),
  nets: [],
  scale: 1,
  targetScale: 1,
  x: 0,
  y: 0,
  targetX: 0,
  targetY: 0,
  dragging: false,
  pointerId: null,
  lastPointer: { x: 0, y: 0 },
  moved: false,
  selectedId: null,
  fitted: false,
  showNetLabels: false,
  visibleStates: { original: true, optimized: true },
  criticalNets: { original: new Map(), optimized: new Map() },
};

const colors = {
  ink: "#17262b",
  muted: "#7b8789",
  line: "#b6b8b1",
  blueprint: "#1d6371",
  blueprintPale: "#dce9e8",
  signal: "#d2593a",
  optimized: "#187f88",
  resizedX2: "#d89b24",
  resizedX4: "#9456a6",
  output: "#8d5e94",
  outputPale: "#eee4ef",
  paper: "#fffdf7",
};

async function initialize() {
  try {
    const response = await fetch("/api/topology", { cache: "no-store" });
    if (!response.ok) throw new Error(`Topology request failed (${response.status})`);
    const topology = await response.json();
    validateTopology(topology);
    loadTopology(topology);
    document.querySelector("#loading-state").hidden = true;
  } catch (error) {
    showLoadError(error instanceof Error ? error.message : String(error));
  }
}

function validateTopology(topology) {
  if (topology?.schema_version !== 2) throw new Error("Unsupported topology schema");
  if (!Array.isArray(topology.nodes) || !Array.isArray(topology.nets)) {
    throw new Error("Topology is missing nodes or nets");
  }
  if (!topology.states?.original || !topology.states?.optimized) {
    throw new Error("Topology is missing original or optimized analysis data");
  }
}

function loadTopology(topology) {
  state.topology = topology;
  state.nodes = layoutNodes(topology.nodes);
  state.nodeById = new Map(state.nodes.map((node) => [node.id, node]));
  state.nets = topology.nets.filter(
    (net) => state.nodeById.has(net.source) && net.targets.some((target) => state.nodeById.has(target.node)),
  );
  state.criticalNets.original = criticalNetRanks(topology.states.original.critical_paths);
  state.criticalNets.optimized = criticalNetRanks(topology.states.optimized.critical_paths);

  document.title = `${topology.circuit_name} · Topology Viewer`;
  document.querySelector("#circuit-title").textContent = topology.circuit_name;
  document.querySelector("#gate-count").textContent = topology.counts.gates;
  document.querySelector("#net-count").textContent = topology.counts.nets;
  document.querySelector("#input-count").textContent = topology.counts.primary_inputs;
  document.querySelector("#output-count").textContent = topology.counts.primary_outputs;
  fitView();
  requestFrame();
}

function layoutNodes(rawNodes) {
  const levels = new Map();
  rawNodes.forEach((node, index) => {
    const level = Number(node.level) || 0;
    if (!levels.has(level)) levels.set(level, []);
    levels.get(level).push({ ...node, order: index });
  });
  const maxRows = Math.max(...[...levels.values()].map((nodes) => nodes.length), 1);
  const verticalGap = maxRows > 20 ? 78 : 98;
  const horizontalGap = 245;
  const laidOut = [];

  [...levels.entries()].sort(([a], [b]) => a - b).forEach(([level, nodes]) => {
    const totalHeight = (nodes.length - 1) * verticalGap;
    nodes.forEach((node, row) => {
      const isGate = node.kind === "gate";
      laidOut.push({
        ...node,
        x: level * horizontalGap,
        y: row * verticalGap - totalHeight / 2,
        width: isGate ? 112 : 48,
        height: isGate ? 54 : 48,
      });
    });
  });
  return laidOut;
}

function graphBounds() {
  if (!state.nodes.length) return { left: 0, right: 1, top: 0, bottom: 1 };
  return state.nodes.reduce(
    (bounds, node) => ({
      left: Math.min(bounds.left, node.x - node.width / 2),
      right: Math.max(bounds.right, node.x + node.width / 2),
      top: Math.min(bounds.top, node.y - node.height / 2),
      bottom: Math.max(bounds.bottom, node.y + node.height / 2),
    }),
    { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity },
  );
}

function fitView() {
  if (!state.nodes.length || panel.clientWidth === 0 || panel.clientHeight === 0) return;
  const bounds = graphBounds();
  const padding = 90;
  const graphWidth = Math.max(bounds.right - bounds.left, 1);
  const graphHeight = Math.max(bounds.bottom - bounds.top, 1);
  const scale = clamp(
    Math.min((panel.clientWidth - padding * 2) / graphWidth, (panel.clientHeight - padding * 2) / graphHeight),
    0.12,
    2.2,
  );
  const centerX = (bounds.left + bounds.right) / 2;
  const centerY = (bounds.top + bounds.bottom) / 2;
  state.targetScale = scale;
  state.targetX = panel.clientWidth / 2 - centerX * scale;
  state.targetY = panel.clientHeight / 2 - centerY * scale;
  if (!state.fitted) {
    state.scale = scale;
    state.x = state.targetX;
    state.y = state.targetY;
    state.fitted = true;
  }
  updateZoomReadout();
  requestFrame();
}

function zoomAt(factor, screenX = panel.clientWidth / 2, screenY = panel.clientHeight / 2) {
  const oldScale = state.targetScale;
  const newScale = clamp(oldScale * factor, 0.08, 4.5);
  const worldX = (screenX - state.targetX) / oldScale;
  const worldY = (screenY - state.targetY) / oldScale;
  state.targetScale = newScale;
  state.targetX = screenX - worldX * newScale;
  state.targetY = screenY - worldY * newScale;
  updateZoomReadout();
  requestFrame();
}

function resizeCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, panel.clientWidth);
  const height = Math.max(1, panel.clientHeight);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  if (!state.fitted) fitView();
  requestFrame();
}

let animationFrame = null;
function requestFrame() {
  if (animationFrame === null) animationFrame = requestAnimationFrame(animate);
}

function animate() {
  animationFrame = null;
  const ease = 0.2;
  state.scale += (state.targetScale - state.scale) * ease;
  state.x += (state.targetX - state.x) * ease;
  state.y += (state.targetY - state.y) * ease;
  if (Math.abs(state.targetScale - state.scale) < 0.0001) state.scale = state.targetScale;
  if (Math.abs(state.targetX - state.x) < 0.02) state.x = state.targetX;
  if (Math.abs(state.targetY - state.y) < 0.02) state.y = state.targetY;
  draw();
  if (state.scale !== state.targetScale || state.x !== state.targetX || state.y !== state.targetY) requestFrame();
}

function draw() {
  const width = panel.clientWidth;
  const height = panel.clientHeight;
  context.clearRect(0, 0, width, height);
  if (!state.topology) return;

  context.save();
  context.translate(state.x, state.y);
  context.scale(state.scale, state.scale);
  drawWorldGrid(width, height);
  state.nets.forEach(drawNet);
  state.nodes.forEach(drawNode);
  context.restore();
}

function drawWorldGrid(width, height) {
  const step = 49;
  const left = -state.x / state.scale;
  const top = -state.y / state.scale;
  const right = left + width / state.scale;
  const bottom = top + height / state.scale;
  context.beginPath();
  context.strokeStyle = "rgba(29, 99, 113, 0.055)";
  context.lineWidth = 1 / state.scale;
  for (let x = Math.floor(left / step) * step; x <= right; x += step) {
    context.moveTo(x, top);
    context.lineTo(x, bottom);
  }
  for (let y = Math.floor(top / step) * step; y <= bottom; y += step) {
    context.moveTo(left, y);
    context.lineTo(right, y);
  }
  context.stroke();
}

function drawNet(net) {
  const source = state.nodeById.get(net.source);
  if (!source) return;
  const selected = netIsSelected(net);
  const faded = Boolean(state.selectedId) && !selected;
  const validTargets = net.targets.map((target) => state.nodeById.get(target.node)).filter(Boolean);
  validTargets.forEach((target, index) => {
    const start = nodeAnchor(source, "right");
    const end = nodeAnchor(target, "left");
    drawNetCurve(start, end, selected ? colors.ink : colors.line, selected ? 2.8 : 1.35, faded ? 0.12 : selected ? 0.85 : 0.55, 0);
    drawArrow(end.x, end.y, selected ? colors.ink : colors.line);

    const originalRank = visibleCriticalRank("original", net.name);
    const optimizedRank = visibleCriticalRank("optimized", net.name);
    const both = originalRank !== null && optimizedRank !== null;
    if (originalRank !== null) {
      drawCriticalCurve(start, end, "original", originalRank, both ? -4 : 0, faded);
    }
    if (optimizedRank !== null) {
      drawCriticalCurve(start, end, "optimized", optimizedRank, both ? 4 : 0, faded);
    }
    if ((state.showNetLabels || selected) && index === 0 && state.scale > 0.32) {
      drawNetLabel(net.name, (start.x + end.x) / 2, (start.y + end.y) / 2 - 6, selected);
    }
  });
  context.globalAlpha = 1;
}

function drawNetCurve(start, end, color, width, alpha, offset) {
  const bend = Math.max(48, (end.x - start.x) * 0.48);
  context.save();
  context.beginPath();
  context.moveTo(start.x, start.y + offset);
  context.bezierCurveTo(start.x + bend, start.y + offset, end.x - bend, end.y + offset, end.x, end.y + offset);
  context.strokeStyle = color;
  context.globalAlpha = alpha;
  context.lineWidth = width / Math.sqrt(state.scale);
  context.stroke();
  context.restore();
}

function drawCriticalCurve(start, end, stateName, rank, offset, faded) {
  const color = stateName === "original" ? colors.signal : colors.optimized;
  context.save();
  if (stateName === "original") context.setLineDash([9 / Math.sqrt(state.scale), 5 / Math.sqrt(state.scale)]);
  drawNetCurve(start, end, color, rank === 1 ? 3.5 : 2.3, faded ? 0.16 : rank === 1 ? 0.95 : 0.68, offset);
  context.restore();
}

function visibleCriticalRank(stateName, netName) {
  if (!state.visibleStates[stateName]) return null;
  return state.criticalNets[stateName].get(netName) ?? null;
}

function criticalNetRanks(paths) {
  const ranks = new Map();
  paths.forEach((path) => path.nets.forEach((netName) => {
    ranks.set(netName, Math.min(path.rank, ranks.get(netName) ?? path.rank));
  }));
  return ranks;
}

function drawArrow(x, y, color) {
  const size = 6 / Math.sqrt(state.scale);
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x - size, y - size * 0.65);
  context.lineTo(x - size, y + size * 0.65);
  context.closePath();
  context.fillStyle = color;
  context.fill();
}

function drawNetLabel(name, x, y, selected) {
  context.save();
  context.font = "600 10px 'IBM Plex Mono', monospace";
  const width = context.measureText(name).width + 10;
  context.fillStyle = selected ? colors.signal : colors.paper;
  roundedRect(x - width / 2, y - 9, width, 17, 4);
  context.fill();
  context.fillStyle = selected ? "white" : colors.muted;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(name, x, y - 1);
  context.restore();
}

function drawNode(node) {
  const selected = node.id === state.selectedId;
  const related = !state.selectedId || selected || nodeIsNeighbor(node.id);
  context.save();
  context.globalAlpha = related ? 1 : 0.26;
  if (node.kind === "gate" && showResizeDifference() && gateWasResized(node.name)) {
    drawResizeHighlight(node);
  }
  context.lineWidth = (selected ? 3.5 : 2) / Math.sqrt(state.scale);
  context.strokeStyle = selected ? colors.signal : nodeColor(node.kind);
  context.fillStyle = nodeFill(node.kind);

  if (node.kind === "gate") {
    roundedRect(node.x - node.width / 2, node.y - node.height / 2, node.width, node.height, 8);
  } else if (node.kind === "input") {
    context.beginPath();
    context.arc(node.x, node.y, node.width / 2, 0, Math.PI * 2);
  } else {
    context.beginPath();
    context.moveTo(node.x, node.y - node.height / 2);
    context.lineTo(node.x + node.width / 2, node.y);
    context.lineTo(node.x, node.y + node.height / 2);
    context.lineTo(node.x - node.width / 2, node.y);
    context.closePath();
  }
  context.fill();
  context.stroke();

  context.fillStyle = colors.ink;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.font = "700 12px 'IBM Plex Mono', monospace";
  context.fillText(node.name, node.x, node.y - (node.kind === "gate" ? 7 : 0));
  if (node.kind === "gate") {
    context.fillStyle = colors.blueprint;
    context.font = "600 9px 'IBM Plex Mono', monospace";
    context.fillText(node.gate_type, node.x, node.y + 11);
  }
  context.restore();
}

function drawResizeHighlight(node) {
  const optimizedSize = state.topology.states.optimized.gates[node.name].size;
  const isX4 = optimizedSize === "X4";
  const margin = isX4 ? 11 : 8;
  const color = isX4 ? colors.resizedX4 : colors.resizedX2;
  context.save();
  roundedRect(
    node.x - node.width / 2 - margin,
    node.y - node.height / 2 - margin,
    node.width + margin * 2,
    node.height + margin * 2,
    12,
  );
  context.fillStyle = isX4
    ? "rgba(148, 86, 166, 0.13)"
    : "rgba(216, 155, 36, 0.13)";
  context.strokeStyle = color;
  context.lineWidth = (isX4 ? 5 : 3.2) / Math.sqrt(state.scale);
  context.fill();
  context.stroke();
  context.restore();
}

function showResizeDifference() {
  return state.visibleStates.original && state.visibleStates.optimized;
}

function gateWasResized(gateName) {
  const original = state.topology.states.original.gates[gateName];
  const optimized = state.topology.states.optimized.gates[gateName];
  return Boolean(original && optimized && original.size !== optimized.size);
}

function roundedRect(x, y, width, height, radius) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
}

function nodeAnchor(node, side) {
  const halfWidth = node.width / 2;
  return { x: node.x + (side === "right" ? halfWidth : -halfWidth), y: node.y };
}

function nodeColor(kind) {
  if (kind === "input") return colors.blueprint;
  if (kind === "output") return colors.output;
  return colors.ink;
}

function nodeFill(kind) {
  if (kind === "input") return colors.blueprintPale;
  if (kind === "output") return colors.outputPale;
  return colors.paper;
}

function netIsSelected(net) {
  return Boolean(state.selectedId) && (
    net.source === state.selectedId || net.targets.some((target) => target.node === state.selectedId)
  );
}

function nodeIsNeighbor(nodeId) {
  return state.nets.some((net) => {
    if (!netIsSelected(net)) return false;
    return net.source === nodeId || net.targets.some((target) => target.node === nodeId);
  });
}

function screenToWorld(screenX, screenY) {
  return { x: (screenX - state.x) / state.scale, y: (screenY - state.y) / state.scale };
}

function hitNode(screenX, screenY) {
  const point = screenToWorld(screenX, screenY);
  return [...state.nodes].reverse().find((node) => (
    Math.abs(point.x - node.x) <= node.width / 2 + 5 / state.scale
    && Math.abs(point.y - node.y) <= node.height / 2 + 5 / state.scale
  )) || null;
}

function selectNode(node) {
  state.selectedId = node?.id ?? null;
  const empty = document.querySelector("#empty-selection");
  const details = document.querySelector("#selection-details");
  empty.hidden = Boolean(node);
  details.hidden = !node;
  if (!node) {
    requestFrame();
    return;
  }
  document.querySelector("#selection-kind").textContent = node.kind === "gate" ? "Logic gate" : `Primary ${node.kind}`;
  document.querySelector("#selection-name").textContent = node.name;
  const type = document.querySelector("#selection-type");
  type.textContent = node.kind === "gate" ? node.gate_type : "Circuit boundary";
  renderGateAnalysis(node);
  renderNetList("#incoming-nets", state.nets.filter((net) => net.targets.some((target) => target.node === node.id)));
  renderNetList("#outgoing-nets", state.nets.filter((net) => net.source === node.id));
  requestFrame();
}

function renderGateAnalysis(node) {
  const container = document.querySelector("#gate-analysis");
  container.hidden = node.kind !== "gate";
  if (node.kind !== "gate") return;
  renderAnalysisCard("original", node.name);
  renderAnalysisCard("optimized", node.name);
}

function renderAnalysisCard(stateName, gateName) {
  const card = document.querySelector(`#${stateName}-analysis`);
  card.hidden = !state.visibleStates[stateName];
  if (card.hidden) return;
  const analysisState = state.topology.states[stateName];
  const metrics = analysisState.gates[gateName];
  const capUnit = state.topology.units.capacitance;
  const timeUnit = state.topology.units.time;
  const pathRanks = analysisState.critical_paths
    .filter((path) => path.gates.includes(gateName))
    .map((path) => `#${path.rank} (${Number(path.slack).toPrecision(4)} ${timeUnit})`)
    .join(", ");
  card.querySelector('[data-field="cell"]').textContent = metrics.cell;
  card.querySelector('[data-field="size"]').textContent = metrics.size;
  card.querySelector('[data-field="load"]').textContent = formatMetric(metrics.load_capacitance, capUnit);
  card.querySelector('[data-field="rise"]').textContent = formatMetric(metrics.delay_rise, timeUnit);
  card.querySelector('[data-field="fall"]').textContent = formatMetric(metrics.delay_fall, timeUnit);
  card.querySelector('[data-field="paths"]').textContent = pathRanks || "None";
}

function formatMetric(value, unit) {
  return `${Number(value).toPrecision(6)} ${unit}`;
}

function renderNetList(selector, nets) {
  const list = document.querySelector(selector);
  list.replaceChildren();
  if (!nets.length) {
    const item = document.createElement("li");
    item.className = "none";
    item.textContent = "None";
    list.append(item);
    return;
  }
  nets.forEach((net) => {
    const item = document.createElement("li");
    item.textContent = net.name;
    list.append(item);
  });
}

function localPointer(event) {
  const bounds = panel.getBoundingClientRect();
  return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
}

panel.addEventListener("wheel", (event) => {
  event.preventDefault();
  const point = localPointer(event);
  zoomAt(Math.exp(-event.deltaY * 0.0012), point.x, point.y);
}, { passive: false });

panel.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  const point = localPointer(event);
  state.dragging = true;
  state.pointerId = event.pointerId;
  state.lastPointer = point;
  state.moved = false;
  panel.classList.add("dragging");
  panel.setPointerCapture(event.pointerId);
});

panel.addEventListener("pointermove", (event) => {
  if (!state.dragging || event.pointerId !== state.pointerId) return;
  const point = localPointer(event);
  const dx = point.x - state.lastPointer.x;
  const dy = point.y - state.lastPointer.y;
  if (Math.abs(dx) + Math.abs(dy) > 2) state.moved = true;
  state.x += dx;
  state.y += dy;
  state.targetX = state.x;
  state.targetY = state.y;
  state.lastPointer = point;
  requestFrame();
});

panel.addEventListener("pointerup", (event) => {
  if (event.pointerId !== state.pointerId) return;
  const point = localPointer(event);
  if (!state.moved) selectNode(hitNode(point.x, point.y));
  state.dragging = false;
  state.pointerId = null;
  panel.classList.remove("dragging");
  panel.releasePointerCapture(event.pointerId);
});

panel.addEventListener("keydown", (event) => {
  if (event.key === "+" || event.key === "=") zoomAt(1.2);
  else if (event.key === "-") zoomAt(1 / 1.2);
  else if (event.key === "0" || event.key.toLowerCase() === "f") fitView();
  else if (event.key === "Escape") selectNode(null);
  else return;
  event.preventDefault();
});

document.querySelector("#zoom-in").addEventListener("click", () => zoomAt(1.25));
document.querySelector("#zoom-out").addEventListener("click", () => zoomAt(1 / 1.25));
document.querySelector("#fit-view").addEventListener("click", fitView);
document.querySelector("#show-net-labels").addEventListener("change", (event) => {
  state.showNetLabels = event.target.checked;
  requestFrame();
});

for (const stateName of ["original", "optimized"]) {
  document.querySelector(`#show-${stateName}`).addEventListener("change", (event) => {
    state.visibleStates[stateName] = event.target.checked;
    const selected = state.nodeById.get(state.selectedId);
    if (selected) renderGateAnalysis(selected);
    requestFrame();
  });
}

function updateZoomReadout() {
  document.querySelector("#zoom-readout").textContent = `${Math.round(state.targetScale * 100)}%`;
}

function showLoadError(message) {
  const loading = document.querySelector("#loading-state");
  loading.textContent = message;
  loading.style.color = colors.signal;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

new ResizeObserver(resizeCanvas).observe(panel);
initialize();
