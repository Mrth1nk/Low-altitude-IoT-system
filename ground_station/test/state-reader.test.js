"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {createStateReader} = require("../server.js");

const endpoints = [
  {path: "/primary", pick: (doc) => doc.result},
  {path: "/fallback", pick: (doc) => doc.result},
];

function reader(options = {}) {
  let now = options.startMs ?? 10_000;
  const instance = createStateReader({
    endpoints,
    cacheTtlMs: 2_000,
    now: () => now,
    request: options.request,
    normalize: (result) => ({
      online: true,
      telemetry: {...result},
      state_fresh: true,
      state_age_sec: 0,
    }),
    preserve: (_previous, next) => next,
  });
  return {
    read: instance,
    advance(ms) { now += ms; },
  };
}

test("state reader reuses one successful cloud read for two seconds", async () => {
  const calls = [];
  const state = reader({
    request: async (path) => {
      calls.push(path);
      return {result: {updated_at: 10, flight_mode: "hold"}};
    },
  });

  const first = await state.read();
  state.advance(1_000);
  const second = await state.read();

  assert.equal(first.telemetry.flight_mode, "hold");
  assert.equal(second.telemetry.flight_mode, "hold");
  assert.deepEqual(calls, ["/primary"]);
});

test("concurrent state reads share one in-flight Tuya request", async () => {
  let release;
  let calls = 0;
  const pending = new Promise((resolve) => { release = resolve; });
  const state = reader({
    request: async () => {
      calls += 1;
      await pending;
      return {result: {updated_at: 10}};
    },
  });

  const reads = [state.read(), state.read(), state.read()];
  release();
  await Promise.all(reads);

  assert.equal(calls, 1);
});

test("fallback endpoint is called only after the primary endpoint fails", async () => {
  const calls = [];
  const state = reader({
    request: async (path) => {
      calls.push(path);
      if (path === "/primary") throw new Error("server busy");
      return {result: {updated_at: 10, flight_mode: "hold"}};
    },
  });

  const result = await state.read();

  assert.equal(result.telemetry.flight_mode, "hold");
  assert.deepEqual(calls, ["/primary", "/fallback"]);
});

test("transient Tuya failure serves the last state and keeps aging it", async () => {
  let failing = false;
  const state = reader({
    startMs: 12_000,
    request: async () => {
      if (failing) throw new Error("server busy");
      return {result: {updated_at: 10, flight_mode: "hold"}};
    },
  });

  await state.read();
  state.advance(2_500);
  failing = true;
  const degraded = await state.read();

  assert.equal(degraded.telemetry.flight_mode, "hold");
  assert.equal(degraded.cloud_degraded, true);
  assert.match(degraded.cloud_error, /server busy/);
  assert.equal(degraded.state_age_sec, 4.5);
  assert.equal(degraded.state_fresh, true);
});

test("cached slave freshness ages independently while rover remains fresh", async () => {
  let now = 12_000;
  const instance = createStateReader({
    endpoints,
    cacheTtlMs: 2_000,
    now: () => now,
    request: async () => ({result: {updated_at: 10}}),
    normalize: () => ({
      online: true,
      telemetry: {updated_at: 10},
      aircraft: {link_active: true},
      slave: {updated_at: 9, online: true, link_active: true, state_fresh: true},
    }),
    preserve: (_previous, next) => next,
  });

  const first = await instance();
  assert.equal(first.state_fresh, true);
  assert.equal(first.slave.state_fresh, true);

  now = 13_500;
  const second = await instance();
  assert.equal(second.state_fresh, true);
  assert.equal(second.slave.state_fresh, false);
  assert.equal(second.slave.status, "OFFLINE");
  assert.equal(second.aircraft.link_active, true);
});
