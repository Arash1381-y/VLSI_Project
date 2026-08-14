const canvas = document.querySelector("#graph-canvas");
const panel = document.querySelector("#graph");
const context = canvas.getContext("2d");
const workspace = document.querySelector(".workspace");
const inspector = document.querySelector(".inspector");
const inspectorResizer = document.querySelector("#inspector-resizer");

const defaultInspectorWidth = 414;
const minimumInspectorWidth = 340;
const minimumGraphWidth = 360;

const state = {
  topology: null,
  nodes: [],
  analysisNodes: [],
  schematicNodes: [],
  nodeById: new Map(),
  nets: [],
  schematicRoutes: new Map(),
  gateSymbols: new Map(),
  viewMode: "analysis",
  schematicReady: false,
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
  focusedNodeIds: new Set(),
  focusedNetNames: new Set(),
  inspectorWidth: defaultInspectorWidth,
  resizingInspector: false,
  inspectorPointerId: null,
};

const gateFamilies = [
  "AND2", "AND3", "BUF", "INV", "NAND2", "NAND3",
  "NOR2", "NOR3", "OR2", "OR3", "XNOR2", "XOR2",
];

const inputPinFractions = {
  1: [0.5],
  2: [0.32, 0.68],
  3: [0.24, 0.5, 0.76],
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
    if (topology === null) {
      showDirectoryPrompt();
    } else {
      await openTopology(topology);
    }
  } catch (error) {
    showLoadError(error instanceof Error ? error.message : String(error));
  }
}

async function openTopology(topology) {
  validateTopology(topology);
  loadTopology(topology);
  document.querySelector("#loading-state").hidden = true;
  await prepareSchematicView(topology);
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
  state.schematicReady = false;
  state.schematicNodes = [];
  state.schematicRoutes = new Map();
  state.viewMode = "analysis";
  state.analysisNodes = layoutNodes(topology.nodes);
  state.nodes = state.analysisNodes;
  state.nodeById = new Map(state.nodes.map((node) => [node.id, node]));
  state.nets = topology.nets.filter(
    (net) => state.nodeById.has(net.source) && net.targets.some((target) => state.nodeById.has(target.node)),
  );
  state.criticalNets.original = criticalNetRanks(topology.states.original.critical_paths);
  state.criticalNets.optimized = criticalNetRanks(topology.states.optimized.critical_paths);
  renderCircuitSummary();
  selectNode(null);
  document.querySelector('[data-view="schematic"]').disabled = true;
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === "analysis";
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });

  document.title = `${topology.circuit_name} · Topology Viewer`;
  document.querySelector("#circuit-title").textContent = topology.circuit_name;
  document.querySelector("#gate-count").textContent = topology.counts.gates;
  document.querySelector("#net-count").textContent = topology.counts.nets;
  document.querySelector("#input-count").textContent = topology.counts.primary_inputs;
  document.querySelector("#output-count").textContent = topology.counts.primary_outputs;
  fitView();
  requestFrame();
}

async function prepareSchematicView(topology) {
  const status = document.querySelector("#layout-status");
  status.hidden = false;
  status.textContent = "Preparing pin-aware schematic layout…";
  try {
    const [layout] = await Promise.all([
      buildSchematicLayout(topology),
      preloadGateSymbols(),
    ]);
    if (state.topology !== topology) return;
    state.schematicNodes = layout.nodes;
    state.schematicRoutes = layout.routes;
    state.schematicReady = true;
    const button = document.querySelector('[data-view="schematic"]');
    button.disabled = false;
    status.hidden = true;
    const requestedView = new URLSearchParams(window.location.search).get("view");
    if (requestedView === "schematic" || state.viewMode === "schematic") {
      activateView("schematic");
    }
  } catch (error) {
    if (state.topology !== topology) return;
    status.textContent = `Schematic layout unavailable: ${error instanceof Error ? error.message : String(error)}`;
    status.style.color = colors.signal;
  }
}

async function preloadGateSymbols() {
  if (state.gateSymbols.size === gateFamilies.length) return;
  await Promise.all(gateFamilies.map((family) => new Promise((resolve, reject) => {
    if (state.gateSymbols.has(family)) {
      resolve();
      return;
    }
    const image = new Image();
    image.addEventListener("load", () => {
      state.gateSymbols.set(family, image);
      resolve();
    }, { once: true });
    image.addEventListener("error", () => reject(new Error(`cannot load ${family} gate symbol`)), { once: true });
    image.src = `/gate-symbols/${family}.svg`;
  })));
}

