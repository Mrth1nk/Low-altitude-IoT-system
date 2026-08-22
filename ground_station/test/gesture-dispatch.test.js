"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const APP_PATH = path.join(__dirname, "../public/app.js");

test("aircraft buttons and gesture events share one guarded dispatcher", () => {
  const app = fs.readFileSync(APP_PATH, "utf8");

  assert.match(app, /function dispatchAircraftCommand\(command, target = selectedAircraft\)/);
  assert.match(app, /Core\.aircraftCommandsAllowed\(latestState, target\)/);
  assert.match(app, /postCommand\(\{command, target\}\)/);
  assert.match(app, /button\.onclick = \(\) => dispatchAircraftCommand\(/);
  assert.match(app, /addEventListener\("groundstation:gesture-command"/);
  assert.match(app, /dispatchAircraftCommand\(command, selectedAircraft\)/);
});

test("aircraft tab changes notify gesture runtime synchronously", () => {
  const app = fs.readFileSync(APP_PATH, "utf8");

  assert.match(app, /new CustomEvent\("groundstation:aircraft-target"/);
  assert.match(app, /detail:\s*\{target:\s*selectedAircraft\}/);
});
