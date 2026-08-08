"use strict";

const Core = window.GroundStationCore;
const $ = (id) => document.getElementById(id);
const els = {
  aircraftContent: $("aircraftContent"),
  aircraftGrid: $("aircraftGrid"),
  aircraftMessages: $("aircraftMessages"),
  aircraftMode: $("aircraftWaypointMode"),
  aircraftTimeline: $("aircraftTimeline"),
  altitude: $("altitudeInput"),
  arrivalModal: $("arrivalModal"),
  arrivalSummary: $("arrivalSummary"),
  blocked: $("opticalBlocked"),
  cloudDot: $("cloudDot"),
  cloudText: $("cloudText"),
  fenceEnabled: $("geofenceEnabled"),
  fenceRadius: $("geofenceRadius"),
  lat: $("latInput"),
  lng: $("lngInput"),
  log: $("log"),
  opticalBadge: $("opticalBadge"),
  queue: $("waypointQueue"),
  queueSummary: $("queueSummary"),
  roverGrid: $("statusGrid"),
  roverMode: $("roverWaypointMode"),
  roverTimeline: $("roverTimeline"),
  speed: $("speedInput"),
  speedField: $("speedField"),
  taskBanner: $("taskBanner"),
  taskDetail: $("taskBannerDetail"),
  taskTitle: $("taskBannerTitle"),
  uploadMission: $("uploadMissionBtn"),
};

const FALLBACK = {lat: 32.11956, lng: 118.958406};
const ARRIVAL_METERS = 3;
const routes = {rover: [], aircraft: []};
const messageHistory = [];
const alertKeys = new Set();
let selectedVehicle = "rover";
let latestState = null;
let currentTarget = {...FALLBACK};
let map = null;
let roverMarker = null;
let aircraftMarker = null;
let targetMarker = null;
let roverPath = null;
let aircraftPath = null;
let roverTrackLine = null;
let fenceCircle = null;
let fallbackMarker = null;
const waypointMarkers = {rover: [], aircraft: []};
let mapFallback = {...FALLBACK};
let roverTrack = [];
let arrivalKey = "";
let driveTimer = null;
let activeKey = "";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function validCoord(lat, lng) {
  return Core.validCoordinate(Number(lat), Number(lng));
}

function addLog(text) {
  const stamp = new Date().toLocaleTimeString();
  els.log.textContent = `[${stamp}] ${text}\n${els.log.textContent}`.slice(0, 5000);
}

function showToast(text, tone = "info") {
  const node = document.createElement("div");
  node.className = `toast ${tone}`;
  node.textContent = text;
  $("toastHost").prepend(node);
  setTimeout(() => node.remove(), 4200);
}