function showDirectoryPrompt() {
  state.topology = null;
  state.nodes = [];
  state.nodeById = new Map();
  state.nets = [];
  const loading = document.querySelector("#loading-state");
  loading.hidden = false;
  loading.replaceChildren();
  const message = document.createElement("span");
  message.textContent = "Choose an experiment output directory with the Dir button";
  loading.append(message);
}

async function chooseOutputDirectory() {
  if (typeof window.showDirectoryPicker !== "function") {
    document.querySelector("#directory-picker").click();
    return;
  }
  try {
    const directory = await window.showDirectoryPicker({ mode: "read" });
    const topologyHandle = await directory.getFileHandle("circuit_topology.json");
    await openTopologyFile(await topologyHandle.getFile());
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    showLoadError(error instanceof Error ? error.message : String(error));
  }
}

async function openTopologyFile(file) {
  const loading = document.querySelector("#loading-state");
  loading.hidden = false;
  loading.textContent = `Reading ${file.name}`;
  try {
    const topology = JSON.parse(await file.text());
    await openTopology(topology);
  } catch (error) {
    showLoadError(error instanceof Error ? error.message : String(error));
  }
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

async function buildSchematicLayout(topology) {
  if (typeof globalThis.ELK !== "function") {
    throw new Error("ELK layout engine did not load");
  }
  const rawById = new Map(topology.nodes.map((node) => [node.id, node]));
  const inputCounts = schematicInputCounts(topology);
  const children = topology.nodes.map((node) => schematicElkNode(node, inputCounts.get(node.id) ?? 0, topology));
  const edgeNetNames = new Map();
  const edges = topology.nets.flatMap((net, netIndex) => net.targets.map((target, targetIndex) => {
    const id = `edge:${netIndex}:${targetIndex}`;
    edgeNetNames.set(id, net.name);
    return {
      id,
      sources: [`${net.source}:out`],
      targets: [target.pin === null ? `${target.node}:in` : `${target.node}:in:${target.pin}`],
    };
  }));
  const graph = {
    id: "circuit-schematic",
    layoutOptions: {
      "org.eclipse.elk.algorithm": "layered",
      "org.eclipse.elk.direction": "RIGHT",
      "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
      "org.eclipse.elk.spacing.nodeNode": "52",
      "org.eclipse.elk.layered.spacing.nodeNodeBetweenLayers": "105",
      "org.eclipse.elk.layered.spacing.edgeNodeBetweenLayers": "36",
      "org.eclipse.elk.layered.spacing.edgeEdgeBetweenLayers": "18",
      "org.eclipse.elk.layered.layering.strategy": "LONGEST_PATH",
      "org.eclipse.elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "org.eclipse.elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "org.eclipse.elk.layered.mergeEdges": "true",
      "org.eclipse.elk.layered.unnecessaryBendpoints": "true",
      "org.eclipse.elk.separateConnectedComponents": "false",
      "org.eclipse.elk.aspectRatio": "1.8",
    },
    children,
    edges,
  };
  const layout = await new globalThis.ELK().layout(graph);
  const nodes = (layout.children ?? []).map((child) => {
    const raw = rawById.get(child.id);
    if (!raw) throw new Error(`layout returned unknown node ${child.id}`);
    return {
      ...raw,
      x: Number(child.x) + Number(child.width) / 2,
      y: Number(child.y) + Number(child.height) / 2,
      width: Number(child.width),
      height: Number(child.height),
      schematic: true,
    };
  });
  const routes = new Map();
  (layout.edges ?? []).forEach((edge) => {
    const netName = edgeNetNames.get(edge.id);
    if (!netName) return;
    const route = routes.get(netName) ?? { sections: [], junctionPoints: [] };
    route.sections.push(...(edge.sections ?? []));
    route.junctionPoints.push(...(edge.junctionPoints ?? []));
    routes.set(netName, route);
  });
  return { nodes, routes };
}

function schematicInputCounts(topology) {
  const counts = new Map();
  topology.nets.forEach((net) => net.targets.forEach((target) => {
    if (target.pin !== null) {
      counts.set(target.node, Math.max(counts.get(target.node) ?? 0, Number(target.pin) + 1));
    }
  }));
  return counts;
}

function schematicElkNode(node, inputCount, topology) {
  if (node.kind === "input") {
    return schematicBoundaryNode(node, "out");
  }
  if (node.kind === "output") {
    return schematicBoundaryNode(node, "in");
  }

  const factor = schematicGateScale(node.name, topology);
  const width = 92 * factor;
  const height = 70 * factor;
  const fractions = inputPinFractions[inputCount];
  if (!fractions) throw new Error(`unsupported ${inputCount}-input gate ${node.name}`);
  const ports = fractions.map((fraction, pin) => ({
    id: `${node.id}:in:${pin}`,
    width: 0,
    height: 0,
    x: 0,
    y: height * fraction,
  }));
  ports.push({ id: `${node.id}:out`, width: 0, height: 0, x: width, y: height / 2 });
  return {
    id: node.id,
    width,
    height,
    ports,
    layoutOptions: { "org.eclipse.elk.portConstraints": "FIXED_POS" },
  };
}

function schematicBoundaryNode(node, portDirection) {
  const size = 32;
  return {
    id: node.id,
    width: size,
    height: size,
    ports: [{
      id: `${node.id}:${portDirection}`,
      width: 0,
      height: 0,
      x: portDirection === "out" ? size : 0,
      y: size / 2,
    }],
    layoutOptions: { "org.eclipse.elk.portConstraints": "FIXED_POS" },
  };
}

function schematicGateScale(gateName, topology) {
  const original = topology.states.original.gates[gateName]?.size;
  const optimized = topology.states.optimized.gates[gateName]?.size;
  return Math.max(gateSizeScale(original), gateSizeScale(optimized));
}

function gateSizeScale(size) {
  if (size === "X4") return 1.28;
  if (size === "X2") return 1.13;
  return 1;
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
  if (state.viewMode === "schematic") {
    drawSchematicNet(net);
    return;
  }
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

function drawSchematicNet(net) {
  const route = state.schematicRoutes.get(net.name);
  if (!route) return;
  const selected = netIsSelected(net);
  const faded = Boolean(state.selectedId) && !selected;
  const sections = route.sections.map((section) => [
    section.startPoint,
    ...(section.bendPoints ?? []),
    section.endPoint,
  ]);

  sections.forEach((points) => drawOrthogonalLine(
    points,
    selected ? colors.ink : colors.line,
    selected ? 3 : 1.45,
    faded ? 0.1 : selected ? 0.9 : 0.62,
  ));

  const originalRank = visibleCriticalRank("original", net.name);
  const optimizedRank = visibleCriticalRank("optimized", net.name);
  if (originalRank !== null) {
    sections.forEach((points) => drawOrthogonalLine(
      points,
      colors.signal,
      originalRank === 1 ? 4.8 : 3.2,
      faded ? 0.15 : originalRank === 1 ? 0.92 : 0.66,
      [9, 6],
    ));
  }
  if (optimizedRank !== null) {
    sections.forEach((points) => drawOrthogonalLine(
      points,
      colors.optimized,
      optimizedRank === 1 ? 3.1 : 2.2,
      faded ? 0.15 : optimizedRank === 1 ? 0.96 : 0.72,
    ));
  }

  context.save();
  context.fillStyle = selected ? colors.ink : colors.line;
  context.globalAlpha = faded ? 0.1 : 0.85;
  schematicJunctionPoints(route, sections).forEach((point) => {
    context.beginPath();
    context.arc(point.x, point.y, 3.1 / Math.sqrt(state.scale), 0, Math.PI * 2);
    context.fill();
  });
  context.restore();

  if ((state.showNetLabels || selected) && state.scale > 0.24) {
    const labelPoint = schematicLabelPoint(sections);
    if (labelPoint) drawNetLabel(net.name, labelPoint.x, labelPoint.y - 7, selected);
  }
}

function schematicJunctionPoints(route, sections) {
  const points = new Map();
  const connect = (point, neighbor) => {
    const key = `${point.x.toFixed(3)},${point.y.toFixed(3)}`;
    const neighborKey = `${neighbor.x.toFixed(3)},${neighbor.y.toFixed(3)}`;
    const record = points.get(key) ?? { point, neighbors: new Set() };
    record.neighbors.add(neighborKey);
    points.set(key, record);
  };
  sections.forEach((section) => {
    for (let index = 1; index < section.length; index += 1) {
      connect(section[index - 1], section[index]);
      connect(section[index], section[index - 1]);
    }
  });
  const derived = [...points.values()]
    .filter((record) => record.neighbors.size >= 3)
    .map((record) => record.point);
  const combined = [...route.junctionPoints, ...derived];
  const unique = new Map(combined.map((point) => [
    `${point.x.toFixed(3)},${point.y.toFixed(3)}`,
    point,
  ]));
  return [...unique.values()];
}

function drawOrthogonalLine(points, color, width, alpha, dash = []) {
  if (points.length < 2) return;
  context.save();
  context.beginPath();
  context.moveTo(points[0].x, points[0].y);
  points.slice(1).forEach((point) => context.lineTo(point.x, point.y));
  context.strokeStyle = color;
  context.globalAlpha = alpha;
  context.lineWidth = width / Math.sqrt(state.scale);
  context.lineCap = "round";
  context.lineJoin = "round";
  context.setLineDash(dash.map((value) => value / Math.sqrt(state.scale)));
  context.stroke();
  context.restore();
}

function schematicLabelPoint(sections) {
  let best = null;
  let bestLength = -1;
  sections.forEach((points) => {
    for (let index = 1; index < points.length; index += 1) {
      const start = points[index - 1];
      const end = points[index];
      const length = Math.abs(end.x - start.x) + Math.abs(end.y - start.y);
      if (length > bestLength) {
        bestLength = length;
        best = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
      }
    }
  });
  return best;
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
  const related = !state.selectedId || state.focusedNodeIds.has(node.id);
  context.save();
  context.globalAlpha = related ? 1 : 0.06;
  if (node.kind === "gate" && showResizeDifference() && gateWasResized(node.name)) {
    drawResizeHighlight(node);
  }
  if (node.kind === "gate" && state.viewMode === "schematic") {
    drawSchematicGateNode(node, selected);
    context.restore();
    return;
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

function drawSchematicGateNode(node, selected) {
  const symbol = state.gateSymbols.get(node.gate_type);
  if (selected) {
    context.save();
    roundedRect(
      node.x - node.width / 2 - 8,
      node.y - node.height / 2 - 8,
      node.width + 16,
      node.height + 16,
      12,
    );
    context.strokeStyle = colors.signal;
    context.lineWidth = 3 / Math.sqrt(state.scale);
    context.setLineDash([7 / Math.sqrt(state.scale), 4 / Math.sqrt(state.scale)]);
    context.stroke();
    context.restore();
  }

  if (symbol) {
    context.drawImage(
      symbol,
      node.x - node.width / 2,
      node.y - node.height / 2,
      node.width,
      node.height,
    );
  } else {
    context.save();
    roundedRect(node.x - node.width / 2, node.y - node.height / 2, node.width, node.height, 8);
    context.fillStyle = colors.paper;
    context.strokeStyle = colors.ink;
    context.lineWidth = 2 / Math.sqrt(state.scale);
    context.fill();
    context.stroke();
    context.restore();
  }

  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillStyle = colors.ink;
  context.font = "700 12px 'IBM Plex Mono', monospace";
  drawTextPlate(node.name, node.x, node.y - node.height / 2 - 11);
  context.fillStyle = colors.blueprint;
  context.font = "700 9px 'IBM Plex Mono', monospace";
  drawTextPlate(schematicGateCaption(node.name), node.x, node.y + node.height / 2 + 11);
}

function schematicGateCaption(gateName) {
  const original = state.topology.states.original.gates[gateName];
  const optimized = state.topology.states.optimized.gates[gateName];
  if (state.visibleStates.original && !state.visibleStates.optimized) return original.cell;
  if (!state.visibleStates.original && state.visibleStates.optimized) return optimized.cell;
  if (original.cell === optimized.cell) return original.cell;
  return `${original.cell} → ${optimized.cell}`;
}

function drawTextPlate(text, x, y) {
  const previousFill = context.fillStyle;
  const width = context.measureText(text).width + 8;
  context.fillStyle = "rgba(255, 253, 247, 0.9)";
  roundedRect(x - width / 2, y - 7, width, 14, 4);
  context.fill();
  context.fillStyle = previousFill;
  context.fillText(text, x, y);
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
  return Boolean(state.selectedId) && state.focusedNetNames.has(net.name);
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
  updateSelectionFocus(node);
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
  renderNetList(
    "#incoming-nets",
    state.nets.filter((net) => net.targets.some((target) => target.node === node.id)),
  );
  renderNameList("#generated-outputs", generatedOutputNames(node));
  requestFrame();
}

function renderCircuitSummary() {
  if (!state.topology) return;
  const units = state.topology.units;
  const fields = {
    wns: (value) => formatMetric(value, units.time),
    tns: (value) => formatMetric(value, units.time),
    circuit_delay: (value) => formatMetric(value, units.time),
    area: (value, summary) => formatBudget(value, summary.maximum_area, units.area),
    power: (value, summary) => formatBudget(value, summary.maximum_power, units.power),
    leakage_power: (value) => formatMetric(value, units.power),
    dynamic_power: (value) => formatMetric(value, units.power),
    timing_compliant: formatCompliance,
    area_compliant: formatCompliance,
    power_compliant: formatCompliance,
    all_constraints_compliant: formatCompliance,
  };
  for (const stateName of ["original", "optimized"]) {
    const card = document.querySelector(`[data-summary-state="${stateName}"]`);
    card.hidden = !state.visibleStates[stateName];
    const summary = state.topology.states[stateName].summary;
    const original = state.topology.states.original.summary;
    Object.entries(fields).forEach(([field, formatter]) => {
      const output = card.querySelector(`[data-field="${field}"]`);
      output.replaceChildren(document.createTextNode(formatter(summary[field], summary)));
      const comparison = stateName === "optimized"
        ? formatSummaryChange(field, summary[field], original[field], units.time)
        : null;
      if (comparison !== null) {
        const delta = document.createElement("span");
        delta.className = "metric-delta";
        delta.textContent = comparison.text;
        delta.title = comparison.title;
        output.append(delta);
      }
      output.classList.toggle("pass", summary[field] === true);
      output.classList.toggle("fail", summary[field] === false);
      output.closest("div").classList.toggle(
        "changed",
        stateName === "optimized" && summary[field] !== original[field],
      );
    });
  }
}

function formatCompliance(value) {
  return value ? "Pass" : "Fail";
}

function formatBudget(value, maximum, unit) {
  return `${formatNumber(value)} / ${formatNumber(maximum)} ${unit}`;
}

function formatSummaryChange(field, optimizedValue, originalValue, timeUnit) {
  if (field === "wns" || field === "tns") {
    return numericChange(optimizedValue, originalValue, (change) => ({
      text: `${signedNumber(change)} ${timeUnit}`,
      title: "Absolute change from original",
    }));
  }
  const percentage = relativePercentageChange(optimizedValue, originalValue);
  return percentage === null ? null : {
    text: percentage,
    title: "Relative change from original",
  };
}

function numericChange(optimizedValue, originalValue, formatter) {
  if (
    typeof optimizedValue !== "number"
    || typeof originalValue !== "number"
    || !Number.isFinite(optimizedValue)
    || !Number.isFinite(originalValue)
  ) return null;
  return formatter(optimizedValue - originalValue);
}

function signedNumber(value) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value)}`;
}

function relativePercentageChange(optimizedValue, originalValue) {
  if (
    typeof optimizedValue !== "number"
    || typeof originalValue !== "number"
    || !Number.isFinite(optimizedValue)
    || !Number.isFinite(originalValue)
  ) return null;
  if (originalValue === 0) return optimizedValue === 0 ? "0%" : "—";
  const percentage = ((optimizedValue - originalValue) / Math.abs(originalValue)) * 100;
  return `${signedNumber(percentage)}%`;
}

function updateSelectionFocus(node) {
  state.focusedNodeIds = new Set();
  state.focusedNetNames = new Set();
  if (!node) return;

  state.focusedNodeIds.add(node.id);
  const incoming = state.nets.filter((net) => net.targets.some((target) => target.node === node.id));
  incoming.forEach((net) => {
    state.focusedNetNames.add(net.name);
    state.focusedNodeIds.add(net.source);
  });

  const pending = [node.id];
  const traversed = new Set();
  while (pending.length) {
    const sourceId = pending.shift();
    if (!sourceId || traversed.has(sourceId)) continue;
    traversed.add(sourceId);
    state.nets.filter((net) => net.source === sourceId).forEach((net) => {
      state.focusedNetNames.add(net.name);
      net.targets.forEach((target) => {
        state.focusedNodeIds.add(target.node);
        pending.push(target.node);
      });
    });
  }
}

function generatedOutputNames(node) {
  if (!node) return [];
  if (node.kind === "output") return [node.name];
  return [...state.focusedNodeIds]
    .map((nodeId) => state.nodeById.get(nodeId))
    .filter((focusedNode) => focusedNode?.kind === "output")
    .map((focusedNode) => focusedNode.name)
    .sort();
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
    .map((path) => `#${path.rank} (${formatNumber(path.slack)} ${timeUnit})`)
    .join(", ");
  const values = {
    cell: metrics.cell,
    size: metrics.size,
    load: formatMetric(metrics.load_capacitance, capUnit),
    rise: formatMetric(metrics.delay_rise, timeUnit),
    fall: formatMetric(metrics.delay_fall, timeUnit),
    paths: pathRanks || "None",
  };
  const original = state.topology.states.original;
  const originalMetrics = original.gates[gateName];
  const originalPathRanks = original.critical_paths
    .filter((path) => path.gates.includes(gateName))
    .map((path) => `#${path.rank} (${formatNumber(path.slack)} ${timeUnit})`)
    .join(", ") || "None";
  const originalValues = {
    cell: originalMetrics.cell,
    size: originalMetrics.size,
    load: formatMetric(originalMetrics.load_capacitance, capUnit),
    rise: formatMetric(originalMetrics.delay_rise, timeUnit),
    fall: formatMetric(originalMetrics.delay_fall, timeUnit),
    paths: originalPathRanks,
  };
  Object.entries(values).forEach(([field, value]) => {
    const output = card.querySelector(`[data-field="${field}"]`);
    output.textContent = value;
    output.closest("div").classList.toggle(
      "changed",
      stateName === "optimized" && value !== originalValues[field],
    );
  });
}

function formatMetric(value, unit) {
  return `${formatNumber(value)} ${unit}`;
}

function formatNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const rounded = Number(numeric.toFixed(4));
  if (Object.is(rounded, -0) || rounded === 0) return "0";
  const formatted = rounded.toFixed(4).replace(/\.?0+$/, "");
  if (formatted.startsWith("0.")) return formatted.slice(1);
  if (formatted.startsWith("-0.")) return `-${formatted.slice(2)}`;
  return formatted;
}

