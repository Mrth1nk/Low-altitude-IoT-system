const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const {
  aircraftCommandsAllowed,
  filterCommandProperties,
  isAircraftCommand,
  normalizeCloudState,
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

function normalizeStatus(result) {
  return normalizeCloudState(result);
}

async function fetchState() {
  if (FAKE_TUYA) {
    lastState = fakeState();
    return lastState;
  }
  const endpoints = [
    {
      path: `/v2.0/cloud/thing/${DEVICE_ID}/shadow/properties`,
      pick: (doc) => doc.result && doc.result.properties,
    },
    {
      path: `/v2.0/cloud/thing/${DEVICE_ID}/state`,
      pick: (doc) => doc.result && (doc.result.properties || doc.result.status || doc.result),
    },
    {
      path: `/v1.0/iot-03/devices/${DEVICE_ID}/status`,
      pick: (doc) => doc.result,
    },
  ];

  const errors = [];
  for (const endpoint of endpoints) {
    try {
      const doc = await tuyaRequestWithToken("GET", endpoint.path, null);
      const state = normalizeStatus(endpoint.pick(doc));
      state.source = endpoint.path;
      lastState = state;
      return state;
    } catch (err) {
      errors.push({path: endpoint.path, error: err.message, detail: err.response || null});
    }
  }

  const err = new Error("所有涂鸦状态接口都返回失败");
  err.response = errors;
  throw err;
}

async function sendCommand(command) {
  const properties = filterCommandProperties(command);
  if (!Object.keys(properties).length) {
    throw new Error("没有可下发的涂鸦属性");
  }
  if (isAircraftCommand(properties) && lastState && !aircraftCommandsAllowed(lastState)) {
    throw new Error("OPTICAL LINK BLOCKED");
  }
  if (FAKE_TUYA) {
    fakeRevision += 1;
    return {success: true, fake: true, revision: fakeRevision, properties};
  }
  return tuyaRequestWithToken("POST", `/v2.0/cloud/thing/${DEVICE_ID}/shadow/properties/issue`, {properties});
}

function fakeState() {
  const now = Date.now() / 1000;
  const offset = (fakeRevision % 8) * 0.000015;
  return normalizeCloudState([
    {
      code: "rover_state",
      value: JSON.stringify({
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
  createServer,
  fetchState,
  normalizeStatus,
  sendCommand,
  signHeaders,
  startServer,
};
