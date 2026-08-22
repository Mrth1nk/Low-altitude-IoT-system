"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {createServer} = require("../server.js");

const PUBLIC_DIR = path.join(__dirname, "../public");
const ASSETS = [
  ["vendor/mediapipe/vision_bundle.mjs", 100_000],
  ["vendor/mediapipe/wasm/vision_wasm_internal.js", 1_000],
  ["vendor/mediapipe/wasm/vision_wasm_internal.wasm", 1_000_000],
  ["vendor/mediapipe/wasm/vision_wasm_nosimd_internal.js", 1_000],
  ["vendor/mediapipe/wasm/vision_wasm_nosimd_internal.wasm", 1_000_000],
  ["vendor/mediapipe/gesture_recognizer.task", 1_000_000],
  ["vendor/lucide/lucide.min.js", 100_000],
];

test("offline gesture dependencies are present and non-empty", () => {
  for (const [relativePath, minimumBytes] of ASSETS) {
    const stat = fs.statSync(path.join(PUBLIC_DIR, relativePath));
    assert.ok(stat.size >= minimumBytes, `${relativePath} is unexpectedly small`);
  }
});

test("static server sends browser-compatible gesture asset MIME types", async (t) => {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const {port} = server.address();

  const expected = [
    ["/vendor/mediapipe/vision_bundle.mjs", "application/javascript"],
    ["/vendor/mediapipe/wasm/vision_wasm_internal.wasm", "application/wasm"],
    ["/vendor/mediapipe/gesture_recognizer.task", "application/octet-stream"],
    ["/vendor/lucide/lucide.min.js", "application/javascript"],
  ];
  for (const [urlPath, contentType] of expected) {
    const response = await fetch(`http://127.0.0.1:${port}${urlPath}`);
    assert.equal(response.status, 200, urlPath);
    assert.match(response.headers.get("content-type") || "", new RegExp(`^${contentType}`));
    await response.arrayBuffer();
  }
});
