"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  appendAircraftMessages,
  aircraftCommandsAllowed,
  normalizeCloudState,
  transactionTimeline,
} = require("../public/core.js");

test("normalizes rover_state while preserving filtered raw properties", () => {
  const state = normalizeCloudState([
    {
      code: "rover_state",
      value: JSON.stringify({
        lat: 32.12,
        lng: 118.95,
        flight_mode: "AUTO",
        optical_state: "locked",
      }),
    },
    {code: "command", value: "auto"},
  ], 1710000000000);

  assert.equal(state.telemetry.flight_mode, "AUTO");
  assert.equal(state.raw.command, "auto");
  assert.equal(state.cloud_received_at, 1710000000);
  assert.equal(state.optical.blocked, false);
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
