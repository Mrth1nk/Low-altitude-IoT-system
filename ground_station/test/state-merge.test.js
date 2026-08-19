"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {preserveAircraftDetails} = require("../server.js");

test("partial cloud properties retain main and slave caches independently", () => {
  const previous = {
    raw: {rover_state: "old-main", slave_state: "old-slave"},
    properties_present: {rover_state: true, slave_state: true},
    telemetry: {updated_at: 100, aircraft_link: true},
    aircraft: {link_active: true, mode: "GUIDED", messages: [{text: "main"}]},
    slave: {updated_at: 100, online: true, link_active: true, mode: "LOITER", messages: [{text: "slave"}]},
  };
  const roverOnly = {
    raw: {rover_state: "new-main"},
    properties_present: {rover_state: true, slave_state: false},
    telemetry: {updated_at: 102, aircraft_link: true, last_command: "arm"},
    aircraft: {link_active: true, mode: "AUTO", messages: [{text: "main-new"}]},
    slave: {status: "OFFLINE", messages: []},
  };
  const mergedRover = preserveAircraftDetails(previous, roverOnly);

  assert.equal(mergedRover.aircraft.mode, "AUTO");
  assert.equal(mergedRover.slave.mode, "LOITER");
  assert.deepEqual(mergedRover.slave.messages, [{text: "slave"}]);

  const slaveOnly = {
    raw: {slave_state: "new-slave"},
    properties_present: {rover_state: false, slave_state: true},
    telemetry: {},
    aircraft: {link_active: false, messages: []},
    slave: {updated_at: 103, online: true, link_active: true, mode: "AUTO", messages: [{text: "slave-new"}]},
  };
  const mergedSlave = preserveAircraftDetails(mergedRover, slaveOnly);

  assert.equal(mergedSlave.telemetry.last_command, "arm");
  assert.equal(mergedSlave.aircraft.mode, "AUTO");
  assert.equal(mergedSlave.slave.mode, "AUTO");
  assert.deepEqual(mergedSlave.aircraft.messages, [{text: "main-new"}]);
});

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

test("partial mission update retains both last valid positions", () => {
  const previous = {
    telemetry: {lat: 32.11974, lng: 118.95314, mission_status: "verified"},
    aircraft: {lat: 32.11981, lng: 118.95322, mode: "GUIDED"},
  };
  const partial = {
    telemetry: {mission_status: "mission active seq=1"},
    aircraft: {mode: "AUTO", mission_status: "executing"},
  };

  const merged = preserveAircraftDetails(previous, partial);

  assert.equal(merged.telemetry.lat, 32.11974);
  assert.equal(merged.telemetry.lng, 118.95314);
  assert.equal(merged.telemetry.mission_status, "mission active seq=1");
  assert.equal(merged.aircraft.lat, 32.11981);
  assert.equal(merged.aircraft.lng, 118.95322);
  assert.equal(merged.aircraft.mode, "AUTO");
});

test("observed zero coordinates replace stale outdoor positions", () => {
  const previous = {
    telemetry: {lat: 32.11974, lng: 118.95314},
    aircraft: {lat: 32.11981, lng: 118.95322},
  };
  const noFix = {
    telemetry: {lat: 0, lng: 0, position_observed: true, gps_fix_type: 1},
    aircraft: {lat: 0, lng: 0, position_observed: true, mode: "LOITER"},
  };

  const merged = preserveAircraftDetails(previous, noFix);

  assert.deepEqual(
    [merged.telemetry.lat, merged.telemetry.lng],
    [0, 0],
  );
  assert.deepEqual(
    [merged.aircraft.lat, merged.aircraft.lng],
    [0, 0],
  );
  assert.equal(merged.telemetry.gps_fix_type, 1);
  assert.equal(merged.aircraft.mode, "LOITER");
});

test("missing unobserved coordinates retain the previous position", () => {
  const previous = {
    telemetry: {lat: 32.11974, lng: 118.95314, position_observed: true},
    aircraft: {lat: 32.11981, lng: 118.95322, position_observed: true},
  };
  const partial = {
    telemetry: {flight_mode: "HOLD"},
    aircraft: {mode: "LOITER"},
  };

  const merged = preserveAircraftDetails(previous, partial);

  assert.deepEqual([merged.telemetry.lat, merged.telemetry.lng], [32.11974, 118.95314]);
  assert.deepEqual([merged.aircraft.lat, merged.aircraft.lng], [32.11981, 118.95322]);
});
