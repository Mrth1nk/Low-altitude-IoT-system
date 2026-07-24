(function initGroundStationCore(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.GroundStationCore = api;
})(typeof globalThis === "object" ? globalThis : this, function groundStationCore() {
  "use strict";

  const COMMAND_PROPERTY_CODES = new Set([
    "action",
    "command",
    "command_id",
    "payload",
    "source_timestamp",
    "steering",
    "target",
    "target_lat",
    "target_lng",
    "target_speed",
    "throttle",
  ]);
  const AIRCRAFT_PREFIX = "aircraft_";
  const MAX_MISSION_ITEMS = 100;
  const MAV_CMD_NAV_WAYPOINT = 16;
  const MAV_CMD_DO_CHANGE_SPEED = 178;
  const MAV_FRAME_MISSION = 2;
  const MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6;

  function objectValue(value) {
    return value && typeof value === "object" && "value" in value
      ? value.value
      : value;
  }

  function propertyMap(result) {
    const list = Array.isArray(result)
      ? result
      : Array.isArray(result?.properties)
        ? result.properties
        : [];
    const byCode = {};
    for (const item of list) {
      const code = item?.code || item?.dp_id || item?.name;
      if (code) byCode[code] = objectValue(item.value);
    }
    return byCode;
  }

  function parseRoverState(raw) {
    if (raw && typeof raw === "object") return {...raw};
    if (typeof raw !== "string") return {};
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {raw_rover_state: raw};
    }
  }

  function opticalBlocked(telemetry) {
    const aircraft = telemetry.aircraft || {};
    const explicit = String(
      telemetry.optical_state
      || telemetry.link_state
      || aircraft.optical_state
      || aircraft.state
      || "",
    ).toLowerCase();
    if (explicit.includes("blocked") || explicit.includes("interrupted")) return true;
    const messages = Array.isArray(aircraft.messages) ? aircraft.messages : [];
    return messages.some((item) => /LINK_BLOCKED|SIGNAL_INTERRUPTED|SIGNAL_INT/i.test(
      `${item?.type || ""} ${item?.text || ""}`,
    ));
  }

  function normalizeAircraft(telemetry, receivedAt) {
    const source = telemetry.aircraft && typeof telemetry.aircraft === "object"
      ? telemetry.aircraft
      : {
          link_active: Boolean(telemetry.aircraft_link),
          last_seen_age_sec: telemetry.aircraft_age,
          packets_received: telemetry.aircraft_packets,
          messages: telemetry.aircraft_msg
            ? [{
                time: Number(telemetry.aircraft_msg_time) || receivedAt,
                type: "AIRCRAFT",
                text: String(telemetry.aircraft_msg),
              }]
            : [],
        };
    if (opticalBlocked(telemetry)) {
      return {blocked: true, status: "OPTICAL LINK BLOCKED", messages: []};
    }
    return {
      ...source,
      blocked: false,
      status: source.link_active ? "LOCKED" : "WAITING",
      messages: Array.isArray(source.messages) ? source.messages : [],
    };
  }

  function normalizeCloudState(result, receivedAtMs = Date.now()) {
    const raw = propertyMap(result);
    const telemetry = parseRoverState(raw.rover_state);
    const cloudReceivedAt = Number(receivedAtMs) / 1000;
    const aircraft = normalizeAircraft(telemetry, cloudReceivedAt);
    return {
      online: true,
      updated_at: cloudReceivedAt,
      cloud_received_at: cloudReceivedAt,
      telemetry,
      raw,
      optical: {
        blocked: aircraft.blocked,
        state: aircraft.blocked ? "blocked" : "locked",
      },
      aircraft,
    };
  }

  function messageIdentity(item, fallbackTime) {
    const time = Number(item?.time) || Number(item?.received_at) || fallbackTime;
    const type = String(item?.type || "MSG");
    const text = String(item?.text || "");
    const sequence = item?.sequence ?? item?.seq ?? "";
    return {
      time,
      type,
      text,
      key: String(item?.key || `${time}\n${sequence}\n${type}\n${text}`),
    };
  }

  function appendAircraftMessages(previous, incoming, cloudReceivedAt, limit = 120) {
    const result = Array.isArray(previous) ? previous.slice() : [];
    const seen = new Set(result.map((item) => item.key));
    for (const raw of Array.isArray(incoming) ? incoming : []) {
      const item = messageIdentity(raw, Number(cloudReceivedAt));
      if (!item.text || seen.has(item.key)) continue;
      seen.add(item.key);
      result.push(item);
    }
    return result.slice(-Math.max(1, Number(limit) || 120));
  }

  function validCoordinate(lat, lon) {
    return Number.isFinite(lat)
      && Number.isFinite(lon)
      && Math.abs(lat) <= 90
      && Math.abs(lon) <= 180
      && (Math.abs(lat) > 0.000001 || Math.abs(lon) > 0.000001);
  }

  function normalizePoint(point, index) {
    const lat = Number(point?.lat);
    const lon = Number(point?.lng ?? point?.lon);
    if (!validCoordinate(lat, lon)) {
      throw new ValueError(`mission point ${index + 1} has invalid coordinate`);
    }
    return {lat, lon};
  }

  class ValueError extends Error {
    constructor(message) {
      super(message);
      this.name = "ValueError";
    }
  }

  function commonMissionItem(point, overrides = {}) {
    return {
      lat: point.lat,
      lon: point.lon,
      alt: Number(overrides.alt ?? 0),
      command: Number(overrides.command ?? MAV_CMD_NAV_WAYPOINT),
      frame: Number(overrides.frame ?? MAV_FRAME_GLOBAL_RELATIVE_ALT_INT),
      param1: Number(overrides.param1 ?? 0),
      param2: Number(overrides.param2 ?? 0),
      param3: Number(overrides.param3 ?? 0),
      param4: Number(overrides.param4 ?? 0),
      autocontinue: true,
    };
  }

  function buildMissionCommand(vehicle, rawPoints, options = {}) {
    const target = vehicle === "aircraft" ? "aircraft" : vehicle === "rover" ? "rover" : "";
    if (!target) throw new ValueError("mission target must be rover or aircraft");
    if (!Array.isArray(rawPoints) || rawPoints.length === 0) {
      throw new ValueError("mission requires at least one point");
    }
    const points = rawPoints.map(normalizePoint);
    const items = [];
    if (target === "rover") {
      for (let index = 0; index < points.length; index += 1) {
        const speed = Number(rawPoints[index]?.speed);
        if (!Number.isFinite(speed) || speed < 0.1 || speed > 3) {
          throw new ValueError(`mission point ${index + 1} has invalid speed`);
        }
        items.push(commonMissionItem({lat: 0.000001, lon: 0.000001}, {
          command: MAV_CMD_DO_CHANGE_SPEED,
          frame: MAV_FRAME_MISSION,
          param1: 1,
          param2: speed,
        }));
        items.push(commonMissionItem(points[index]));
      }
    } else {
      const altitude = Number(options.altitude);
      if (!Number.isFinite(altitude) || altitude < 1 || altitude > 120) {
        throw new ValueError("aircraft altitude must be between 1 and 120 m");
      }
      for (const point of points) items.push(commonMissionItem(point, {alt: altitude}));
    }
    if (items.length > MAX_MISSION_ITEMS) {
      throw new ValueError(`mission exceeds ${MAX_MISSION_ITEMS} mission items`);
    }
    const commandId = String(options.commandId || makeId());
    const missionId = String(options.missionId || `${target}-${Date.now()}`);
    return {
      command: target === "aircraft" ? "aircraft_mission" : "mission",
      target,
      command_id: commandId,
      source_timestamp: Number(options.timestamp ?? Date.now()),
      payload: {
        mission_id: missionId,
        vehicle: target,
        items,
      },
    };
  }

  function makeId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
      const random = Math.floor(Math.random() * 16);
      return (char === "x" ? random : (random & 3) | 8).toString(16);
    });
  }

  function filterCommandProperties(command) {
    const result = {};
    if (!command || typeof command !== "object") return result;
    for (const [code, value] of Object.entries(command)) {
      if (
        COMMAND_PROPERTY_CODES.has(code)
        && value !== undefined
        && value !== null
        && value !== ""
      ) result[code] = value;
    }
    return result;
  }

  function aircraftCommandsAllowed(state) {
    return Boolean(state?.online && !state?.optical?.blocked);
  }

  function transactionTimeline(telemetry, vehicle) {
    const aircraft = vehicle === "aircraft";
    const stage = String(
      aircraft ? telemetry?.aircraft_tx_stage : telemetry?.rover_tx_stage,
    ).toLowerCase();
    const status = String(
      aircraft ? telemetry?.aircraft_mission_status : telemetry?.mission_status,
    ).toLowerCase();
    const error = String(
      (aircraft ? telemetry?.aircraft_fault_text : telemetry?.fault_text) || "",
    );
    const keys = ["upload", "verified", "ready", "auto", "reached"];
    const rank = {
      idle: -1,
      queued: 0,
      staged: 0,
      uploading: 0,
      awaiting_ack: 0,
      acknowledged: 0,
      fc_uploaded: 0,
      verified: 1,
      ready: 2,
      execution_ready: 2,
      auto: 3,
      executing: 3,
      endpoint_reached: 4,
      reached: 4,
      completed: 4,
    };
    const combined = `${stage} ${status}`;
    const failed = /fail|reject|nack|timeout|unsafe|mismatch/.test(combined) || Boolean(error);
    let currentRank = rank[stage] ?? -1;
    if (/verified/.test(combined)) currentRank = Math.max(currentRank, 1);
    if (/execution.ready|ready=true/.test(combined)) currentRank = Math.max(currentRank, 2);
    if (/\bauto\b|executing|mission active/.test(combined)) currentRank = Math.max(currentRank, 3);
    if (/reached|completed/.test(combined)) currentRank = Math.max(currentRank, 4);
    return keys.map((key, index) => {
      let itemState = index <= currentRank ? "done" : "idle";
      if (index === 2 && currentRank === 1) itemState = "waiting";
      if (failed && index === Math.max(0, currentRank + 1)) itemState = "failed";
      return {
        key,
        label: {
          upload: "上传",
          verified: "验证",
          ready: "就绪",
          auto: "AUTO",
          reached: "到达",
        }[key],
        state: itemState,
        detail: itemState === "failed" ? error || status || stage : "",
      };
    });
  }

  function isAircraftCommand(command) {
    if (!command || typeof command !== "object") return false;
    return command.target === "aircraft"
      || String(command.command || command.action || "").startsWith(AIRCRAFT_PREFIX);
  }

  return {
    MAX_MISSION_ITEMS,
    appendAircraftMessages,
    aircraftCommandsAllowed,
    buildMissionCommand,
    filterCommandProperties,
    isAircraftCommand,
    normalizeCloudState,
    transactionTimeline,
    validCoordinate,
  };
});