function renderNetList(selector, nets) {
  renderNameList(selector, nets.map((net) => net.name));
}

function renderNameList(selector, names) {
  const list = document.querySelector(selector);
  list.replaceChildren();
  if (!names.length) {
    const item = document.createElement("li");
    item.className = "none";
    item.textContent = "None";
    list.append(item);
    return;
  }
  names.forEach((name) => {
    const item = document.createElement("li");
    item.textContent = name;
    list.append(item);
  });
}

function localPointer(event) {
  const bounds = panel.getBoundingClientRect();
  return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
}

function activateView(viewMode) {
  if (viewMode === "schematic" && !state.schematicReady) return;
  state.viewMode = viewMode;
  state.nodes = viewMode === "schematic" ? state.schematicNodes : state.analysisNodes;
  state.nodeById = new Map(state.nodes.map((node) => [node.id, node]));
  state.fitted = false;
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === viewMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const graph = document.querySelector("#graph");
  graph.setAttribute(
    "aria-label",
    viewMode === "schematic"
      ? "Interactive circuit schematic with orthogonal pin-aware wiring"
      : "Interactive circuit topology",
  );
  fitView();
}

function inspectorWidthLimits() {
  const availableWidth = workspace.getBoundingClientRect().width;
  const maximum = availableWidth - 54 - 24 - minimumGraphWidth;
  return {
    minimum: minimumInspectorWidth,
    maximum: Math.max(minimumInspectorWidth, maximum),
  };
}

