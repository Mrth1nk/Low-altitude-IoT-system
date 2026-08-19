const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const {
  aircraftCommandsAllowed,
  filterCommandProperties,
  isAircraftCommand,
  normalizeCloudState,
  validCoordinate,
} = require("./public/core.js");

const PORT = Number(process.env.PORT || 5178);
const HOST = process.env.HOST || "0.0.0.0";
const TUYA_HOST = process.env.TUYA_HOST || "https://openapi.tuyacn.com";
const DEVICE_ID = process.env.TUYA_DEVICE_ID || "262c1deefdc2650445bwpd";
const ACCESS_ID = process.env.TUYA_ACCESS_ID || "";
const ACCESS_SECRET = process.env.TUYA_ACCESS_SECRET || "";
const FAKE_TUYA = process.env.TUYA_FAKE === "1";
const PUBLIC_DIR = path.join(__dirname, "public");

let cachedToken = null;
let lastState = null;
let fakeRevision = 0;
let cloudStateReader = null;

const STATE_CACHE_TTL_MS = Number(process.env.TUYA_STATE_CACHE_MS || 2000);
const STATE_ENDPOINTS = [
  {
    path: `/v2.0/cloud/thing/${DEVICE_ID}/shadow/properties`,
    pick: (doc) => doc.result && doc.result.properties,
  },
  {
    path: `/v2.0/cloud/thing/${DEVICE_ID}/state`,
    pick: (doc) => doc.result && (doc.result.properties || doc.result.status || doc.result),
  },
];

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function hmac(value) {
  return crypto.createHmac("sha256", ACCESS_SECRET).update(value).digest("hex").toUpperCase();
}

function signHeaders(method, urlPath, body = "", token = "") {
  const t = String(Date.now());
  const nonce = crypto.randomUUID().replace(/-/g, "");
  const contentHash = sha256(body);
  const stringToSign = `${method}\n${contentHash}\n\n${urlPath}`;
  const raw = token ? `${ACCESS_ID}${token}${t}${nonce}${stringToSign}` : `${ACCESS_ID}${t}${nonce}${stringToSign}`;
  const headers = {
    client_id: ACCESS_ID,
    sign: hmac(raw),
    sign_method: "HMAC-SHA256",
    t,
    nonce,
    "Content-Type": "application/json",
    lang: "zh",
  };
  if (token) headers.access_token = token;
  return headers;
}

async function tuyaRequest(method, urlPath, bodyObj, token) {
  const body = bodyObj ? JSON.stringify(bodyObj) : "";
  const res = await fetch(`${TUYA_HOST}${urlPath}`, {
    method,
    headers: signHeaders(method, urlPath, body, token),
    body: body || undefined,
  });
  const text = await res.text();
  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    doc = {success: false, msg: text};
  }
  if (!res.ok || doc.success === false) {
    const message = doc.msg || doc.message || `HTTP ${res.status}`;
    const err = new Error(message);
    err.response = doc;
    throw err;
  }
  return doc;
}

function isTokenError(err) {
  const code = String(err?.response?.code || "");
  const message = String(err?.message || err?.response?.msg || "").toLowerCase();
  return code === "1010" || code === "1011" || message.includes("token invalid");
}

async function tuyaRequestWithToken(method, urlPath, bodyObj) {
  let token = await getToken();
  try {
    return await tuyaRequest(method, urlPath, bodyObj, token);
  } catch (err) {
    if (!isTokenError(err)) throw err;
    cachedToken = null;
    token = await getToken();
    return tuyaRequest(method, urlPath, bodyObj, token);
  }
}

async function getToken() {
  if (cachedToken && Date.now() < cachedToken.expiresAt) return cachedToken.value;
  if (!ACCESS_ID || !ACCESS_SECRET) {
    throw new Error("请先设置 TUYA_ACCESS_ID 和 TUYA_ACCESS_SECRET");
  }
  const doc = await tuyaRequest("GET", "/v1.0/token?grant_type=1");
  const token = doc.result && doc.result.access_token;
  if (!token) throw new Error("涂鸦 token 响应缺少 access_token");
  cachedToken = {
    value: token,
    expiresAt: Date.now() + Math.max(60, Number(doc.result.expire_time || 7200) - 60) * 1000,
  };
  return token;
}

function normalizeStatus(result, receivedAtMs) {
  return normalizeCloudState(result, receivedAtMs);
}

