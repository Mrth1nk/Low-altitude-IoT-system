"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  buildMissionCommand,
  createGroundStationStores,
  filterCommandProperties,
  isAircraftCommand,
  prepareAircraftUploadRoute,
  routeSegmentDistances,
  switchAircraftAltitude,
} = require("../public/core.js");
const {buildAircraftMissionFragments, buildCommandsBody, buildIssueBody} = require("../server.js");

const points = [
  {lat: 32.1197, lng: 118.9531, speed: 0.8},
  {lat: 32.1198, lng: 118.9533, speed: 1.2},
];

test("calculates the first segment from current position and later route segments", () => {
  const origin = {lat: 32.1197, lng: 118.9531};
  const route = [
    {lat: 32.1198, lng: 118.9531},
    {lat: 32.1198, lng: 118.9533},
  ];

  const distances = routeSegmentDistances(route, origin);

  assert.equal(distances.length, 2);
  assert.ok(distances[0] > 10 && distances[0] < 12);
  assert.ok(distances[1] > 18 && distances[1] < 20);
  assert.deepEqual(routeSegmentDistances(route, null), [null, distances[1]]);
});

test("appends exactly one current aircraft position as the final return point", () => {
  const route = [
    {lat: 32.1198, lng: 118.9531},
    {lat: 32.1198, lng: 118.9533},
    {lat: 1, lng: 1, autoReturn: true},
  ];
  const state = {
    online: true,
    state_fresh: true,
    optical: {blocked: false},
    aircraft: {
      link_active: true,
      blocked: false,
      lat: 32.1197,
      lng: 118.9531,
    },
  };

  const prepared = prepareAircraftUploadRoute(route, state);

  assert.equal(prepared.length, 3);
  assert.deepEqual(prepared.at(-1), {
    lat: 32.1197,
    lng: 118.9531,
    autoReturn: true,
  });
  assert.equal(prepared.filter((point) => point.autoReturn).length, 1);
  assert.deepEqual(route.at(-1), {lat: 1, lng: 1, autoReturn: true});
});

test("rejects an unsafe aircraft return-position snapshot", () => {
  const route = [{lat: 32.1198, lng: 118.9531}];
  const ready = {
    online: true,
    state_fresh: true,
    optical: {blocked: false},
    aircraft: {link_active: true, lat: 32.1197, lng: 118.9531},
  };

  assert.throws(
    () => prepareAircraftUploadRoute(route, {...ready, state_fresh: false}),
    /stale/i,
  );
  assert.throws(
    () => prepareAircraftUploadRoute(route, {
      ...ready,
      optical: {blocked: true},
    }),
    /blocked/i,
  );
  assert.throws(
    () => prepareAircraftUploadRoute(route, {
      ...ready,
      aircraft: {...ready.aircraft, lat: undefined, lng: undefined},
    }),
    /position/i,
  );
  assert.throws(
    () => prepareAircraftUploadRoute(route, {
      ...ready,
      aircraft: {...ready.aircraft, lat: 0, lng: 0},
    }),
    /position/i,
  );
  assert.throws(
    () => prepareAircraftUploadRoute([], ready),
    /at least one/i,
  );
});

test("builds one complete rover mission with speed commands and no fabricated home", () => {
  const command = buildMissionCommand("rover", points, {
    commandId: "00112233-4455-4677-8899-aabbccddeeff",
    missionId: "rover-demo",
    timestamp: 1710000000000,
  });

  assert.equal(command.command, "mission");
  assert.equal(command.target, "rover");
  assert.equal(command.command_id, "00112233-4455-4677-8899-aabbccddeeff");
  assert.equal(command.source_timestamp, 1710000000000);
  assert.equal(command.payload.mission_id, "rover-demo");
  assert.equal(command.payload.items.length, 4);
  assert.deepEqual(
    command.payload.items.map((item) => item.command),
    [178, 16, 178, 16],
  );
  assert.deepEqual(
    command.payload.items.filter((item) => item.command === 178).map((item) => item.param2),
    [0.8, 1.2],
  );
  assert.deepEqual(
    command.payload.items.filter((item) => item.command === 178).map((item) => item.frame),
    [0, 0],
  );
  assert.deepEqual(
    command.payload.items.filter((item) => item.command === 16).map((item) => [item.lat, item.lon]),
    [[32.1197, 118.9531], [32.1198, 118.9533]],
  );
  assert.deepEqual(
    command.payload.items.filter((item) => item.command === 16).map((item) => item.frame),
    [0, 0],
  );
  assert.deepEqual(
    command.payload.items.filter((item) => item.command === 16).map((item) => item.alt),
    [0, 0],
  );
  assert.equal(command.payload.items.some((item) => item.is_home), false);
});