function applyInspectorWidth(requestedWidth, persist = true) {
  if (window.matchMedia("(max-width: 900px)").matches) {
    inspector.style.setProperty("--inspector-font-scale", "1");
    return;
  }
  const limits = inspectorWidthLimits();
  const width = clamp(requestedWidth, limits.minimum, limits.maximum);
  const fontScale = clamp(Math.sqrt(width / defaultInspectorWidth), 0.9, 1.5);
  state.inspectorWidth = width;
  workspace.style.setProperty("--inspector-width", `${width}px`);
  inspector.style.setProperty("--inspector-font-scale", String(fontScale));
  inspectorResizer.setAttribute("aria-valuemax", String(Math.round(limits.maximum)));
  inspectorResizer.setAttribute("aria-valuenow", String(Math.round(width)));
  if (persist) {
    try {
      localStorage.setItem("circuit-viewer-inspector-width", String(width));
    } catch {
      // Browser privacy settings may disable local storage; resizing still works.
    }
  }
}

function initializeInspectorResize() {
  let initialWidth = defaultInspectorWidth;
  try {
    const storedWidth = Number(localStorage.getItem("circuit-viewer-inspector-width"));
    if (Number.isFinite(storedWidth) && storedWidth > 0) initialWidth = storedWidth;
  } catch {
    // Use the default when local storage is unavailable.
  }
  applyInspectorWidth(initialWidth, false);
}