function renderMetrics(root, entries) {
  root.innerHTML = entries.map(([label, value]) => (
    `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
  )).join("");
}

function renderTimeline(root, telemetry, vehicle) {
  root.innerHTML = Core.transactionTimeline(telemetry, vehicle).map((item) => (
    `<div class="timeline-item ${item.state}" title="${escapeHtml(item.detail)}">${escapeHtml(item.label)}</div>`
  )).join("");
}

function formatTime(seconds) {
  if (!Number(seconds)) return "-";
  return new Date(Number(seconds) * 1000).toLocaleTimeString();
}

function renderMessages() {
  els.aircraftMessages.innerHTML = messageHistory.length
    ? messageHistory.slice().reverse().map((item) => (
        `<div class="message-row"><time>${formatTime(item.time)}</time>`
        + `<strong>${escapeHtml(item.type)}</strong><span>${escapeHtml(item.text)}</span></div>`
      )).join("")
    : '<div class="empty-state">等待飞机消息</div>';
}

function aircraftDetails(aircraft) {
  const heading = Number(aircraft.heading);
  const normalizedHeading = Number.isFinite(heading)
    ? (((Math.abs(heading) > 360 ? heading / 100 : heading) % 360) + 360) % 360
    : null;
  return [
    ["模式", aircraft.mode || aircraft.flight_mode || "-"],
    ["解锁", aircraft.armed === true ? "YES" : aircraft.armed === false ? "NO" : "-"],
    ["电量", `${aircraft.battery_percent ?? 100}%`],
    ["高度", aircraft.altitude != null ? `${Number(aircraft.altitude).toFixed(2)} m` : "-"],
    ["速度", aircraft.ground_speed != null ? `${Number(aircraft.ground_speed).toFixed(2)} m/s` : "-"],
    ["航向", Number.isFinite(normalizedHeading) ? `${normalizedHeading.toFixed(2)}°` : "-"],
    ["位置", Core.formatObservedPosition(aircraft)],
    ["任务", aircraft.mission_status || "-"],
  ];
}

function renderState(state) {
  latestState = state;
  const telemetry = state.telemetry || {};
  const aircraft = state.aircraft || {};
  els.cloudDot.classList.toggle("online", Boolean(state.online));
  const fresh = state.state_fresh === true;
  const age = Number(state.state_age_sec);
  els.cloudText.textContent = state.online
    ? (fresh ? "涂鸦云在线" : `云状态过期 ${Number.isFinite(age) ? `${age.toFixed(1)}s` : ""}`)
    : "涂鸦云离线";
  renderMetrics(els.roverGrid, [
    ["模式", telemetry.flight_mode || "-"],
    ["解锁", fresh ? (telemetry.armed ? "YES" : "NO") : "过期"],
    ["电量", `${telemetry.battery_percent ?? "-"}%`],
    ["速度", `${telemetry.ground_speed ?? "-"} m/s`],
    ["LTE", `${telemetry.lte_rssi ?? "-"} dBm`],
    ["位置", Core.formatObservedPosition(telemetry)],
  ]);
  renderTimeline(els.roverTimeline, telemetry, "rover");
  renderTimeline(els.aircraftTimeline, telemetry, "aircraft");

  const blocked = Boolean(state.optical?.blocked);
  els.blocked.hidden = !blocked;
  els.aircraftContent.hidden = false;
  els.opticalBadge.textContent = blocked ? "BLOCKED" : aircraft.status || "LOCKED";
  els.opticalBadge.classList.toggle("locked", !blocked);
  for (const button of document.querySelectorAll("[data-aircraft-command]")) {
    button.disabled = blocked || !state.online;
    button.title = blocked ? "OPTICAL LINK BLOCKED" : "";
  }
  updateMissionButton();
  if (blocked) {
    els.aircraftGrid.innerHTML = "";
    const merged = Core.appendAircraftMessages(
      messageHistory,
      aircraft.messages,
      state.cloud_received_at,
    );
    messageHistory.splice(0, messageHistory.length, ...merged);
    renderMessages();
  } else {
    renderMetrics(els.aircraftGrid, aircraftDetails(aircraft));
    const heartbeatMessages = aircraft.messages
      .map((message) => Core.aircraftHeartbeatSummary(message, aircraft.armed))
      .filter(Boolean);
    const merged = Core.appendAircraftMessages(
      messageHistory,
      heartbeatMessages,
      state.cloud_received_at,
    );
    messageHistory.splice(0, messageHistory.length, ...merged);
    renderMessages();
  }

  updateMapFromState(telemetry, aircraft);
  updateTaskBanner(telemetry, state.state_fresh === true);
  checkAlerts(telemetry);
  renderQueue();
}

function updateTaskBanner(telemetry, stateFresh = true) {
  const stage = String(telemetry.rover_tx_stage || "");
  const status = String(telemetry.mission_status || "待命");
  const fault = String(telemetry.fault_text || "");
  let title = "待命";
  let detail = stateFresh
    ? "涂鸦云在线，等待任务"
    : "涂鸦云在线，但小车状态尚未刷新";
  let tone = "idle";
  if (fault || /fail|reject|unsafe|mismatch/i.test(`${stage} ${status}`)) {
    title = "任务失败";
    detail = fault || status;
    tone = "danger";
  } else if (/completed|reached/i.test(status)) {
    title = "已到达";
    detail = status;
    tone = "ok";
  } else if (/upload|queue|verify|execut|active|auto/i.test(`${stage} ${status}`)) {
    title = "任务进行中";
    detail = status;
    tone = "active";
  } else if (Number(telemetry.gps_fix_type || 0) < 3) {
    title = "等待定位";
    detail = "任务可上传验证，AUTO 等待真实 GPS/Home/EKF";
    tone = "warn";
  }
  els.taskBanner.className = `task-banner ${tone}`;
  els.taskTitle.textContent = title;
  els.taskDetail.textContent = detail;
}

function pushAlert(key, text, tone = "warn") {
  if (alertKeys.has(key)) return;
  alertKeys.add(key);
  if (alertKeys.size > 30) alertKeys.delete(alertKeys.values().next().value);
  showToast(text, tone);
}

function checkAlerts(telemetry) {
  const battery = Number(telemetry.battery_percent);
  const lte = Number(telemetry.lte_rssi);
  if (Number.isFinite(battery) && battery < 25) {
    pushAlert(`battery-${Math.floor(battery / 5)}`, `小车电量低：${battery}%`, "danger");
  }
  if (Number.isFinite(lte) && lte < -90) {
    pushAlert(`lte-${Math.floor(lte / 5)}`, `L610 信号较弱：${lte} dBm`);
  }
  if (telemetry.fault_text) {
    pushAlert(`fault-${telemetry.fault_text}`, `小车故障：${telemetry.fault_text}`, "danger");
  }
  if (els.fenceEnabled.checked && validCoord(telemetry.lat, telemetry.lng)) {
    const radius = clamp(Number(els.fenceRadius.value), 5, 500);
    const center = routes.rover[0] || currentTarget;
    const distance = distanceMeters(telemetry.lat, telemetry.lng, center.lat, center.lng);
    if (distance > radius) {
      pushAlert(`fence-${Math.floor(distance / 10)}`, `小车越出电子围栏：${distance.toFixed(1)} m`, "danger");
    }
  }
  maybeShowArrival(telemetry);
}

function maybeShowArrival(telemetry) {
  const route = routes.rover;
  if (!route.length || !validCoord(telemetry.lat, telemetry.lng)) return;
  const endpoint = route[route.length - 1];
  const distance = distanceMeters(telemetry.lat, telemetry.lng, endpoint.lat, endpoint.lng);
  const missionReached = /reached|completed/i.test(String(telemetry.mission_status || ""));
  if (distance > ARRIVAL_METERS && !missionReached) return;
  const key = `${endpoint.lat.toFixed(7)},${endpoint.lng.toFixed(7)}`;
  if (arrivalKey === key) return;
  arrivalKey = key;
  els.arrivalSummary.innerHTML = [
    ["终点", `${endpoint.lat.toFixed(7)}, ${endpoint.lng.toFixed(7)}`],
    ["位置", `${Number(telemetry.lat).toFixed(7)}, ${Number(telemetry.lng).toFixed(7)}`],
    ["误差", `${distance.toFixed(1)} m`],
  ].map(([label, value]) => (
    `<div><span>${label}</span><strong>${value}</strong></div>`
  )).join("");
  els.arrivalModal.hidden = false;
  showToast("小车已到达任务终点", "ok");
}

function initMap() {
  els.lat.value = FALLBACK.lat.toFixed(7);
  els.lng.value = FALLBACK.lng.toFixed(7);
  if (!window.L) {
    $("map").classList.add("map-unavailable");
    $("map").textContent = "地图资源离线，仍可输入坐标编辑任务";
    requestBrowserFallback();
    return;
  }
  map = L.map("map", {zoomControl: true}).setView([FALLBACK.lat, FALLBACK.lng], 17);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  roverMarker = L.circleMarker([FALLBACK.lat, FALLBACK.lng], {
    radius: 7, color: "#087f72", fillColor: "#087f72", fillOpacity: 1,
  }).addTo(map).bindTooltip("小车");
  aircraftMarker = L.circleMarker([FALLBACK.lat, FALLBACK.lng], {
    radius: 7, color: "#2463df", fillColor: "#fff", fillOpacity: 1, weight: 3,
  }).addTo(map).bindTooltip("飞机");
  targetMarker = L.circleMarker([FALLBACK.lat, FALLBACK.lng], {
    radius: 6, color: "#b25d08", fillOpacity: 0.2,
  }).addTo(map).bindTooltip("编辑点");
  roverPath = L.polyline([], {color: "#087f72", weight: 4}).addTo(map);
  aircraftPath = L.polyline([], {color: "#2463df", weight: 4, dashArray: "8 7"}).addTo(map);
  roverTrackLine = L.polyline([], {color: "#0f766e", weight: 2, opacity: 0.45}).addTo(map);
  fenceCircle = L.circle([FALLBACK.lat, FALLBACK.lng], {
    radius: 50, color: "#d92d35", fillOpacity: 0.025, opacity: 0,
  }).addTo(map);
  map.on("click", (event) => setTarget(event.latlng.lat, event.latlng.lng));
  requestBrowserFallback();
}

function requestBrowserFallback() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition((position) => {
    mapFallback = {
      lat: position.coords.latitude,
      lng: position.coords.longitude,
    };
    const telemetry = latestState?.telemetry || {};
    if (!validCoord(telemetry.lat, telemetry.lng) && map) {
      map.setView([mapFallback.lat, mapFallback.lng], 17);
      if (!fallbackMarker) {
        fallbackMarker = L.circleMarker([mapFallback.lat, mapFallback.lng], {
          radius: 5, color: "#64748b", fillOpacity: 0.35,
        }).addTo(map).bindTooltip("浏览器地图参考");
      }
    }
  }, () => {}, {timeout: 3000, maximumAge: 60000});
}

function setTarget(lat, lng) {
  if (!validCoord(lat, lng)) return;
  currentTarget = {lat: Number(lat), lng: Number(lng)};
  els.lat.value = currentTarget.lat.toFixed(7);
  els.lng.value = currentTarget.lng.toFixed(7);
  if (targetMarker) targetMarker.setLatLng([currentTarget.lat, currentTarget.lng]);
}

function updateMapFromState(telemetry, aircraft) {
  if (!map) return;
  if (validCoord(telemetry.lat, telemetry.lng)) {
    const point = {lat: Number(telemetry.lat), lng: Number(telemetry.lng)};
    roverMarker.setLatLng([point.lat, point.lng]);
    const last = roverTrack[roverTrack.length - 1];
    if (!last || distanceMeters(last.lat, last.lng, point.lat, point.lng) > 0.8) {
      roverTrack.push(point);
      roverTrack = roverTrack.slice(-300);
      roverTrackLine.setLatLngs(roverTrack.map((item) => [item.lat, item.lng]));
    }
    if (fallbackMarker) {
      map.removeLayer(fallbackMarker);
      fallbackMarker = null;
    }
  }
  if (validCoord(aircraft.lat, aircraft.lng)) {
    aircraftMarker.setLatLng([Number(aircraft.lat), Number(aircraft.lng)]);
  }
  redrawRoutes();
}

function currentVehiclePosition(vehicle) {
  const source = vehicle === "aircraft"
    ? latestState?.aircraft
    : latestState?.telemetry;
  const lat = Number(source?.lat);
  const lng = Number(source?.lng);
  return validCoord(lat, lng) ? {lat, lng} : null;
}

function segmentDistanceLabel(distance, index) {
  const prefix = index === 0 ? "距当前位置" : "距上一点";
  return Number.isFinite(distance)
    ? `${prefix} ${distance.toFixed(1)} m`
    : `${prefix} -`;
}

function redrawRoutes() {
  if (!map) return;
  roverPath.setLatLngs(routes.rover.map((point) => [point.lat, point.lng]));
  aircraftPath.setLatLngs(routes.aircraft.map((point) => [point.lat, point.lng]));
  for (const vehicle of ["rover", "aircraft"]) {
    const distances = Core.routeSegmentDistances(
      routes[vehicle],
      currentVehiclePosition(vehicle),
    );
    for (const marker of waypointMarkers[vehicle]) map.removeLayer(marker);
    waypointMarkers[vehicle].length = 0;
    routes[vehicle].forEach((point, index) => {
      const color = vehicle === "rover" ? "rover" : "aircraft";
      const distance = distances[index];
      const distanceLabel = segmentDistanceLabel(distance, index);
      const pointLabel = point.autoReturn ? "返航点" : `航点 ${index + 1}`;
      const marker = L.marker([point.lat, point.lng], {
        icon: L.divIcon({
          className: `waypoint-icon ${color}`,
          html: `<span>${index + 1}</span>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        }),
        keyboard: false,
        zIndexOffset: 500 + index,
      }).addTo(map).bindTooltip(
        `${vehicle === "rover" ? "小车" : "飞机"}${pointLabel}<br>${distanceLabel}`,
        {direction: "top", offset: [0, -12]},
      );
      waypointMarkers[vehicle].push(marker);
    });
  }
  const center = routes.rover[0] || currentTarget;
  fenceCircle.setLatLng([center.lat, center.lng]);
  fenceCircle.setRadius(clamp(Number(els.fenceRadius.value), 5, 500));
  fenceCircle.setStyle({
    opacity: els.fenceEnabled.checked ? 1 : 0,
    fillOpacity: els.fenceEnabled.checked ? 0.025 : 0,
  });
}

