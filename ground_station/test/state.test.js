"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  appendAircraftMessages,
  aircraftCommandsAllowed,
  aircraftHeartbeatSummary,
  normalizeCloudState,
  transactionTimeline,
} = require("../public/core.js");

test("hidden UI states cannot be overridden by component display rules", () => {
  const css = fs.readFileSync(
    path.join(__dirname, "../public/style.css"),
    "utf8",
  );
  assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/);
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

  assert.deepEqual(state.aircraft, {
    blocked: true,
    status: "OPTICAL LINK BLOCKED",
    messages: [],
  });
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
  assert.deepEqual(state.aircraft.messages, []);
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
