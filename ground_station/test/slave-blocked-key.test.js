"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const APP_PATH = path.join(__dirname, "../public/app.js");

test("holding B previews optical blockage only for the selected slave", () => {
  const app = fs.readFileSync(APP_PATH, "utf8");

  assert.match(app, /let forceSlaveBlocked = false;/);
  assert.match(app, /selectedAircraft === "aircraft_2" && forceSlaveBlocked/);
  assert.match(app, /const allowed = !previewBlocked\s*&& Core\.aircraftCommandsAllowed/);
  assert.match(app, /event\.key\.toLowerCase\(\) === "b"/);
  assert.match(app, /setSlaveBlockedPreview\(true\)/);
  assert.match(app, /setSlaveBlockedPreview\(false\)/);
});

test("blocked preview ignores editors and cannot stick after focus loss", () => {
  const app = fs.readFileSync(APP_PATH, "utf8");

  assert.match(app, /function isEditableTarget\(target\)/);
  assert.match(app, /target\.isContentEditable/);
  assert.match(app, /window\.addEventListener\("blur", clearTransientKeyboardState\)/);
  assert.match(app, /document\.visibilityState === "hidden"/);
  assert.match(app, /clearTransientKeyboardState\(\)/);
});