function finishInspectorResize(event) {
  if (!state.resizingInspector || event.pointerId !== state.inspectorPointerId) return;
  state.resizingInspector = false;
  state.inspectorPointerId = null;
  inspectorResizer.classList.remove("active");
  document.body.classList.remove("resizing-inspector");
  inspectorResizer.releasePointerCapture(event.pointerId);
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
  else if (event.key.toLowerCase() === "v" && state.schematicReady) {
    activateView(state.viewMode === "analysis" ? "schematic" : "analysis");
  }
  else return;
  event.preventDefault();
});

inspectorResizer.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || window.matchMedia("(max-width: 900px)").matches) return;
  state.resizingInspector = true;
  state.inspectorPointerId = event.pointerId;
  inspectorResizer.classList.add("active");
  document.body.classList.add("resizing-inspector");
  inspectorResizer.setPointerCapture(event.pointerId);
  event.preventDefault();
});

inspectorResizer.addEventListener("pointermove", (event) => {
  if (!state.resizingInspector || event.pointerId !== state.inspectorPointerId) return;
  const workspaceRight = workspace.getBoundingClientRect().right;
  applyInspectorWidth(workspaceRight - event.clientX);
});

inspectorResizer.addEventListener("pointerup", finishInspectorResize);
inspectorResizer.addEventListener("pointercancel", finishInspectorResize);
inspectorResizer.addEventListener("dblclick", () => {
  applyInspectorWidth(defaultInspectorWidth);
});
inspectorResizer.addEventListener("keydown", (event) => {
  const step = event.shiftKey ? 40 : 16;
  if (event.key === "ArrowLeft") applyInspectorWidth(state.inspectorWidth + step);
  else if (event.key === "ArrowRight") applyInspectorWidth(state.inspectorWidth - step);
  else if (event.key === "Home") applyInspectorWidth(defaultInspectorWidth);
  else return;
  event.preventDefault();
});