function preserveAircraftDetails(previous, next) {
  const roverPresent = next?.properties_present?.rover_state !== false;
  const slavePresent = next?.properties_present?.slave_state === true;
  if (previous && !roverPresent) {
    next = {
      ...next,
      telemetry: {...(previous.telemetry || {})},
      aircraft: {...(previous.aircraft || {})},
      optical: {...(previous.optical || {})},
    };
  }
  if (previous && !slavePresent) {
    next = {...next, slave: {...(previous.slave || {})}};
  }
  const aircraftLinkActive = next?.telemetry?.aircraft_link === true;
  const nextHasDetails = Boolean(
    next?.aircraft?.link_active
    || next?.aircraft?.mode
    || (Array.isArray(next?.aircraft?.messages) && next.aircraft.messages.length),
  );
  const previousHasDetails = Boolean(
    previous?.aircraft?.link_active
    || previous?.aircraft?.mode
    || (Array.isArray(previous?.aircraft?.messages) && previous.aircraft.messages.length),
  );
  const merged = aircraftLinkActive && !nextHasDetails && previousHasDetails
    ? {...next, aircraft: {...previous.aircraft}}
    : {
        ...next,
        aircraft: next?.aircraft ? {...next.aircraft} : next?.aircraft,
      };

  const retainPosition = (previousValue, nextValue) => {
    if (nextValue?.position_observed === true) return nextValue;
    const nextLat = Number(nextValue?.lat);
    const nextLng = Number(nextValue?.lng);
    if (validCoordinate(nextLat, nextLng)) return nextValue;
    const previousLat = Number(previousValue?.lat);
    const previousLng = Number(previousValue?.lng);
    if (!validCoordinate(previousLat, previousLng)) return nextValue;
    return {...nextValue, lat: previousLat, lng: previousLng};
  };

  merged.telemetry = retainPosition(previous?.telemetry, next?.telemetry || {});
  merged.aircraft = retainPosition(previous?.aircraft, merged.aircraft || {});
  merged.slave = retainPosition(previous?.slave, next?.slave || {});
  merged.raw = {...(previous?.raw || {}), ...(next?.raw || {})};
  return merged;
}

function createStateReader({
  request,
  normalize = normalizeStatus,
  preserve = preserveAircraftDetails,
  endpoints = STATE_ENDPOINTS,
  cacheTtlMs = STATE_CACHE_TTL_MS,
  now = Date.now,
} = {}) {
  let cachedState = null;
  let cachedResult = null;
  let lastAttemptAt = Number.NEGATIVE_INFINITY;
  let inFlight = null;

  const serve = (state) => {
    const served = {
      ...state,
      telemetry: {...(state?.telemetry || {})},
      aircraft: {...(state?.aircraft || {})},
      slave: {...(state?.slave || {})},
    };
    const updatedAt = Number(served.telemetry.updated_at || 0);
    served.state_age_sec = updatedAt > 0
      ? Math.max(0, now() / 1000 - updatedAt)
      : null;
    served.state_fresh = served.state_age_sec !== null && served.state_age_sec <= 8;
    const slaveUpdatedAt = Number(served.slave.updated_at || 0);
    served.slave.state_age_sec = slaveUpdatedAt > 0
      ? Math.max(0, now() / 1000 - slaveUpdatedAt)
      : null;
    served.slave.state_fresh = served.slave.state_age_sec !== null
      && served.slave.state_age_sec <= 3;
    if (!served.slave.state_fresh) {
      served.slave.online = false;
      served.slave.link_active = false;
      served.slave.status = "OFFLINE";
    }
    return served;
  };

  const load = async () => {
    const errors = [];
    for (const endpoint of endpoints) {
      try {
        const doc = await request(endpoint.path);
        const picked = endpoint.pick(doc);
        if (picked === null || picked === undefined) {
          throw new Error("Tuya state response has no properties");
        }
        const state = normalize(picked, now());
        state.source = endpoint.path;
        state.cloud_degraded = false;
        delete state.cloud_error;
        cachedState = preserve(cachedState, state);
        cachedResult = cachedState;
        return serve(cachedResult);
      } catch (err) {
        errors.push({path: endpoint.path, error: err.message, detail: err.response || null});
      }
    }

    if (cachedState) {
      cachedResult = {
        ...cachedState,
        cloud_degraded: true,
        cloud_error: errors.map((item) => item.error).filter(Boolean).join("; "),
      };
      return serve(cachedResult);
    }

    const err = new Error("所有涂鸦状态接口都返回失败");
    err.response = errors;
    throw err;
  };

  return async function readState() {
    const current = now();
    if (cachedResult && current - lastAttemptAt < cacheTtlMs) {
      return serve(cachedResult);
    }
    if (inFlight) return inFlight;
    lastAttemptAt = current;
    inFlight = load().finally(() => {
      inFlight = null;
    });
    return inFlight;
  };
}

async function fetchState() {
  if (FAKE_TUYA) {
    lastState = fakeState();
    return lastState;
  }
  if (!cloudStateReader) {
    cloudStateReader = createStateReader({
      request: (urlPath) => tuyaRequestWithToken("GET", urlPath, null),
    });
  }
  lastState = await cloudStateReader();
  return lastState;
}

