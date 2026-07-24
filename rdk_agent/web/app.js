const statusGrid = document.getElementById("statusGrid");
const log = document.getElementById("log");
const roverDot = document.getElementById("roverDot");
const aircraftStatusGrid = document.getElementById("aircraftStatusGrid");
const aircraftMessages = document.getElementById("aircraftMessages");
const aircraftPanel = document.getElementById("aircraftPanel");
let map;
let roverMarker;
let targetMarker;
let browserPosition = null;
const activeDriveKeys = new Set();
const FALLBACK_POSITION = {lat: 32.11974, lng: 118.95314};

function initMap() {
  if (!window.L || map) return;
  const initial = getDisplayPosition(FALLBACK_POSITION);
  map = L.map("map").setView([initial.lat, initial.lng], 18);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  roverMarker = L.circleMarker([initial.lat, initial.lng], {
    radius: 8,
    color: "#1d4ed8",
    fillColor: "#2563eb",
    fillOpacity: 0.85,
  }).addTo(map).bindPopup(initial.source === "browser" ? "电脑定位" : "Rover");
  map.on("click", (event) => {
    const {lat, lng} = event.latlng;
    document.getElementById("targetLat").value = lat.toFixed(7);
    document.getElementById("targetLng").value = lng.toFixed(7);
    if (!targetMarker) {
      targetMarker = L.marker([lat, lng]).addTo(map).bindPopup("Target");
    } else {
      targetMarker.setLatLng([lat, lng]);
    }
  });
}

function addLog(line) {
  const stamp = new Date().toLocaleTimeString();
  log.textContent = `[${stamp}] ${line}\n` + log.textContent;
}

async function postCommand(body) {
  const res = await fetch("/api/command", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const doc = await res.json();
  addLog(doc.ok ? `command sent: ${body.command}` : `command failed: ${doc.error}`);
}

let driveTimer = null;

function startDrive(button) {
  const steering = Number(button.dataset.steering || 0);
  const throttle = Number(button.dataset.throttle || 0);
  const send = () => postCommand({
    command: throttle === 0 && steering === 0 ? "stop" : "manual",
    control_mode: "manual",
    steering,
    throttle,
  });
  send();
  clearInterval(driveTimer);
  driveTimer = setInterval(send, 120);
}

function stopDrive() {
  if (!driveTimer) return;
  clearInterval(driveTimer);
  driveTimer = null;
  postCommand({command: "stop"});
}

function renderState(doc) {
  const t = doc.telemetry || {};
  const displayPosition = getDisplayPosition(t);
  const items = [
    ["云", doc.online ? "online" : "offline"],
    ["飞控", t.fc_link ? "linked" : "missing"],
    ["模式", t.flight_mode || "-"],
    ["电量", "100%"],
    ["LTE", `${t.lte_rssi ?? "-"} dBm`],
    ["位置", positionLabel(displayPosition, t)],
    ["速度", `${t.ground_speed ?? "-"} m/s`],
    ["任务", t.mission_status || "-"],
  ];
  if (t.fault_text && t.fault_text !== "-") items.push(["故障", t.fault_text]);
  statusGrid.innerHTML = items.map(([k, v]) => `<div><span>${k}</span><strong>${v}</strong></div>`).join("");
  const x = 50 + Math.max(-40, Math.min(40, (displayPosition.lng - FALLBACK_POSITION.lng) * 10000));
  const y = 50 - Math.max(-40, Math.min(40, (displayPosition.lat - FALLBACK_POSITION.lat) * 10000));
  roverDot.style.left = `${x}%`;
  roverDot.style.top = `${y}%`;
  initMap();
  if (map) {
    const pos = [displayPosition.lat, displayPosition.lng];
    roverMarker.setLatLng(pos);
    roverMarker.setPopupContent(displayPosition.source === "browser" ? "电脑定位" : "Rover");
    map.panTo(pos, {animate: false});
  }
}

function positionLabel(displayPosition, t) {
  if (displayPosition.source === "rover") return `${t.lat ?? "-"}, ${t.lng ?? "-"}`;
  if (displayPosition.source === "browser") return `电脑定位 ${displayPosition.lat.toFixed(7)}, ${displayPosition.lng.toFixed(7)}`;
  return `默认南京大学仙林 ${displayPosition.lat.toFixed(7)}, ${displayPosition.lng.toFixed(7)}`;
}

function hasRoverGps(t) {
  const lat = Number(t.lat);
  const lng = Number(t.lng);
  return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) > 0.000001 && Math.abs(lng) > 0.000001;
}

function getDisplayPosition(t) {
  if (hasRoverGps(t)) {
    return {lat: Number(t.lat), lng: Number(t.lng), source: "rover"};
  }
  if (browserPosition) {
    return {...browserPosition, source: "browser"};
  }
  return {...FALLBACK_POSITION, source: "fallback"};
}

function startBrowserLocation() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      browserPosition = {
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
      };
      if (map) map.panTo([browserPosition.lat, browserPosition.lng], {animate: false});
    },
    () => {},
    {enableHighAccuracy: true, timeout: 8000, maximumAge: 5000},
  );
  navigator.geolocation.watchPosition(
    (pos) => {
      browserPosition = {
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
      };
    },
    () => {},
    {enableHighAccuracy: true, timeout: 8000, maximumAge: 3000},
  );
}

async function refresh() {
  try {
    const res = await fetch("/api/state", {cache: "no-store"});
    renderState(await res.json());
  } catch (err) {
    addLog(`state error: ${err.message}`);
  }
}

