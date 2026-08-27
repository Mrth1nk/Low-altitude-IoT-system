"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  appendAircraftMessages,
  aircraftCommandsAllowed,
  aircraftHeartbeatSummary,
  composeDriveKeys,
  normalizeCloudState,
  formatObservedPosition,
  transactionTimeline,
} = require("../public/core.js");

test("combines simultaneous WASD keys into rover steering and throttle", () => {
  assert.deepEqual(composeDriveKeys(new Set(["w", "a"])), [-100, 100]);
  assert.deepEqual(composeDriveKeys(new Set(["S", "D"])), [100, -100]);
});

test("opposite drive keys cancel independently and unrelated keys are ignored", () => {
  assert.deepEqual(composeDriveKeys(new Set(["w", "s", "a"])), [-100, 0]);
  assert.equal(composeDriveKeys(new Set(["w", "s", "a", "d"])), null);
  assert.equal(composeDriveKeys(new Set(["Shift", "x"])), null);
});

test("one cloud response independently normalizes main and slave aircraft state", () => {
  const state = normalizeCloudState([
    {code: "rover_state", value: JSON.stringify({
      updated_at: 1710000000,
      aircraft_link: true,
      aircraft: {link_active: true, mode: "GUIDED", armed: false},
    })},
    {code: "slave_state", value: JSON.stringify({
      updated_at: 1710000000.5,
      online: true,
      fc_connected: true,
      blocked: false,
      mode: "LOITER",
      armed: true,
      lat: 32.12,
      lon: 118.95,
      position_observed: true,
      mission_stage: "VERIFIED",
      event: {timestamp: 1710000000.5, sequence: 7, type: "HEARTBEAT", text: "LOITER armed=YES"},
    })},
  ], 1710000001000);

  assert.equal(state.aircraft.mode, "GUIDED");
  assert.equal(state.slave.mode, "LOITER");
  assert.equal(state.slave.lng, 118.95);
  assert.equal(state.slave.state_fresh, true);
  assert.equal(aircraftCommandsAllowed(state, "aircraft"), true);
  assert.equal(aircraftCommandsAllowed(state, "aircraft_2"), true);
});

test("stale slave disables only slave commands", () => {
  const state = normalizeCloudState([
    {code: "rover_state", value: JSON.stringify({
      updated_at: 1710000000,
      aircraft_link: true,
      aircraft: {link_active: true, mode: "GUIDED", armed: false},
    })},
    {code: "slave_state", value: JSON.stringify({
      updated_at: 1709999990,
      online: true,
      fc_connected: true,
      mode: "LOITER",
    })},
  ], 1710000001000);

  assert.equal(state.slave.state_fresh, false);
  assert.equal(state.slave.status, "OFFLINE");
  assert.equal(aircraftCommandsAllowed(state, "aircraft_2"), false);
  assert.equal(aircraftCommandsAllowed(state, "aircraft"), true);
});

test("slave stays commandable across Tuya report and cache latency", () => {
  const state = normalizeCloudState([
    {code: "rover_state", value: JSON.stringify({updated_at: 1710000004})},
    {code: "slave_state", value: JSON.stringify({
      updated_at: 1710000000,
      online: true,
      fc_connected: true,
      blocked: false,
    })},
  ], 1710000004500);

  assert.equal(state.slave.state_age_sec, 4.5);
  assert.equal(state.slave.state_fresh, true);
  assert.equal(state.slave.online, true);
  assert.equal(aircraftCommandsAllowed(state, "aircraft_2"), true);
});

test("offline slave clears a previously blocked optical state", () => {
  const state = normalizeCloudState([
    {code: "rover_state", value: JSON.stringify({updated_at: 1710000010})},
    {code: "slave_state", value: JSON.stringify({
      updated_at: 1710000009.5,
      online: false,
      fc_connected: true,
      blocked: true,
    })},
  ], 1710000010000);

  assert.equal(state.slave.state_fresh, true);
  assert.equal(state.slave.online, false);
  assert.equal(state.slave.blocked, false);
  assert.equal(state.slave.status, "OFFLINE");
});

test("formats observed zero position without making it navigable", () => {
  assert.equal(formatObservedPosition({lat: 0, lng: 0, position_observed: true}), "0.00000, 0.00000");
  assert.equal(formatObservedPosition({lat: 0, lng: 0, position_observed: false}), "-");
  assert.equal(
    formatObservedPosition({lat: 0, lng: 0, position_observed: false}, 5, true),
    "0.00000, 0.00000",
  );
});