function buildIssueBody(properties) {
  return {properties: JSON.stringify(properties)};
}

function buildCommandsBody(properties) {
  return {
    commands: Object.entries(properties).map(([code, value]) => ({code, value})),
  };
}

function buildAircraftMissionFragments(command) {
  if (command?.command !== "aircraft_mission" || !Array.isArray(command?.payload?.items)) {
    throw new Error("aircraft mission items are required");
  }
    const commandId = command.command_id || crypto.randomUUID();
    const missionId = String(command.payload.mission_id || `aircraft-${Date.now()}`);
    const target = command.target === "aircraft_2" ? "aircraft_2" : "aircraft";
    const items = command.payload.items.map((item, index) => ({
      mission_id: missionId,
      index,
      lat: Number(item.lat),
      lon: Number(item.lon ?? item.lng),
      alt: Number(item.alt),
      command: Number(item.command ?? 16),
      frame: Number(item.frame ?? 6),
      param1: Number(item.param1 ?? 0),
      param2: Number(item.param2 ?? 0),
      param3: Number(item.param3 ?? 0),
      param4: Number(item.param4 ?? 0),
      autocontinue: item.autocontinue !== false,
    }));
    // Match Python json.dumps(..., sort_keys=True, separators=(",", ":")).
    // Mission floats must keep ".0" because the aircraft-side verifier hashes
    // normalized Python floats, while JSON.stringify(10.0) emits "10".
    const floatKeys = new Set(["lat", "lon", "alt", "param1", "param2", "param3", "param4"]);
    const stable = (value, key = "") => Array.isArray(value)
      ? `[${value.map((entry) => stable(entry)).join(",")}]`
      : value && typeof value === "object"
        ? `{${Object.keys(value).sort().map((name) => `${JSON.stringify(name)}:${stable(value[name], name)}`).join(",")}}`
        : typeof value === "number" && Number.isFinite(value)
          ? (floatKeys.has(key) && Number.isInteger(value) ? `${value}.0` : JSON.stringify(value))
          : JSON.stringify(value);
    const checksum = crypto.createHash("sha256")
      .update(stable(items))
      .digest("hex");
    return [
      {command: "aircraft_mission_begin", command_id: commandId, target,
        payload: {mission_id: missionId, item_count: items.length, vehicle: target, checksum}},
      ...items.map((item) => ({command: "aircraft_mission_item", command_id: commandId, target, payload: item})),
      {command: "aircraft_mission_commit", command_id: commandId, target,
        payload: {mission_id: missionId, item_count: items.length, checksum}},
    ];
}

async function sendCommand(command, options = {}) {
  if (!options.skipGate && isAircraftCommand(command)) {
    const gateState = FAKE_TUYA ? lastState : await fetchState();
    const target = command.target === "aircraft_2" ? "aircraft_2" : "aircraft";
    if (!gateState || !aircraftCommandsAllowed(gateState, target)) {
      throw new Error(target === "aircraft_2" ? "SLAVE OFFLINE" : "OPTICAL LINK BLOCKED");
    }
  }
  if (command?.command === "aircraft_mission" && Array.isArray(command?.payload?.items)) {
    const fragments = buildAircraftMissionFragments(command);
    const results = [];
    for (const fragment of fragments) {
      results.push(await sendCommand(fragment, {skipGate: true}));
      await new Promise((resolve) => setTimeout(resolve, 450));
    }
    return {success: true, fragmented: true, count: fragments.length, results};
  }
  const properties = filterCommandProperties(command);
  if (!Object.keys(properties).length) {
    throw new Error("没有可下发的涂鸦属性");
  }
  if (FAKE_TUYA) {
    fakeRevision += 1;
    return {success: true, fake: true, revision: fakeRevision, properties};
  }
  return tuyaRequestWithToken(
    "POST",
    `/v1.0/iot-03/devices/${DEVICE_ID}/commands`,
    buildCommandsBody(properties),
  );
}