function renderAircraft(doc) {
  const compactMessages = compactAircraftMessages(doc.messages || []);
  const newest = compactMessages[0];
  const items = [
    ["网关", doc.ok ? "online" : "offline"],
    ["链路", doc.link_active ? "receiving" : "waiting"],
    ["最近消息", newest ? formatMessageTime(newest.time) : "-"],
    ["包数", doc.packets_received ?? 0],
    ["来源", doc.last_remote || "-"],
  ];
  aircraftStatusGrid.innerHTML = items.map(([k, v]) => `<div><span>${k}</span><strong>${v}</strong></div>`).join("");
  aircraftMessages.innerHTML = compactMessages.length
    ? compactMessages.map((item) => `<div><time>${formatMessageTime(item.time)}</time><strong>${escapeHtml(messageTypeLabel(item))}</strong><span title="${escapeHtml(rawAircraftMessageText(item))}">${escapeHtml(displayAircraftMessageText(item))}</span></div>`).join("")
    : `<div><span>等待系统收到飞行链路消息。</span></div>`;
}

function compactAircraftMessages(messages) {
  const compact = [];
  for (const item of messages.slice(-40).reverse()) {
    const previous = compact[compact.length - 1];
    if (previous && previous.type === item.type && previous.text === item.text) {
      previous.count += 1;
      continue;
    }
    compact.push({...item, count: 1});
    if (compact.length >= 18) break;
  }
  return compact;
}

function formatMessageTime(value) {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleTimeString();
}

function messageTypeLabel(item) {
  const type = item.type || "MSG";
  return item.count > 1 ? `${type} x${item.count}` : type;
}

function rawAircraftMessageText(item) {
  return String(item?.text || "");
}

function displayAircraftMessageText(item) {
  const text = rawAircraftMessageText(item);
  if (text.includes("SIGNAL_INTERRUPTED") || text.includes("SIGNAL_INT")) {
    return "光链路中断，飞机指令已锁定";
  }
  const rawBytes = text.match(/收到数传\s+(\d+B)/);
  if (rawBytes) {
    return `收到飞行链路数据 ${rawBytes[1]}`;
  }
  const fieldElevation = text.match(/Field Elevation Set:\s*(-?\d+(?:\.\d+)?)m?/i);
  if (fieldElevation) {
    return `场地高度已设为 ${fieldElevation[1]}m`;
  }
  return text;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

async function refreshAircraft() {
  const url = document.getElementById("aircraftGateway").value.trim() || "/api/aircraft";
  try {
    const res = await fetch(url, {cache: "no-store"});
    renderAircraft(await res.json());
  } catch (err) {
    aircraftStatusGrid.innerHTML = [
      ["网关", "offline"],
      ["链路", "unknown"],
      ["错误", err.message],
    ].map(([k, v]) => `<div><span>${k}</span><strong>${v}</strong></div>`).join("");
    aircraftMessages.innerHTML = `<div><span>无法连接 RDK 飞机网关：${url}<br>${err.message}</span></div>`;
  }
}

document.getElementById("stopBtn").onclick = () => postCommand({command: "stop"});
document.getElementById("gotoBtn").onclick = () => postCommand({
  command: "goto",
  target_lat: Number(document.getElementById("targetLat").value),
  target_lng: Number(document.getElementById("targetLng").value),
  target_speed: Number(document.getElementById("targetSpeed").value || 0.5),
});
for (const button of document.querySelectorAll("[data-command]")) {
  button.onclick = () => postCommand({command: button.dataset.command});
}
for (const button of document.querySelectorAll(".drive-btn")) {
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    startDrive(button);
  });
  button.addEventListener("pointerup", stopDrive);
  button.addEventListener("pointerleave", stopDrive);
  button.addEventListener("pointercancel", stopDrive);
}
function keyDriveVector() {
  const forward = activeDriveKeys.has("arrowup") || activeDriveKeys.has("w");
  const reverse = activeDriveKeys.has("arrowdown") || activeDriveKeys.has("s");
  const left = activeDriveKeys.has("arrowleft") || activeDriveKeys.has("a");
  const right = activeDriveKeys.has("arrowright") || activeDriveKeys.has("d");
  let throttle = 0;
  let steering = 0;
  if (forward && !reverse) throttle = 100;
  if (reverse && !forward) throttle = -100;
  if (left && !right) steering = throttle ? -75 : -100;
  if (right && !left) steering = throttle ? 75 : 100;
  return {steering, throttle};
}

function updateKeyboardDrive() {
  const {steering, throttle} = keyDriveVector();
  if (steering || throttle) {
    const buttonLike = {dataset: {steering: String(steering), throttle: String(throttle)}};
    startDrive(buttonLike);
  } else {
    stopDrive();
  }
}

function shouldIgnoreDriveKey(event) {
  const tag = event.target?.tagName?.toLowerCase();
  return tag === "input" || tag === "textarea" || event.target?.isContentEditable;
}

window.addEventListener("keydown", (event) => {
  const key = event.key.toLowerCase();
  if (!["arrowup", "arrowdown", "arrowleft", "arrowright", "w", "a", "s", "d"].includes(key) || shouldIgnoreDriveKey(event)) return;
  event.preventDefault();
  activeDriveKeys.add(key);
  updateKeyboardDrive();
});

window.addEventListener("keyup", (event) => {
  const key = event.key.toLowerCase();
  if (!activeDriveKeys.has(key)) return;
  event.preventDefault();
  activeDriveKeys.delete(key);
  updateKeyboardDrive();
});
document.getElementById("openAircraftBtn").onclick = () => {
  aircraftPanel.scrollIntoView({behavior: "smooth", block: "start"});
  refreshAircraft();
};
document.getElementById("aircraftRefresh").onclick = refreshAircraft;

setInterval(refresh, 1000);
setInterval(refreshAircraft, 1000);
initMap();
startBrowserLocation();
refresh();
refreshAircraft();