function setVehicleMode(vehicle) {
  selectedVehicle = vehicle === "aircraft" ? "aircraft" : "rover";
  const aircraft = selectedVehicle === "aircraft";
  els.roverMode.classList.toggle("active", !aircraft);
  els.aircraftMode.classList.toggle("active", aircraft);
  els.speedField.hidden = aircraft;
  els.altitude.hidden = !aircraft;
  els.uploadMission.textContent = aircraft ? "上传飞机任务" : "上传小车任务";
  updateMissionButton();
  renderQueue();
}

function updateMissionButton() {
  const blocked = selectedVehicle === "aircraft"
    && latestState
    && !Core.aircraftCommandsAllowed(latestState);
  els.uploadMission.disabled = Boolean(blocked);
  els.uploadMission.title = blocked ? "OPTICAL LINK BLOCKED" : "";
}

function pointFromInputs() {
  const lat = Number(els.lat.value);
  const lng = Number(els.lng.value);
  if (!validCoord(lat, lng)) throw new Error("航点坐标无效");
  const point = {lat, lng};
  if (selectedVehicle === "rover") {
    point.speed = clamp(Number(els.speed.value), 0.1, 3);
    if (!Number.isFinite(point.speed)) throw new Error("小车速度无效");
  }
  return point;
}