test("builds one aircraft mission with uniform altitude and does not request AUTO", () => {
  const command = buildMissionCommand("aircraft", points, {
    altitude: 24,
    commandId: "11112233-4455-4677-8899-aabbccddeeff",
    missionId: "air-demo",
    timestamp: 1710000000000,
  });

  assert.equal(command.command, "aircraft_mission");
  assert.equal(command.target, "aircraft");
  assert.deepEqual(command.payload.items.map((item) => item.alt), [24, 24]);
  assert.deepEqual(command.payload.items.map((item) => item.command), [16, 16]);
  assert.equal(command.payload.start_auto, undefined);
});

test("builds slave mission and preserves aircraft_2 target in Tuya envelope", () => {
  const command = buildMissionCommand("aircraft_2", points, {
    altitude: 18,
    commandId: "22112233-4455-4677-8899-aabbccddeeff",
    missionId: "slave-demo",
    timestamp: 1710000000000,
  });
  const envelope = JSON.parse(filterCommandProperties(command).command);

  assert.equal(command.command, "aircraft_mission");
  assert.equal(command.target, "aircraft_2");
  assert.equal(command.payload.vehicle, "aircraft_2");
  assert.equal(envelope.target, "aircraft_2");
  assert.equal(envelope.payload.mission_id, "slave-demo");
});

test("every slave mission fragment keeps the aircraft_2 target", () => {
  const command = buildMissionCommand("aircraft_2", points, {
    altitude: 18,
    commandId: "44112233-4455-4677-8899-aabbccddeeff",
    missionId: "slave-fragments",
  });
  const fragments = buildAircraftMissionFragments(command);

  assert.ok(fragments.length > 2);
  assert.ok(fragments.every((fragment) => fragment.target === "aircraft_2"));
  assert.equal(fragments[0].payload.vehicle, "aircraft_2");
});

test("simple slave command preserves aircraft_2 target in command envelope", () => {
  const properties = filterCommandProperties({
    command: "aircraft_guided",
    target: "aircraft_2",
    command_id: "33112233-4455-4677-8899-aabbccddeeff",
  });

  assert.deepEqual(JSON.parse(properties.command), {
    command: "aircraft_guided",
    command_id: "33112233-4455-4677-8899-aabbccddeeff",
    target: "aircraft_2",
  });
});

test("main and slave mission queues and message histories never share arrays", () => {
  const stores = createGroundStationStores();
  stores.routes.aircraft.push({lat: 1, lng: 2});
  stores.messages.aircraft_2.push({text: "slave"});

  assert.deepEqual(stores.routes.aircraft_2, []);
  assert.deepEqual(stores.messages.aircraft, []);
  assert.notEqual(stores.routes.aircraft, stores.routes.aircraft_2);
  assert.notEqual(stores.messages.aircraft, stores.messages.aircraft_2);
});

test("main and slave aircraft restore independent altitude inputs", () => {
  const stores = createGroundStationStores();

  assert.equal(switchAircraftAltitude(stores.altitudes, "rover", "aircraft", 0), 20);
  assert.equal(switchAircraftAltitude(stores.altitudes, "aircraft", "aircraft_2", 35), 20);
  assert.equal(switchAircraftAltitude(stores.altitudes, "aircraft_2", "aircraft", 18), 35);
  assert.equal(stores.altitudes.aircraft, 35);
  assert.equal(stores.altitudes.aircraft_2, 18);
});