test("ground station omits mission transaction lamps", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "../public/index.html"),
    "utf8",
  );
  const app = fs.readFileSync(
    path.join(__dirname, "../public/app.js"),
    "utf8",
  );
  assert.doesNotMatch(html, /id="(?:rover|aircraft)Timeline"/);
  assert.doesNotMatch(app, /renderTimeline|(?:rover|aircraft)Timeline/);
});

test("hidden UI states cannot be overridden by component display rules", () => {
  const css = fs.readFileSync(
    path.join(__dirname, "../public/style.css"),
    "utf8",
  );
  assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/);
});

test("ground station exposes three map targets and two aircraft tabs", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "../public/index.html"),
    "utf8",
  );
  assert.match(html, /id="roverWaypointMode"[^>]*>\s*小车/);
  assert.match(html, /id="aircraftWaypointMode"[^>]*>\s*主机/);
  assert.match(html, /id="slaveWaypointMode"[^>]*>\s*从机/);
  assert.match(html, /id="mainAircraftTab"[^>]*>\s*主机 1/);
  assert.match(html, /id="slaveAircraftTab"[^>]*>\s*从机 2/);
});

test("normalizes rover_state while preserving filtered raw properties", () => {
  const state = normalizeCloudState([
    {
      code: "rover_state",
      value: JSON.stringify({
        lat: 32.12,
        lng: 118.95,
        flight_mode: "AUTO",
        optical_state: "locked",
        updated_at: 1709999998,
      }),
    },
    {code: "command", value: "auto"},
  ], 1710000000000);

  assert.equal(state.telemetry.flight_mode, "AUTO");
  assert.equal(state.raw.command, "auto");
  assert.equal(state.cloud_received_at, 1710000000);
  assert.equal(state.optical.blocked, false);
  assert.equal(state.state_fresh, true);
  assert.equal(state.state_age_sec, 2);
});

test("stale rover and aircraft state cannot be presented as live or commandable", () => {
  const state = normalizeCloudState([{
    code: "rover_state",
    value: JSON.stringify({
      updated_at: 100,
      armed: true,
      optical_state: "locked",
      aircraft: {
        link_active: true,
        last_seen_age_sec: 1,
        messages: [],
      },
    }),
  }], 200000);

  assert.equal(state.state_fresh, false);
  assert.equal(state.aircraft.link_active, false);
  assert.equal(aircraftCommandsAllowed(state), false);
});

test("fresh nested aircraft heartbeat remains commandable", () => {
  const state = normalizeCloudState([{
    code: "rover_state",
    value: JSON.stringify({
      updated_at: 1710000000,
      aircraft_link: true,
      aircraft: {
        link_active: true,
        mode: "GUIDED",
        armed: false,
        messages: [{
          time: 1710000000,
          type: "HEARTBEAT",
          text: "GUIDED armed=NO",
        }],
      },
    }),
  }], 1710000001000);

  assert.equal(state.aircraft.link_active, true);
  assert.equal(state.optical.blocked, false);
  assert.equal(aircraftCommandsAllowed(state), true);
});

test("fresh rover with stale aircraft heartbeat is optical blocked", () => {
  const state = normalizeCloudState([{
    code: "rover_state",
    value: JSON.stringify({
      updated_at: 1710000030,
      aircraft_link: true,
      aircraft: {
        link_active: true,
        last_seen_age_sec: 30,
        mode: "LOITER",
        armed: false,
        messages: [],
      },
    }),
  }], 1710000031000);

  assert.equal(state.state_fresh, true);
  assert.equal(state.optical.blocked, true);
  assert.equal(state.aircraft.blocked, true);
  assert.equal(state.aircraft.status, "OPTICAL LINK BLOCKED");
  assert.equal(state.aircraft.messages[0].text, "BLOCKED");
  assert.equal(aircraftCommandsAllowed(state), false);
});