function addWaypoint() {
  try {
    const point = pointFromInputs();
    const route = routes[selectedVehicle];
    for (let index = route.length - 1; index >= 0; index -= 1) {
      if (route[index]?.autoReturn === true) route.splice(index, 1);
    }
    route.push(point);
    arrivalKey = "";
    renderQueue();
    redrawRoutes();
    showToast(`已加入${selectedVehicle === "rover" ? "小车" : "飞机"}航点`, "ok");
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function currentMissionIndex() {
  const telemetry = latestState?.telemetry || {};
  const source = selectedVehicle === "aircraft"
    ? `${telemetry.aircraft_mission_status || ""} ${telemetry.aircraft_tx_stage || ""}`
    : `${telemetry.mission_status || ""} ${telemetry.rover_tx_stage || ""}`;
  const match = source.match(/(?:seq|current)[=: ]+(\d+)/i);
  if (!match) return -1;
  const protocolSeq = Number(match[1]);
  return selectedVehicle === "aircraft"
    ? Math.max(0, protocolSeq - 1)
    : Math.max(0, Math.floor((protocolSeq - 2) / 2));
}

function renderQueue() {
  const route = routes[selectedVehicle];
  const current = currentMissionIndex();
  const distances = Core.routeSegmentDistances(
    route,
    currentVehiclePosition(selectedVehicle),
  );
  els.queueSummary.textContent = `${route.length} 个航点`;
  els.queue.innerHTML = route.map((point, index) => (
    `<div class="queue-row ${index === current ? "current" : ""}">`
    + `<strong>${index + 1}</strong>`
    + `<span>${point.lat.toFixed(6)}, ${point.lng.toFixed(6)}${point.autoReturn ? " · 返航" : ""}</span>`
    + `<small>${segmentDistanceLabel(distances[index], index)}</small>`
    + `<em>${selectedVehicle === "rover" ? `${point.speed.toFixed(1)} m/s` : `${Number(els.altitude.value || 0).toFixed(0)} m`}</em>`
    + "</div>"
  )).join("");
}

function clearRoute() {
  routes[selectedVehicle].length = 0;
  arrivalKey = "";
  renderQueue();
  redrawRoutes();
  showToast("当前任务队列已清空", "ok");
}

async function postCommand(command, {quiet = false} = {}) {
  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(command),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    if (!quiet) {
      addLog(`已下发 ${command.command}`);
      showToast("云端指令已下发", "ok");
    }
    return result;
  } catch (error) {
    addLog(`下发失败：${error.message}`);
    if (!quiet) showToast(`指令失败：${error.message}`, "danger");
    return {ok: false, error: error.message};
  }
}

async function uploadMission() {
  let route = routes[selectedVehicle];
  try {
    if (
      selectedVehicle === "aircraft"
      && (!latestState || !Core.aircraftCommandsAllowed(latestState))
    ) {
      throw new Error("OPTICAL LINK BLOCKED");
    }
    if (selectedVehicle === "aircraft") {
      const prepared = Core.prepareAircraftUploadRoute(route, latestState);
      routes.aircraft.splice(0, routes.aircraft.length, ...prepared);
      route = routes.aircraft;
      renderQueue();
      redrawRoutes();
    }
    const command = Core.buildMissionCommand(selectedVehicle, route, {
      altitude: Number(els.altitude.value),
    });
    const result = await postCommand(command);
    if (result.ok) {
      addLog(`${selectedVehicle} mission ${command.payload.mission_id} 共 ${route.length} 点`);
    }
  } catch (error) {
    showToast(error.message, "danger");
    addLog(`任务构造失败：${error.message}`);
  }
}

function drivePayload(steering, throttle) {
  return {command: "manual", steering, throttle};
}

function startDrive(button) {
  const payload = drivePayload(
    Number(button.dataset.steering),
    Number(button.dataset.throttle),
  );
  postCommand(payload);
  clearInterval(driveTimer);
  driveTimer = setInterval(() => postCommand(payload, {quiet: true}), 250);
}

function stopDrive() {
  if (!driveTimer) return;
  clearInterval(driveTimer);
  driveTimer = null;
  postCommand({command: "stop", steering: 0, throttle: 0}, {quiet: true});
}

function keyDrive(key) {
  if (key === "ArrowUp" || key.toLowerCase() === "w") return [0, 100];
  if (key === "ArrowDown" || key.toLowerCase() === "s") return [0, -100];
  if (key === "ArrowLeft" || key.toLowerCase() === "a") return [-100, 0];
  if (key === "ArrowRight" || key.toLowerCase() === "d") return [100, 0];
  return null;
}

async function refresh() {
  try {
    const response = await fetch("/api/state", {cache: "no-store"});
    const state = await response.json();
    if (!response.ok || state.ok === false) throw new Error(state.error || `HTTP ${response.status}`);
    renderState(state);
  } catch (error) {
    els.cloudDot.classList.remove("online");
    els.cloudText.textContent = "涂鸦云离线";
    addLog(`状态同步失败：${error.message}`);
  }
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function distanceMeters(lat1, lng1, lat2, lng2) {
  return Core.distanceMeters(lat1, lng1, lat2, lng2);
}

$("refreshBtn").onclick = refresh;
$("queueWaypointBtn").onclick = addWaypoint;
$("clearWaypointQueueBtn").onclick = clearRoute;
$("uploadMissionBtn").onclick = uploadMission;
$("clearLogBtn").onclick = () => { els.log.textContent = ""; };
$("closeArrivalModal").onclick = () => { els.arrivalModal.hidden = true; };
els.roverMode.onclick = () => setVehicleMode("rover");
els.aircraftMode.onclick = () => setVehicleMode("aircraft");
els.fenceEnabled.onchange = redrawRoutes;
els.fenceRadius.oninput = redrawRoutes;
els.altitude.oninput = renderQueue;
els.lat.onchange = () => setTarget(Number(els.lat.value), Number(els.lng.value));
els.lng.onchange = () => setTarget(Number(els.lat.value), Number(els.lng.value));

for (const button of document.querySelectorAll("[data-command]")) {
  button.onclick = () => postCommand({command: button.dataset.command});
}
for (const button of document.querySelectorAll("[data-aircraft-command]")) {
  button.onclick = () => {
    if (!latestState || !Core.aircraftCommandsAllowed(latestState)) {
      showToast("OPTICAL LINK BLOCKED", "danger");
      return;
    }
    postCommand({command: button.dataset.aircraftCommand, target: "aircraft"});
  };
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
  const drive = keyDrive(event.key);
  if (!drive || activeKey || ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  event.preventDefault();
  activeKey = event.key;
  const fakeButton = {dataset: {steering: drive[0], throttle: drive[1]}};
  startDrive(fakeButton);
});
window.addEventListener("keyup", (event) => {
  if (event.key !== activeKey) return;
  event.preventDefault();
  activeKey = "";
  stopDrive();
});

setVehicleMode("rover");
initMap();
refresh();
setInterval(refresh, 1000);
