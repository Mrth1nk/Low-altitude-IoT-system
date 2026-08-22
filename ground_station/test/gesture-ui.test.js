"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const PUBLIC_DIR = path.join(__dirname, "../public");
const HTML_PATH = path.join(PUBLIC_DIR, "index.html");
const RUNTIME_PATH = path.join(PUBLIC_DIR, "gesture-runtime.js");

test("gesture camera modal exposes the required accessible controls", () => {
  const html = fs.readFileSync(HTML_PATH, "utf8");

  assert.match(html, /id="gestureControlBtn"[^>]+aria-label="打开手势控制"/);
  assert.match(html, /data-lucide="camera"/);
  assert.match(html, /id="gestureModal"[^>]+hidden/);
  assert.match(html, /role="dialog"[^>]+aria-modal="true"/);
  assert.match(html, /id="gestureVideo"[^>]+playsinline[^>]+muted/);
  assert.match(html, /id="gestureCanvas"/);
  assert.match(html, /id="gestureTarget"/);
  assert.match(html, /id="gestureGesture"/);
  assert.match(html, /id="gestureConfidence"/);
  assert.match(html, /id="gestureStatus"/);
  assert.match(html, /id="gestureProgress"[^>]+max="1"/);
  assert.match(html, /id="gestureEnabled"[^>]+type="checkbox"/);
  assert.match(html, /id="closeGestureModal"[^>]+aria-label="关闭手势控制"/);
  assert.match(html, /data-lucide="x"/);
});

test("gesture scripts load offline and in dependency order", () => {
  const html = fs.readFileSync(HTML_PATH, "utf8");
  const lucideIndex = html.indexOf("/vendor/lucide/lucide.min.js");
  const coreIndex = html.indexOf("/gesture-core.js");
  const appIndex = html.indexOf("/app.js");
  const runtimeIndex = html.indexOf("/gesture-runtime.js");

  assert.ok(lucideIndex >= 0, "local Lucide script is missing");
  assert.ok(coreIndex > lucideIndex, "gesture core must load after Lucide");
  assert.ok(appIndex > coreIndex, "app must load after gesture core");
  assert.ok(runtimeIndex > appIndex, "module runtime must load after app");
  assert.match(html, /lucide\.createIcons\(\)/);
  assert.match(html, /<script type="module" src="\/gesture-runtime\.js"><\/script>/);
});

test("gesture runtime uses local MediaPipe VIDEO inference with GPU fallback", () => {
  const runtime = fs.readFileSync(RUNTIME_PATH, "utf8");

  assert.match(runtime, /from "\/vendor\/mediapipe\/vision_bundle\.mjs"/);
  assert.match(runtime, /FilesetResolver\.forVisionTasks\("\/vendor\/mediapipe\/wasm"\)/);
  assert.match(runtime, /modelAssetPath:\s*"\/vendor\/mediapipe\/gesture_recognizer\.task"/);
  assert.match(runtime, /runningMode:\s*"VIDEO"/);
  assert.match(runtime, /numHands:\s*1/);
  assert.match(runtime, /createRecognizer\("GPU"\)/);
  assert.match(runtime, /createRecognizer\("CPU"\)/);
  assert.match(runtime, /GestureRecognizer\.HAND_CONNECTIONS/);
  assert.match(runtime, /drawConnectors/);
  assert.match(runtime, /drawLandmarks/);
  assert.match(runtime, /recognizeForVideo/);
});

test("gesture runtime only emits frontend command events and cleans up capture", () => {
  const runtime = fs.readFileSync(RUNTIME_PATH, "utf8");

  assert.doesNotMatch(runtime, /\bfetch\s*\(/);
  assert.match(runtime, /new CustomEvent\("groundstation:gesture-command"/);
  assert.match(runtime, /source:\s*"gesture"/);
  assert.match(runtime, /addEventListener\("groundstation:aircraft-target"/);
  assert.match(runtime, /addEventListener\("visibilitychange"/);
  assert.match(runtime, /getTracks\(\)/);
  assert.match(runtime, /track\.stop\(\)/);
  assert.match(runtime, /CAMERA_START_TIMEOUT_MS/);
  assert.match(runtime, /Promise\.race/);
});
