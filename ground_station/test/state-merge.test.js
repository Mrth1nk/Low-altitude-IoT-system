"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {preserveAircraftDetails} = require("../server.js");

test("rover-only cloud update cannot erase active aircraft details", () => {
  const previous = {
    telemetry: {aircraft_link: true},
    aircraft: {
      link_active: true,
      mode: "GUIDED",
      armed: false,
      messages: [{type: "HEARTBEAT", text: "GUIDED armed=NO"}],
    },
  };
  const partial = {
    telemetry: {aircraft_link: true, last_command: "arm"},
    aircraft: {link_active: false, messages: [], status: "WAITING"},
  };

  const merged = preserveAircraftDetails(previous, partial);

  assert.equal(merged.telemetry.last_command, "arm");
  assert.equal(merged.aircraft.mode, "GUIDED");
  assert.equal(merged.aircraft.messages.length, 1);
});

test("explicit aircraft link loss clears old aircraft details", () => {
  const previous = {
    telemetry: {aircraft_link: true},
    aircraft: {link_active: true, mode: "GUIDED", messages: [{type: "HEARTBEAT"}]},
  };
  const disconnected = {
    telemetry: {aircraft_link: false},
    aircraft: {link_active: false, messages: [], status: "WAITING"},
  };

  const merged = preserveAircraftDetails(previous, disconnected);

  assert.equal(merged.aircraft.link_active, false);
  assert.equal(merged.aircraft.messages.length, 0);
});
