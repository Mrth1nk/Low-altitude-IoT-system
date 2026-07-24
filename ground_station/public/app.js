const statusGrid = document.getElementById("statusGrid");
const aircraftGrid = document.getElementById("aircraftGrid");
const aircraftMessages = document.getElementById("aircraftMessages");
const log = document.getElementById("log");
const latInput = document.getElementById("latInput");
const lngInput = document.getElementById("lngInput");
const speedInput = document.getElementById("speedInput");
const sendWaypointBtn = document.getElementById("sendWaypointBtn");
const taskBanner = document.getElementById("taskBanner");
const taskBannerTitle = document.getElementById("taskBannerTitle");
const taskBannerDetail = document.getElementById("taskBannerDetail");
const toastHost = document.getElementById("toastHost");
const arrivalModal = document.getElementById("arrivalModal");
const arrivalSummary = document.getElementById("arrivalSummary");
const waypointQueueEl = document.getElementById("waypointQueue");
const geofenceEnabledEl = document.getElementById("geofenceEnabled");
const geofenceRadiusEl = document.getElementById("geofenceRadius");
const aircraftAlert = document.getElementById("aircraftAlert");
const roverWaypointModeBtn = document.getElementById("roverWaypointMode");
const aircraftWaypointModeBtn = document.getElementById("aircraftWaypointMode");
const aircraftTestHomeEl = document.getElementById("aircraftTestHome");