function fakeState() {
  const now = Date.now() / 1000;
  const offset = (fakeRevision % 8) * 0.000015;
  return normalizeCloudState([
    {
      code: "rover_state",
      value: JSON.stringify({
        updated_at: now,
        lat: 32.11956 + offset,
        lng: 118.958406 + offset,
        ground_speed: fakeRevision ? 0.7 : 0,
        heading: 86,
        battery_percent: 82,
        armed: false,
        flight_mode: "HOLD",
        lte_rssi: -61,
        fc_link: true,
        gps_fix_type: 3,
        satellites_visible: 12,
        mission_status: fakeRevision ? "mission active seq=2" : "idle",
        rover_tx_stage: fakeRevision ? "verified" : "idle",
        optical_state: "locked",
        aircraft_tx_stage: fakeRevision ? "VERIFIED" : "idle",
        aircraft_mission_status: fakeRevision ? "execution_ready" : "idle",
        aircraft: {
          link_active: true,
          mode: "GUIDED",
          armed: false,
          battery_percent: 76,
          lat: 32.11961,
          lng: 118.95847,
          altitude: 18.2,
          ground_speed: 0.3,
          messages: [
            {time: now - 2, sequence: fakeRevision * 2, type: "HEARTBEAT", text: "GUIDED armed=NO"},
            {time: now, sequence: fakeRevision * 2 + 1, type: "STATUS", text: "optical target locked"},
          ],
        },
      }),
    },
    {
      code: "slave_state",
      value: JSON.stringify({
        updated_at: now,
        online: true,
        fc_connected: true,
        blocked: false,
        mode: "LOITER",
        armed: false,
        battery: 100,
        lat: 32.11966,
        lon: 118.95852,
        position_observed: true,
        altitude: 16.4,
        speed: 0.2,
        heading: 115,
        mission_stage: "READY",
        event: {timestamp: now, sequence: fakeRevision, type: "HEARTBEAT", text: "LOITER armed=NO"},
      }),
    },
    {code: "command", value: "noop"},
  ]);
}

async function createSpace(name) {
  return tuyaRequestWithToken("POST", "/v2.0/cloud/space/creation", {
    name,
    description: "RDK rover TuyaLink demo space",
  });
}

async function createAsset(name) {
  return tuyaRequestWithToken("POST", "/v1.0/iot-02/assets", {name});
}

async function bindDeviceToAsset(assetId, bindCode) {
  return tuyaRequestWithToken("POST", "/v1.1/iot-02/device-bc-bind", {
    asset_id: String(assetId),
    device_bind_code_list: String(bindCode),
  });
}

function sendJson(res, status, doc) {
  const body = JSON.stringify(doc);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function serveStatic(req, res) {
  const reqPath = new URL(req.url, `http://${req.headers.host}`).pathname;
  const filePath = path.join(PUBLIC_DIR, reqPath === "/" ? "index.html" : reqPath);
  if (!filePath.startsWith(PUBLIC_DIR) || !fs.existsSync(filePath)) {
    res.writeHead(404);
    res.end("not found");
    return;
  }
  const ext = path.extname(filePath);
  const type = ext === ".js" ? "application/javascript" : ext === ".css" ? "text/css" : "text/html";
  res.writeHead(200, {"Content-Type": `${type}; charset=utf-8`});
  fs.createReadStream(filePath).pipe(res);
}

function createServer() {
  return http.createServer(async (req, res) => {
  try {
    if (req.url.startsWith("/api/state")) {
      sendJson(res, 200, await fetchState());
      return;
    }
    if (req.url.startsWith("/api/command") && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", async () => {
        try {
          sendJson(res, 200, {ok: true, result: await sendCommand(JSON.parse(body || "{}"))});
        } catch (err) {
          sendJson(res, 500, {ok: false, error: err.message, detail: err.response || null});
        }
      });
      return;
    }
    if (req.url.startsWith("/api/space") && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", async () => {
        try {
          const payload = JSON.parse(body || "{}");
          sendJson(res, 200, {ok: true, result: await createSpace(payload.name || "rover-demo")});
        } catch (err) {
          sendJson(res, 500, {ok: false, error: err.message, detail: err.response || null});
        }
      });
      return;
    }
    if (req.url.startsWith("/api/asset") && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", async () => {
        try {
          const payload = JSON.parse(body || "{}");
          sendJson(res, 200, {ok: true, result: await createAsset(payload.name || "rover-demo")});
        } catch (err) {
          sendJson(res, 500, {ok: false, error: err.message, detail: err.response || null});
        }
      });
      return;
    }
    if (req.url.startsWith("/api/bind") && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", async () => {
        try {
          const payload = JSON.parse(body || "{}");
          sendJson(res, 200, {ok: true, result: await bindDeviceToAsset(payload.asset_id, payload.bind_code)});
        } catch (err) {
          sendJson(res, 500, {ok: false, error: err.message, detail: err.response || null});
        }
      });
      return;
    }
    serveStatic(req, res);
  } catch (err) {
    sendJson(res, 500, {ok: false, error: err.message, detail: err.response || null});
  }
  });
}

function startServer() {
  const server = createServer();
  server.listen(PORT, HOST, () => {
    console.log(`Tuya cloud ground station: http://127.0.0.1:${PORT}/`);
  });
  return server;
}

if (require.main === module) startServer();

module.exports = {
  buildAircraftMissionFragments,
  buildCommandsBody,
  buildIssueBody,
  createStateReader,
  createServer,
  fetchState,
  normalizeStatus,
  preserveAircraftDetails,
  sendCommand,
  signHeaders,
  startServer,
};