test("fresh explicit aircraft link loss becomes a timed blocked message", () => {
  const state = normalizeCloudState([{
    code: "rover_state",
    value: JSON.stringify({
      updated_at: 1710000000,
      aircraft_link: false,
      aircraft_msg_time: 1709999999,
      aircraft: {link_active: false, messages: []},
    }),
  }], 1710000001000);

  assert.equal(state.optical.blocked, true);
  assert.deepEqual(state.aircraft.messages, [{
    time: 1709999999,
    type: "OPTICAL",
    text: "BLOCKED",
    key: "optical-blocked-1709999999",
  }]);
  assert.equal(aircraftCommandsAllowed(state), false);
});
test("blocked optical state clears aircraft detail and rejects every aircraft command", () => {
  const state = normalizeCloudState([
    {
      code: "rover_state",
      value: JSON.stringify({
        aircraft: {
          link_active: true,
          mode: "GUIDED",
          armed: true,
          messages: [{time: 100, type: "HEARTBEAT", text: "GUIDED"}],
        },
        optical_state: "blocked",
      }),
    },
  ], 1710000000000);

  assert.equal(state.aircraft.blocked, true);
  assert.equal(state.aircraft.status, "OPTICAL LINK BLOCKED");
  assert.deepEqual(state.aircraft.messages, [{
    time: 1710000000,
    type: "OPTICAL",
    text: "BLOCKED",
    key: "optical-blocked-1710000000",
  }]);
  assert.equal(aircraftCommandsAllowed(state), false);
});

test("legacy compact aircraft interruption state is treated as optical blocked", () => {
  const state = normalizeCloudState([
    {
      code: "rover_state",
      value: JSON.stringify({
        aircraft_link: false,
        aircraft_age: 17450,
        aircraft_msg: "STATUSTEXT 状态 SIGNAL_INTERRUPTED:OPTICAL_LINK_BL",
        aircraft_msg_time: 1710000000,
      }),
    },
  ], 1710000001000);

  assert.equal(state.optical.blocked, true);
  assert.deepEqual(state.aircraft.messages, [{
    time: 1710000000,
    type: "OPTICAL",
    text: "BLOCKED",
    key: "optical-blocked-1710000000",
  }]);
  assert.equal(aircraftCommandsAllowed(state), false);
});

test("repeated aircraft messages remain separate and keep source or cloud receive time", () => {
  const previous = [];
  const first = appendAircraftMessages(previous, [
    {time: 100, type: "HEARTBEAT", text: "GUIDED"},
    {time: 101, type: "HEARTBEAT", text: "GUIDED"},
  ], 999);
  const polledAgain = appendAircraftMessages(first, [
    {time: 100, type: "HEARTBEAT", text: "GUIDED"},
    {time: 101, type: "HEARTBEAT", text: "GUIDED"},
  ], 1000);
  const noSourceTime = appendAircraftMessages(polledAgain, [
    {sequence: 3, type: "STATUS", text: "verified"},
  ], 1001);

  assert.deepEqual(
    noSourceTime.map((item) => [item.time, item.text]),
    [[100, "GUIDED"], [101, "GUIDED"], [1001, "verified"]],
  );
});

test("aircraft heartbeat summary keeps only mode and complete armed state", () => {
  assert.deepEqual(
    aircraftHeartbeatSummary({
      time: 1710000000,
      type: "AIRCRAFT",
      text: "HEARTBEAT 心跳 GUIDED armed=YES sys=1/1",
    }),
    {
      time: 1710000000,
      type: "HEARTBEAT",
      text: "GUIDED armed=YES",
    },
  );
  assert.equal(
    aircraftHeartbeatSummary({
      type: "STATUSTEXT",
      text: "PreArm: GPS 1: Bad fix",
    }),
    null,
  );
  assert.equal(
    aircraftHeartbeatSummary({
      type: "HEARTBEAT",
      text: "GUIDED arme",
    }, false).text,
    "GUIDED armed=NO",
  );
  assert.equal(
    aircraftHeartbeatSummary({
      type: "HEARTBEAT",
      text: "GUIDED arme",
    }).text,
    "GUIDED",
  );
});

test("transaction timeline exposes upload verification readiness execution and failure", () => {
  assert.deepEqual(
    transactionTimeline({
      rover_tx_stage: "verified",
      mission_status: "verified mission; waiting for GPS",
    }, "rover").map((item) => [item.key, item.state]),
    [
      ["upload", "done"],
      ["verified", "done"],
      ["ready", "waiting"],
      ["auto", "idle"],
      ["reached", "idle"],
    ],
  );

  assert.equal(
    transactionTimeline({
      aircraft_tx_stage: "FAILED",
      aircraft_fault_text: "readback mismatch",
    }, "aircraft").find((item) => item.state === "failed").detail,
    "readback mismatch",
  );
});