let driveTimer = null;
let activeKey = "";
let map = null;
let roverMarker = null;
let targetMarker = null;
let trackLine = null;
let activeRouteLine = null;
let queueRouteLine = null;
let geofenceCircle = null;
let aircraftLinkActive = false;
let aircraftCommandUsable = false;
let waypointMode = "rover";
let activeTarget = null;
let localTargetHoldUntil = 0;
let lastArrivalKey = "";
let lastAlertKey = "";
const NJU_XIANLIN = {lat: 32.11956, lng: 118.958406};
const ARRIVAL_RADIUS_M = 3;
const waypointQueue = [];
const roverTrack = [];
const compactAircraftTimes = new Map();
const aircraftMessageHistory = [];
const seenAircraftMessageKeys = new Set();
const aircraftTelemetry = {
  mode: "-",
  armed: "-",
  system: "-",
  battery: "-",
  speed: "-",
  position: "-",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function addLog(line) {
  const stamp = new Date().toLocaleTimeString();
  log.textContent = `[${stamp}] ${line}\n` + log.textContent;
}

function renderGrid(root, items) {
  root.innerHTML = items
    .map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

function validCoord(lat, lng) {
  return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) > 0.000001 && Math.abs(lng) > 0.000001;
}

function formatCoord(value) {
  return Number(value).toFixed(7);
}

function initMap() {
  if (!window.L || map) return;
  map = L.map("map", {zoomControl: true}).setView([NJU_XIANLIN.lat, NJU_XIANLIN.lng], 17);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  roverMarker = L.marker([NJU_XIANLIN.lat, NJU_XIANLIN.lng]).addTo(map).bindPopup("小车/参考位置");
  targetMarker = L.circleMarker([NJU_XIANLIN.lat, NJU_XIANLIN.lng], {
    radius: 9,
    color: "#2563eb",
    weight: 3,
    fillOpacity: 0.25,
  }).addTo(map).bindPopup("目标航点");
  trackLine = L.polyline([], {color: "#0f766e", weight: 3, opacity: 0.78}).addTo(map);
  activeRouteLine = L.polyline([], {color: "#f97316", weight: 4, opacity: 0.86}).addTo(map);
  queueRouteLine = L.polyline([], {color: "#2563eb", weight: 3, opacity: 0.72, dashArray: "8 8"}).addTo(map);
  geofenceCircle = L.circle([NJU_XIANLIN.lat, NJU_XIANLIN.lng], {
    radius: Number(geofenceRadiusEl.value || 50),
    color: "#dc2626",
    fillColor: "#dc2626",
    fillOpacity: 0.04,
    weight: 2,
  }).addTo(map);
  updateGeofenceVisibility();
  latInput.value = formatCoord(NJU_XIANLIN.lat);
  lngInput.value = formatCoord(NJU_XIANLIN.lng);
  map.on("click", (event) => setTarget(event.latlng.lat, event.latlng.lng, true, true));
}

function setTarget(lat, lng, pan = false, localEdit = false) {
  if (!validCoord(lat, lng)) return;
  if (localEdit) {
    activeTarget = currentWaypointFromInputs(lat, lng);
    localTargetHoldUntil = Date.now() + 20000;
    lastArrivalKey = "";
  }
  latInput.value = formatCoord(lat);
  lngInput.value = formatCoord(lng);
  if (targetMarker) targetMarker.setLatLng([lat, lng]);
  if (geofenceEnabledEl.checked && geofenceCircle) geofenceCircle.setLatLng([lat, lng]);
  updateRouteLines();
  if (pan && map) map.panTo([lat, lng]);
}

function holdCurrentInputTarget() {
  const lat = Number(latInput.value);
  const lng = Number(lngInput.value);
  if (!validCoord(lat, lng)) return;
  activeTarget = currentWaypointFromInputs(lat, lng);
  localTargetHoldUntil = Date.now() + 20000;
  if (targetMarker) targetMarker.setLatLng([lat, lng]);
  updateRouteLines();
  updateGeofenceVisibility();
}

function updateMap(t, raw) {
  initMap();
  if (!map) return;
  const roverLat = Number(t.lat);
  const roverLng = Number(t.lng);
  const hasRoverGps = validCoord(roverLat, roverLng);
  const displayLat = hasRoverGps ? roverLat : NJU_XIANLIN.lat;
  const displayLng = hasRoverGps ? roverLng : NJU_XIANLIN.lng;
  roverMarker.setLatLng([displayLat, displayLng]);
  roverMarker.bindPopup(hasRoverGps ? "小车 GPS" : "南京大学仙林校区参考点");
  updateRoverTrack(displayLat, displayLng, hasRoverGps);

  const cloudTarget = cloudTargetFromState(t, raw);
  if (cloudTarget && Date.now() > localTargetHoldUntil) {
    activeTarget = cloudTarget;
    setTarget(cloudTarget.lat, cloudTarget.lng);
  } else if (activeTarget) {
    setTarget(activeTarget.lat, activeTarget.lng);
  }
  updateGeofenceRadius();
  const center = hasRoverGps ? [roverLat, roverLng] : [NJU_XIANLIN.lat, NJU_XIANLIN.lng];
  if (!map._initialFitDone) {
    map.setView(center, 17);
    map._initialFitDone = true;
  }
  updateRouteLines(displayLat, displayLng);
}

function cloudTargetFromState(t, raw) {
  const lat = Number(raw.target_lat || t.target_lat);
  const lng = Number(raw.target_lng || t.target_lng);
  const speed = Number(raw.target_speed || t.target_speed || speedInput.value || 0.5);
  if (!validCoord(lat, lng)) return null;
  return {lat, lng, speed};
}

function renderState(doc) {
  const t = doc.telemetry || {};
  const raw = doc.raw || {};
  const aircraft = normalizeAircraft(t);
  renderGrid(statusGrid, [
    ["云", doc.online ? "online" : "offline"],
    ["飞控", t.fc_link ? "linked" : "missing"],
    ["模式", t.flight_mode || "-"],
    ["电量", "100%"],
    ["LTE", `${t.lte_rssi ?? "-"} dBm`],
    ["位置", `${t.lat ?? "-"}, ${t.lng ?? "-"}`],
    ["速度", `${t.ground_speed ?? "-"} m/s`],
    ["任务", t.mission_status || "-"],
    ...(t.fault_text && t.fault_text !== "-" ? [["故障", t.fault_text]] : []),
  ]);
  updateMap(t, raw);
  updateTaskExperience(doc, t, raw, aircraft);
  updateAircraftTelemetry(aircraft);
  ingestAircraftMessages(aircraft.messages);
  aircraftLinkActive = Boolean(aircraft.link_active);
  aircraftCommandUsable = aircraftUsable(aircraft);
  updateAircraftCommandButtons();
  const messages = aircraftMessageHistory.slice(0, 28);
  renderGrid(aircraftGrid, [
    ["链路", aircraft.link_active ? "receiving" : aircraftCommandUsable ? "stale-ok" : "waiting"],
    ["模式", aircraftTelemetry.mode],
    ["解锁", aircraftTelemetry.armed],
    ["系统", aircraftTelemetry.system],
    ["电池", aircraftTelemetry.battery],
    ["速度/航向", aircraftTelemetry.speed],
    ["位置", aircraftTelemetry.position],
  ]);
  updateAircraftAlert(aircraft);
  aircraftMessages.innerHTML = messages.length
    ? messages.map((item) => `<div class="message-row"><time>${formatMessageTime(item.time)}</time><strong>${escapeHtml(messageTypeLabel(item))}</strong><span class="message-text" title="${escapeHtml(rawAircraftMessageText(item))}">${escapeHtml(displayAircraftMessageText(item))}</span></div>`).join("")
    : "";
}

function updateRoverTrack(lat, lng, hasRoverGps) {
  if (!hasRoverGps || !trackLine) return;
  const last = roverTrack[roverTrack.length - 1];
  if (!last || distanceMeters(last.lat, last.lng, lat, lng) >= 0.8) {
    roverTrack.push({lat, lng});
    if (roverTrack.length > 300) roverTrack.shift();
    trackLine.setLatLngs(roverTrack.map((point) => [point.lat, point.lng]));
  }
}

function updateRouteLines(roverLat = null, roverLng = null) {
  if (!activeRouteLine || !queueRouteLine) return;
  const roverPoint = validCoord(Number(roverLat), Number(roverLng))
    ? [Number(roverLat), Number(roverLng)]
    : roverMarker
      ? [roverMarker.getLatLng().lat, roverMarker.getLatLng().lng]
      : null;
  const targetPoint = activeTarget && validCoord(activeTarget.lat, activeTarget.lng)
    ? [activeTarget.lat, activeTarget.lng]
    : validCoord(Number(latInput.value), Number(lngInput.value))
      ? [Number(latInput.value), Number(lngInput.value)]
      : null;

  activeRouteLine.setLatLngs(roverPoint && targetPoint ? [roverPoint, targetPoint] : []);

  const queuePoints = waypointQueue.map((point) => [point.lat, point.lng]);
  const queueStart = targetPoint || roverPoint;
  queueRouteLine.setLatLngs(queueStart && queuePoints.length ? [queueStart, ...queuePoints] : []);
}

function updateGeofenceRadius() {
  if (!geofenceCircle) return;
  geofenceCircle.setRadius(Math.max(5, Number(geofenceRadiusEl.value || 50)));
  updateGeofenceVisibility();
}

function updateGeofenceVisibility() {
  if (!geofenceCircle) return;
  const enabled = geofenceEnabledEl.checked;
  if (enabled) {
    const targetLat = Number(latInput.value);
    const targetLng = Number(lngInput.value);
    if (validCoord(targetLat, targetLng)) geofenceCircle.setLatLng([targetLat, targetLng]);
  }
  geofenceCircle.setStyle({opacity: enabled ? 1 : 0, fillOpacity: enabled ? 0.04 : 0});
}

function updateTaskExperience(doc, t, raw, aircraft) {
  const roverLat = Number(t.lat);
  const roverLng = Number(t.lng);
  const cloudTarget = cloudTargetFromState(t, raw);
  const targetLat = Number(activeTarget?.lat ?? cloudTarget?.lat);
  const targetLng = Number(activeTarget?.lng ?? cloudTarget?.lng);
  const hasRoverGps = validCoord(roverLat, roverLng);
  const hasTarget = validCoord(targetLat, targetLng);
  const distance = hasRoverGps && hasTarget ? distanceMeters(roverLat, roverLng, targetLat, targetLng) : null;
  const mission = String(t.mission_status || "");
  const waypointWaiting = t.waypoint_ready === false || Number(t.gps_fix_type || 0) < 3;

  let status = "待命";
  let detail = doc.online ? "涂鸦云在线，等待任务" : "涂鸦云离线";
  let tone = "idle";
  if (mission.includes("command_failed") || t.fault_text) {
    status = "任务失败";
    detail = t.fault_text || mission;
    tone = "danger";
  } else if (waypointWaiting && hasTarget) {
    status = "等待 GPS";
    detail = "小车 GPS 未满足航点条件";
    tone = "warn";
  } else if (distance != null && distance <= ARRIVAL_RADIUS_M) {
    status = "已到达";
    detail = `距目标 ${distance.toFixed(1)} m`;
    tone = "ok";
    maybeShowArrival(targetLat, targetLng, roverLat, roverLng, distance);
  } else if (hasTarget && (mission.includes("waypoint") || mission.includes("sent") || activeTarget)) {
    status = "行驶中";
    detail = distance == null ? "等待位置回传" : `距目标 ${distance.toFixed(1)} m`;
    tone = "active";
  }

  updateTaskBanner(status, detail, tone);
  checkRoverAlerts(t, hasRoverGps, roverLat, roverLng);
  if (!aircraft.link_active) pushOnceAlert("aircraft-link", "飞机链路暂未刷新，飞机指令已锁定", "warn");
}

function currentWaypointFromInputs(lat = Number(latInput.value), lng = Number(lngInput.value)) {
  const value = Number(speedInput.value || (waypointMode === "aircraft" ? 20 : 0.5));
  return {
    lat,
    lng,
    speed: waypointMode === "aircraft" ? Math.max(1, Math.min(120, value || 20)) : Math.max(0.1, Math.min(3, value || 0.5)),
    mode: waypointMode,
  };
}

function setWaypointMode(mode) {
  waypointMode = mode === "aircraft" ? "aircraft" : "rover";
  roverWaypointModeBtn.classList.toggle("active", waypointMode === "rover");
  aircraftWaypointModeBtn.classList.toggle("active", waypointMode === "aircraft");
  speedInput.placeholder = waypointMode === "aircraft" ? "高度 m" : "速度 m/s";
  sendWaypointBtn.textContent = waypointMode === "aircraft" ? "发送飞机航点" : "发送航点";
  aircraftTestHomeEl.disabled = waypointMode !== "aircraft";
  if (waypointMode !== "aircraft") aircraftTestHomeEl.checked = false;
  if (waypointMode === "aircraft" && Number(speedInput.value || 0) < 1) speedInput.value = "20";
  if (waypointMode === "rover" && Number(speedInput.value || 0) > 3) speedInput.value = "0.5";
  holdCurrentInputTarget();
}

function updateTaskBanner(title, detail, tone) {
  taskBanner.className = `task-banner ${tone}`;
  taskBannerTitle.textContent = title;
  taskBannerDetail.textContent = detail;
}

function checkRoverAlerts(t, hasRoverGps, roverLat, roverLng) {
  const lte = Number(t.lte_rssi);
  if (Number.isFinite(lte) && lte < -90) pushOnceAlert(`lte-${Math.round(lte / 5)}`, `L610 信号较弱：${lte} dBm`, "warn");
  if (geofenceEnabledEl.checked && hasRoverGps && geofenceCircle) {
    const center = geofenceCircle.getLatLng();
    const radius = Number(geofenceRadiusEl.value || 50);
    const distance = distanceMeters(roverLat, roverLng, center.lat, center.lng);
    if (distance > radius) pushOnceAlert(`fence-${Math.floor(distance / 5)}`, `小车已离开电子围栏：${distance.toFixed(1)} m`, "danger");
  }
}

function pushOnceAlert(key, message, tone = "info") {
  if (lastAlertKey === key) return;
  lastAlertKey = key;
  showToast(message, tone);
}

function maybeShowArrival(targetLat, targetLng, roverLat, roverLng, distance) {
  const key = `${targetLat.toFixed(6)},${targetLng.toFixed(6)}`;
  if (lastArrivalKey === key) return;
  lastArrivalKey = key;
  arrivalSummary.innerHTML = [
    ["目标", `${targetLat.toFixed(7)}, ${targetLng.toFixed(7)}`],
    ["小车", `${roverLat.toFixed(7)}, ${roverLng.toFixed(7)}`],
    ["误差", `${distance.toFixed(1)} m`],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  arrivalModal.hidden = false;
  showToast(`已到达目标航点，误差 ${distance.toFixed(1)} m`, "ok");
}

function updateAircraftAlert(aircraft) {
  if (aircraft.link_active) {
    aircraftAlert.hidden = true;
    aircraftAlert.textContent = "";
    return;
  }
  aircraftAlert.hidden = false;
  aircraftAlert.textContent = aircraftCommandUsable
    ? "飞机链路暂未实时刷新：系统会继续尝试发送并保持链路保护。"
    : "飞机链路未连接：飞机指令已锁定。";
}

function normalizeAircraft(t) {
  if (t.aircraft && typeof t.aircraft === "object") return t.aircraft;
  const text = String(t.aircraft_msg || "");
  const messageTime = Number(t.aircraft_msg_time || 0);
  const messages = text ? [stableCompactAircraftMessage("AIRCRAFT", text, messageTime)] : [];
  return {
    link_active: Boolean(t.aircraft_link),
    last_seen_age_sec: t.aircraft_age,
    packets_received: t.aircraft_packets,
    last_remote: "-",
    messages,
  };
}

function stableCompactAircraftMessage(type, text, sourceTime = 0) {
  const time = Number(sourceTime) || Date.now() / 1000;
  const key = `${type}\n${time}\n${text}`;
  if (!compactAircraftTimes.has(key)) compactAircraftTimes.set(key, time);
  return {time: compactAircraftTimes.get(key), type, text, key};
}

function updateAircraftTelemetry(aircraft) {
  const messages = Array.isArray(aircraft.messages) ? aircraft.messages : [];
  for (const item of messages.slice(-40)) {
    parseAircraftTelemetryLine(item.type || "MSG", item.text || "");
  }
}

function parseAircraftTelemetryLine(type, text) {
  const label = `${type || ""} ${text || ""}`;
  const heartbeat = label.match(/心跳\s+([A-Z0-9_]+)\s+armed=(YES|NO)\s+sys=([0-9]+\/[0-9]+)/i);
  if (heartbeat) {
    aircraftTelemetry.mode = heartbeat[1].toUpperCase();
    aircraftTelemetry.armed = heartbeat[2].toUpperCase() === "YES" ? "YES" : "NO";
    aircraftTelemetry.system = heartbeat[3];
  }
  const battery = label.match(/电池\s+(-?\d+)%\s+([0-9.]+V)/);
  if (battery) {
    aircraftTelemetry.battery = `${battery[1]}% ${battery[2]}`;
  }
  const hud = label.match(/速度\s+(-?[0-9.]+m\/s)\s+航向\s+(-?\d+)\s+油门\s+(\d+)%/);
  if (hud) {
    aircraftTelemetry.speed = `${hud[1]} / ${hud[2]} deg`;
  }
  const position = label.match(/位置\s+(-?[0-9.]+),\s*(-?[0-9.]+)\s+高\s+(-?[0-9.]+m)/);
  if (position) {
    aircraftTelemetry.position = `${position[1]}, ${position[2]} / ${position[3]}`;
  }
}

function compactAircraftMessages(messages) {
  const list = [];
  for (const item of (Array.isArray(messages) ? messages : []).slice(-40).reverse()) {
    const type = item.type || "MSG";
    const text = item.text || "";
    const time = Number(item.time) || stableCompactAircraftMessage(type, text).time;
    list.push({
      time,
      type,
      text,
      key: item.key || `${type}\n${time}\n${text}`,
      count: 1,
    });
    if (list.length >= 18) break;
  }
  return list;
}

function ingestAircraftMessages(messages) {
  for (const item of compactAircraftMessages(messages).reverse()) {
    if (!item.text) continue;
    if (seenAircraftMessageKeys.has(item.key)) continue;
    seenAircraftMessageKeys.add(item.key);
    aircraftMessageHistory.unshift({...item, count: 1});
    if (aircraftMessageHistory.length > 80) aircraftMessageHistory.length = 80;
  }
}

function formatMessageTime(value) {
  return value ? new Date(value * 1000).toLocaleTimeString() : "-";
}

function messageTypeLabel(item) {
  return item.type;
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

async function refresh() {
  try {
    const res = await fetch("/api/state", {cache: "no-store"});
    const doc = await res.json();
    if (!res.ok || doc.ok === false) throw new Error(doc.error || `HTTP ${res.status}`);
    renderState(doc);
  } catch (err) {
    addLog(`state error: ${err.message}`);
  }
}

async function postCommand(body, options = {}) {
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const doc = await res.json();
    if (!options.quiet || !doc.ok) addLog(doc.ok ? `command sent: ${body.command}` : `command failed: ${doc.error}`);
    if (!options.quiet) showToast(doc.ok ? `云指令已下发：${body.command}` : `云指令失败：${doc.error}`, doc.ok ? "ok" : "danger");
    return doc;
  } catch (err) {
    addLog(`command error: ${err.message}`);
    if (!options.quiet) showToast(`云指令异常：${err.message}`, "danger");
    return {ok: false, error: err.message};
  }
}

async function sendAircraftCommand(command) {
  if (!aircraftCommandUsable) {
    addLog("aircraft command blocked: 飞机链路未刷新");
    return {ok: false, error: "aircraft link inactive"};
  }
  return postCommand({command});
}

async function sendAircraftWaypoint(lat, lng, altitude) {
  if (!aircraftCommandUsable) {
    addLog("aircraft waypoint blocked: 飞机链路未刷新");
    showToast("飞机链路未连接，无法发送飞机航点", "warn");
    return {ok: false, error: "aircraft link inactive"};
  }
  const useTestHome = Boolean(aircraftTestHomeEl.checked);
  return postCommand({
    command: useTestHome ? "aircraft_goto_test_home" : "aircraft_goto",
    target_lat: lat,
    target_lng: lng,
    target_speed: altitude,
  });
}

function updateAircraftCommandButtons() {
  for (const button of document.querySelectorAll("[data-aircraft-command]")) {
    button.disabled = !aircraftCommandUsable;
    button.title = aircraftCommandUsable ? "" : "飞机链路刷新后才能发送飞机指令";
  }
}

function aircraftUsable(aircraft) {
  const age = Number(aircraft.last_seen_age_sec);
  return Boolean(aircraft.link_active) || (Number.isFinite(age) && age < 300 && Number(aircraft.packets_received || 0) > 0);
}

function drivePayload(steering, throttle) {
  return {
    command: throttle === 0 && steering === 0 ? "stop" : "manual",
    steering,
    throttle,
  };
}

function startDriveControl(steering, throttle) {
  const send = () => postCommand({
    ...drivePayload(steering, throttle),
  }, {quiet: true});
  postCommand(drivePayload(steering, throttle));
  clearInterval(driveTimer);
  driveTimer = setInterval(send, 250);
}

function startDrive(button) {
  const steering = Number(button.dataset.steering || 0);
  const throttle = Number(button.dataset.throttle || 0);
  startDriveControl(steering, throttle);
}

function stopDrive() {
  if (!driveTimer) return;
  clearInterval(driveTimer);
  driveTimer = null;
  postCommand({command: "stop", steering: 0, throttle: 0});
}

function sendWaypoint() {
  const lat = Number(latInput.value);
  const lng = Number(lngInput.value);
  const waypoint = currentWaypointFromInputs(lat, lng);
  if (!validCoord(lat, lng)) {
    addLog("waypoint failed: 坐标无效");
    showToast("航点坐标无效", "danger");
    return;
  }
  activeTarget = waypoint;
  lastArrivalKey = "";
  if (waypointMode === "aircraft") {
    sendAircraftWaypoint(lat, lng, waypoint.speed);
    return;
  }
  postCommand({
    command: "waypoint",
    target_lat: lat,
    target_lng: lng,
    target_speed: waypoint.speed,
  });
}

function queueWaypoint() {
  const lat = Number(latInput.value);
  const lng = Number(lngInput.value);
  const waypoint = currentWaypointFromInputs(lat, lng);
  if (!validCoord(lat, lng)) {
    showToast("航点坐标无效，无法加入队列", "danger");
    return;
  }
  waypointQueue.push(waypoint);
  renderWaypointQueue();
  updateRouteLines();
  showToast(`已加入${waypoint.mode === "aircraft" ? "飞机" : "小车"}航点队列：${waypointQueue.length} 个`, "ok");
}

function sendNextWaypoint() {
  if (!waypointQueue.length) {
    showToast("航点队列为空", "warn");
    return;
  }
  const next = waypointQueue.shift();
  setWaypointMode(next.mode || "rover");
  setTarget(next.lat, next.lng, true, true);
  speedInput.value = String(next.speed);
  renderWaypointQueue();
  updateRouteLines();
  sendWaypoint();
}

function renderWaypointQueue() {
  waypointQueueEl.innerHTML = waypointQueue.length
    ? waypointQueue.map((point, index) => `<div><strong>${index + 1}</strong><span>${point.mode === "aircraft" ? "飞机" : "小车"} ${point.lat.toFixed(6)}, ${point.lng.toFixed(6)}</span><em>${point.speed.toFixed(1)} ${point.mode === "aircraft" ? "m高" : "m/s"}</em></div>`).join("")
    : "";
}

function clearWaypointQueue() {
  waypointQueue.length = 0;
  renderWaypointQueue();
  updateRouteLines();
  showToast("航点队列已清空", "ok");
}

function distanceMeters(lat1, lng1, lat2, lng2) {
  const radius = 6371000;
  const toRad = (value) => value * Math.PI / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * radius * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function showToast(message, tone = "info") {
  const node = document.createElement("div");
  node.className = `toast ${tone}`;
  node.textContent = message;
  toastHost.prepend(node);
  setTimeout(() => node.remove(), 4200);
}

function keyToDrive(key) {
  if (key === "ArrowUp" || key.toLowerCase() === "w") return [0, 100];
  if (key === "ArrowDown" || key.toLowerCase() === "s") return [0, -100];
  if (key === "ArrowLeft" || key.toLowerCase() === "a") return [-100, 0];
  if (key === "ArrowRight" || key.toLowerCase() === "d") return [100, 0];
  return null;
}

document.getElementById("refreshBtn").onclick = refresh;
sendWaypointBtn.onclick = sendWaypoint;
roverWaypointModeBtn.onclick = () => setWaypointMode("rover");
aircraftWaypointModeBtn.onclick = () => setWaypointMode("aircraft");
document.getElementById("queueWaypointBtn").onclick = queueWaypoint;
document.getElementById("sendNextWaypointBtn").onclick = sendNextWaypoint;
document.getElementById("clearWaypointQueueBtn").onclick = clearWaypointQueue;
document.getElementById("closeArrivalModal").onclick = () => {
  arrivalModal.hidden = true;
};
geofenceEnabledEl.onchange = updateGeofenceVisibility;
geofenceRadiusEl.onchange = () => {
  if (geofenceCircle) geofenceCircle.setRadius(Math.max(5, Number(geofenceRadiusEl.value || 50)));
};
latInput.onchange = holdCurrentInputTarget;
lngInput.onchange = holdCurrentInputTarget;
for (const button of document.querySelectorAll("[data-command]")) {
  button.onclick = () => postCommand({command: button.dataset.command});
}
for (const button of document.querySelectorAll("[data-aircraft-command]")) {
  button.onclick = () => sendAircraftCommand(button.dataset.aircraftCommand);
}
for (const button of document.querySelectorAll("[data-steering]")) {
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    startDrive(button);
  });
  button.addEventListener("pointerup", stopDrive);
  button.addEventListener("pointerleave", stopDrive);
  button.addEventListener("pointercancel", stopDrive);
}
window.addEventListener("keydown", (event) => {
  const drive = keyToDrive(event.key);
  if (!drive || activeKey) return;
  if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName || "")) return;
  event.preventDefault();
  activeKey = event.key;
  startDriveControl(drive[0], drive[1]);
});
window.addEventListener("keyup", (event) => {
  if (event.key !== activeKey) return;
  event.preventDefault();
  activeKey = "";
  stopDrive();
});

setInterval(refresh, 1000);
initMap();
refresh();