window.addEventListener("resize", () => applyInspectorWidth(state.inspectorWidth, false));

document.querySelector("#zoom-in").addEventListener("click", () => zoomAt(1.25));
document.querySelector("#zoom-out").addEventListener("click", () => zoomAt(1 / 1.25));
document.querySelector("#fit-view").addEventListener("click", fitView);
document.querySelector("#choose-directory").addEventListener("click", chooseOutputDirectory);
document.querySelector("#directory-picker").addEventListener("change", async (event) => {
  const files = [...event.target.files];
  const candidates = files
    .filter((file) => file.name === "circuit_topology.json")
    .sort((left, right) => (
      left.webkitRelativePath.split("/").length
      - right.webkitRelativePath.split("/").length
    ));
  if (!candidates.length) {
    showLoadError("Selected directory does not contain circuit_topology.json");
  } else {
    await openTopologyFile(candidates[0]);
  }
  event.target.value = "";
});
document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => activateView(button.dataset.view));
});
document.querySelector("#show-net-labels").addEventListener("change", (event) => {
  state.showNetLabels = event.target.checked;
  requestFrame();
});

document.querySelectorAll("[data-state]").forEach((button) => {
  button.addEventListener("click", () => {
    const stateName = button.dataset.state;
    state.visibleStates[stateName] = !state.visibleStates[stateName];
    button.classList.toggle("active", state.visibleStates[stateName]);
    button.setAttribute("aria-pressed", String(state.visibleStates[stateName]));
    renderCircuitSummary();
    const selected = state.nodeById.get(state.selectedId);
    if (selected) renderGateAnalysis(selected);
    requestFrame();
  });
});

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
initializeInspectorResize();
initialize();