test("slave return point uses slave current position without changing main return logic", () => {
  const state = {
    online: true,
    state_fresh: true,
    optical: {blocked: false},
    aircraft: {link_active: true, lat: 32.1, lng: 118.9},
    slave: {online: true, state_fresh: true, link_active: true, blocked: false, lat: 32.2, lng: 119.0},
  };
  const route = [{lat: 32.21, lng: 119.01}];

  assert.deepEqual(prepareAircraftUploadRoute(route, state, "aircraft_2").at(-1), {
    lat: 32.2, lng: 119.0, autoReturn: true,
  });
  assert.deepEqual(prepareAircraftUploadRoute(route, state).at(-1), {
    lat: 32.1, lng: 118.9, autoReturn: true,
  });
});

test("rejects invalid or oversized mission input before command creation", () => {
  assert.throws(() => buildMissionCommand("rover", [], {}), /at least one/i);
  assert.throws(
    () => buildMissionCommand("aircraft", [{lat: 0, lng: 0}], {altitude: 20}),
    /coordinate/i,
  );
  assert.throws(
    () => buildMissionCommand("aircraft", points, {altitude: 0}),
    /altitude/i,
  );
  assert.throws(
    () => buildMissionCommand("rover", Array.from({length: 51}, () => points[0]), {}),
    /100 mission items/i,
  );
});

test("server property filter emits only Tuya product DPs for simple commands", () => {
  const filtered = filterCommandProperties({
    command: "manual",
    target: "rover",
    command_id: "00112233-4455-4677-8899-aabbccddeeff",
    source_timestamp: 1710000000000,
    target_lat: 32,
    steering: 10,
    unknown: "must-not-leave-server",
  });

  assert.deepEqual(Object.keys(filtered).sort(), [
    "command",
    "steering",
    "target_lat",
  ]);
});

test("Tuya send-property body serializes properties as the required JSON string", () => {
  assert.deepEqual(
    buildIssueBody({command: "disarm", steering: 0}),
    {properties: "{\"command\":\"disarm\",\"steering\":0}"},
  );
});

test("Tuya DP passthrough body converts every property into a device command", () => {
  assert.deepEqual(
    buildCommandsBody({command: "drive", steering: "120", throttle: "1650"}),
    {
      commands: [
        {code: "command", value: "drive"},
        {code: "steering", value: "120"},
        {code: "throttle", value: "1650"},
      ],
    },
  );
});

test("command filtering identifies every aircraft command including full missions", () => {
  assert.equal(isAircraftCommand({command: "aircraft_auto"}), true);
  assert.equal(isAircraftCommand({command: "aircraft_follow", target: "aircraft_2"}), true);
  assert.equal(
    isAircraftCommand({command: "aircraft_mission", target: "aircraft"}),
    true,
  );
  assert.equal(isAircraftCommand({command: "mission", target: "rover"}), false);
});

test("FOLLOW control is visible only for aircraft 2", () => {
  const html = fs.readFileSync(path.join(__dirname, "../public/index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../public/app.js"), "utf8");
  assert.match(html, /id="aircraftFollowBtn"[^>]*data-aircraft-command="aircraft_follow"/);
  assert.match(app, /aircraftFollowBtn/);
  assert.match(app, /selectedAircraft\s*!==\s*"aircraft_2"/);
});

test("server property filter preserves hidden network mode commands", () => {
  assert.deepEqual(
    filterCommandProperties({
      command: "network_phone",
      source_timestamp: 1710000000000,
    }),
    {
      command: "network_phone",
    },
  );
});

test("server packs a mission envelope into the existing command string DP", () => {
  const properties = filterCommandProperties({
    command: "aircraft_mission",
    target: "aircraft",
    command_id: "00112233-4455-4677-8899-aabbccddeeff",
    source_timestamp: 1710000000000,
    payload: {mission_id: "m1", items: [{lat: 32.1, lng: 118.9, alt: 20}]},
  });

  assert.deepEqual(Object.keys(properties), ["command"]);
  assert.deepEqual(JSON.parse(properties.command), {
    command: "aircraft_mission",
    command_id: "00112233-4455-4677-8899-aabbccddeeff",
    payload: {mission_id: "m1", items: [{lat: 32.1, lng: 118.9, alt: 20}]},
  });
});
