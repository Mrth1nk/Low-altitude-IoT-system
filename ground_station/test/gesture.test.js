"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const {
  createGestureStateMachine,
} = require("../public/gesture-core.js");

function sample(gesture, now, target = "aircraft", confidence = 0.9) {
  return {gesture, confidence, now, target};
}

test("Victory arms once after 1500 ms", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));

  assert.deepEqual(machine.update(sample("Victory", 0)), {
    state: "disabled",
    progress: 0,
  });
  machine.enable("aircraft");
  assert.deepEqual(machine.update(sample("Victory", 0)), {
    state: "holding",
    progress: 0,
  });
  assert.deepEqual(machine.update(sample("Victory", 750)), {
    state: "holding",
    progress: 0.5,
  });
  machine.update(sample("Victory", 1499));
  assert.equal(fired.length, 0);
  assert.deepEqual(machine.update(sample("Victory", 1500)), {
    state: "latched",
    progress: 1,
  });
  assert.deepEqual(fired, [{command: "aircraft_arm", target: "aircraft"}]);
});

test("Closed_Fist disarms after 1000 ms", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft_2");

  machine.update(sample("Closed_Fist", 10, "aircraft_2", 0.75));
  machine.update(sample("Closed_Fist", 1009, "aircraft_2", 0.75));
  assert.equal(fired.length, 0);
  machine.update(sample("Closed_Fist", 1010, "aircraft_2", 0.75));

  assert.deepEqual(fired, [{command: "aircraft_disarm", target: "aircraft_2"}]);
});

test("confidence below the default threshold resets the hold", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");

  machine.update(sample("Victory", 0));
  assert.deepEqual(machine.update(sample("Victory", 1000, "aircraft", 0.74)), {
    state: "idle",
    progress: 0,
  });
  machine.update(sample("Victory", 1100));
  machine.update(sample("Victory", 2500));
  assert.equal(fired.length, 0);
  machine.update(sample("Victory", 2600));

  assert.equal(fired.length, 1);
});

test("target mismatch and target changes cancel an active hold", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");

  machine.update(sample("Victory", 0));
  assert.deepEqual(machine.update(sample("Victory", 1000, "aircraft_2")), {
    state: "idle",
    progress: 0,
  });
  machine.update(sample("Victory", 1100));
  machine.setTarget("aircraft_2");
  machine.update(sample("Victory", 2600, "aircraft_2"));
  assert.equal(fired.length, 0);
  machine.update(sample("Victory", 4100, "aircraft_2"));

  assert.deepEqual(fired, [{command: "aircraft_arm", target: "aircraft_2"}]);
});

test("disable resets a hold and prevents commands", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");
  machine.update(sample("Closed_Fist", 0));
  machine.disable();

  assert.deepEqual(machine.update(sample("Closed_Fist", 1000)), {
    state: "disabled",
    progress: 0,
  });
  assert.deepEqual(fired, []);

  machine.enable("aircraft");
  machine.update(sample("Closed_Fist", 1100));
  machine.update(sample("Closed_Fist", 2100));
  assert.equal(fired.length, 1);
});

test("a held gesture remains latched and fires exactly once", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");

  machine.update(sample("Victory", 0));
  machine.update(sample("Victory", 1500));
  assert.deepEqual(machine.update(sample("Victory", 5000)), {
    state: "latched",
    progress: 1,
  });
  assert.equal(fired.length, 1);
});

test("gesture release rearms the same command", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");

  machine.update(sample("Victory", 0));
  machine.update(sample("Victory", 1500));
  assert.deepEqual(machine.update(sample(undefined, 1600, "aircraft", 0)), {
    state: "idle",
    progress: 0,
  });
  machine.update(sample("Victory", 1700));
  machine.update(sample("Victory", 3200));

  assert.equal(fired.length, 2);
});

test("changing a latched gesture starts the newly mapped command hold", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");

  machine.update(sample("Victory", 0));
  machine.update(sample("Victory", 1500));
  assert.deepEqual(machine.update(sample("Closed_Fist", 1600)), {
    state: "holding",
    progress: 0,
  });
  machine.update(sample("Closed_Fist", 2600));

  assert.deepEqual(fired, [
    {command: "aircraft_arm", target: "aircraft"},
    {command: "aircraft_disarm", target: "aircraft"},
  ]);
});

test("unsupported gestures reset an active hold", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");

  machine.update(sample("Victory", 0));
  machine.update(sample("Pointing_Up", 1400));
  machine.update(sample("Victory", 1500));
  machine.update(sample("Victory", 1600));

  assert.deepEqual(fired, []);
  assert.deepEqual(machine.update(sample("constructor", 1700)), {
    state: "idle",
    progress: 0,
  });
});

test("validates callback, confidence, and hold timing options", () => {
  assert.throws(() => createGestureStateMachine(), /callback.*function/i);
  assert.throws(
    () => createGestureStateMachine(() => {}, {minimumConfidence: 2}),
    /confidence/i,
  );
  assert.throws(
    () => createGestureStateMachine(() => {}, {victoryHoldMs: 0}),
    /Victory.*positive/i,
  );
  assert.throws(
    () => createGestureStateMachine(() => {}, {closedFistHoldMs: Infinity}),
    /Closed_Fist.*positive/i,
  );

  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event), {
    victoryHoldMs: 10,
    closedFistHoldMs: 20,
  });
  machine.enable("aircraft");
  machine.update(sample("Victory", 0));
  machine.update(sample("Victory", 10));
  assert.equal(fired.length, 1);
});

test("browser UMD build exposes GroundStationGestureCore", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "../public/gesture-core.js"),
    "utf8",
  );
  const context = {};

  vm.runInNewContext(source, context);

  assert.equal(
    typeof context.GroundStationGestureCore.createGestureStateMachine,
    "function",
  );
});
