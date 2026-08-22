# Ground Station Gesture Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add offline browser-side Victory/closed-fist aircraft control with a visible hand skeleton while reusing the existing aircraft button command path.

**Architecture:** A small UMD gesture state machine provides deterministic hold, reset, and latch behavior and is tested under Node. A separate ES module owns MediaPipe, camera, canvas, and modal lifecycle, then emits a local browser event only after a gesture completes. `app.js` maps both that event and physical buttons through one existing-command dispatcher.

**Tech Stack:** Vanilla HTML/CSS/JavaScript, Node test runner, MediaPipe Tasks Vision 1.0.1, Lucide 1.33.0, browser `getUserMedia`, Canvas 2D.

---

## File Map

- Create `ground_station/public/gesture-core.js`: pure gesture state machine and command mapping.
- Create `ground_station/public/gesture-runtime.js`: camera, MediaPipe, skeleton rendering, modal lifecycle, and custom-event adapter.
- Create `ground_station/public/vendor/mediapipe/`: pinned offline JS/WASM runtime and gesture model.
- Modify `ground_station/public/index.html`: gesture button, modal markup, and scripts.
- Modify `ground_station/public/app.js`: one aircraft dispatcher shared by buttons and gestures.
- Modify `ground_station/public/style.css`: compact heading control and responsive camera modal.
- Create `ground_station/test/gesture.test.js`: deterministic state-machine tests.
- Modify `ground_station/test/state.test.js`: static UI and integration assertions.

### Task 1: Gesture State Machine

**Files:**
- Create: `ground_station/public/gesture-core.js`
- Create: `ground_station/test/gesture.test.js`

- [ ] **Step 1: Write failing timing, reset, mapping, and latch tests**

```javascript
const {createGestureStateMachine} = require("../public/gesture-core.js");

test("Victory arms once after 1500 ms and requires release", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft");
  machine.update({gesture: "Victory", confidence: 0.9, now: 0, target: "aircraft"});
  machine.update({gesture: "Victory", confidence: 0.9, now: 1499, target: "aircraft"});
  assert.equal(fired.length, 0);
  machine.update({gesture: "Victory", confidence: 0.9, now: 1500, target: "aircraft"});
  machine.update({gesture: "Victory", confidence: 0.9, now: 3000, target: "aircraft"});
  assert.deepEqual(fired, [{command: "aircraft_arm", target: "aircraft"}]);
});

test("Closed_Fist disarms after 1000 ms", () => {
  const fired = [];
  const machine = createGestureStateMachine((event) => fired.push(event));
  machine.enable("aircraft_2");
  machine.update({gesture: "Closed_Fist", confidence: 0.8, now: 0, target: "aircraft_2"});
  machine.update({gesture: "Closed_Fist", confidence: 0.8, now: 1000, target: "aircraft_2"});
  assert.deepEqual(fired, [{command: "aircraft_disarm", target: "aircraft_2"}]);
});
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `node --test ground_station/test/gesture.test.js`

Expected: FAIL because `gesture-core.js` does not exist.

- [ ] **Step 3: Implement the minimal pure state machine**

```javascript
const DEFINITIONS = {
  Victory: {command: "aircraft_arm", holdMs: 1500},
  Closed_Fist: {command: "aircraft_disarm", holdMs: 1000},
};

function createGestureStateMachine(onTrigger, {minimumConfidence = 0.75} = {}) {
  let enabled = false;
  let target = "aircraft";
  let activeGesture = "";
  let startedAt = 0;
  let latched = false;

  function reset() {
    activeGesture = "";
    startedAt = 0;
    latched = false;
  }

  function update(sample) {
    const definition = DEFINITIONS[sample.gesture];
    if (!enabled || sample.target !== target || !definition
        || sample.confidence < minimumConfidence) {
      reset();
      return {state: enabled ? "idle" : "disabled", progress: 0};
    }
    if (latched) return {state: "latched", progress: 1};
    if (activeGesture !== sample.gesture) {
      activeGesture = sample.gesture;
      startedAt = sample.now;
    }
    const progress = Math.min(1, (sample.now - startedAt) / definition.holdMs);
    if (progress >= 1) {
      latched = true;
      onTrigger({command: definition.command, target});
    }
    return {state: latched ? "latched" : "holding", progress};
  }

  return {
    enable(nextTarget) { enabled = true; target = nextTarget; reset(); },
    disable() { enabled = false; reset(); },
    setTarget(nextTarget) { target = nextTarget; reset(); },
    update,
  };
}
```

- [ ] **Step 4: Run the gesture tests and confirm GREEN**

Run: `node --test ground_station/test/gesture.test.js`

Expected: all gesture tests pass, including confidence drop, target change,
disable, release, and one-shot latch cases.

- [ ] **Step 5: Commit the state machine**

```bash
git add ground_station/public/gesture-core.js ground_station/test/gesture.test.js
git commit -m "feat: add gesture command state machine"
```

### Task 2: Offline MediaPipe Assets

**Files:**
- Create: `ground_station/public/vendor/mediapipe/vision_bundle.mjs`
- Create: `ground_station/public/vendor/mediapipe/wasm/`
- Create: `ground_station/public/vendor/mediapipe/gesture_recognizer.task`
- Create: `ground_station/public/vendor/lucide/lucide.min.js`
- Modify: `ground_station/test/state.test.js`

- [ ] **Step 1: Write a failing static asset test**

```javascript
test("gesture AI assets are stored locally for offline demos", () => {
  for (const relative of [
    "../public/vendor/mediapipe/vision_bundle.mjs",
    "../public/vendor/mediapipe/gesture_recognizer.task",
    "../public/vendor/lucide/lucide.min.js",
  ]) {
    assert.ok(fs.statSync(path.join(__dirname, relative)).size > 1024);
  }
});
```

- [ ] **Step 2: Run the static test and confirm RED**

Run: `node --test ground_station/test/state.test.js`

Expected: FAIL with missing MediaPipe asset.

- [ ] **Step 3: Download and unpack the pinned runtime and model**

```bash
mkdir -p ground_station/public/vendor/mediapipe/wasm
curl -L https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-1.0.1.tgz -o /tmp/tasks-vision-1.0.1.tgz
tar -xzf /tmp/tasks-vision-1.0.1.tgz -C /tmp
cp /tmp/package/vision_bundle.mjs ground_station/public/vendor/mediapipe/
cp /tmp/package/wasm/* ground_station/public/vendor/mediapipe/wasm/
curl -L https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task -o ground_station/public/vendor/mediapipe/gesture_recognizer.task
curl -L https://registry.npmjs.org/lucide/-/lucide-1.33.0.tgz -o /tmp/lucide-1.33.0.tgz
tar -xzf /tmp/lucide-1.33.0.tgz -C /tmp
mkdir -p ground_station/public/vendor/lucide
cp /tmp/package/dist/umd/lucide.min.js ground_station/public/vendor/lucide/lucide.min.js
```

- [ ] **Step 4: Verify files and test GREEN**

Run: `node --test ground_station/test/state.test.js`

Expected: the asset test passes and no static reference uses an HTTP model or
WASM URL.

- [ ] **Step 5: Commit the pinned assets**

```bash
git add ground_station/public/vendor/mediapipe ground_station/test/state.test.js
git commit -m "build: vendor offline hand gesture model"
```

### Task 3: Camera and Skeleton Modal

**Files:**
- Create: `ground_station/public/gesture-runtime.js`
- Modify: `ground_station/public/index.html`
- Modify: `ground_station/public/style.css`
- Modify: `ground_station/test/state.test.js`

- [ ] **Step 1: Write failing modal contract assertions**

```javascript
assert.match(html, /id="gestureControlBtn"/);
assert.match(html, /id="gestureModal"/);
assert.match(html, /id="gestureVideo"/);
assert.match(html, /id="gestureCanvas"/);
assert.match(html, /id="gestureEnabled"/);
assert.match(html, /gesture-core\.js/);
assert.match(html, /gesture-runtime\.js/);
```

- [ ] **Step 2: Run the UI test and confirm RED**

Run: `node --test ground_station/test/state.test.js`

Expected: FAIL because gesture controls are absent.

- [ ] **Step 3: Add semantic modal markup and compact heading control**

```html
<div class="aircraft-heading-actions">
  <button id="gestureControlBtn" class="icon-command secondary" title="手势控制" aria-label="打开手势控制"><i data-lucide="camera"></i></button>
  <span id="opticalBadge" class="badge">等待状态</span>
</div>
<div id="gestureModal" class="modal gesture-modal" hidden>
  <div class="modal-backdrop"></div>
  <section class="modal-window gesture-window" role="dialog" aria-modal="true" aria-labelledby="gestureTitle">
    <header><h2 id="gestureTitle">边缘 AI 手势控制</h2><button id="closeGestureModal" title="关闭" aria-label="关闭"><i data-lucide="x"></i></button></header>
    <div class="gesture-preview"><video id="gestureVideo" playsinline muted></video><canvas id="gestureCanvas"></canvas></div>
    <div class="gesture-readout"><strong id="gestureTarget">主机 1</strong><span id="gestureStatus">摄像头未启动</span><progress id="gestureProgress" max="1" value="0"></progress></div>
    <label class="gesture-enable"><input id="gestureEnabled" type="checkbox"> 启用手势控制</label>
  </section>
</div>
```

Load `/vendor/lucide/lucide.min.js` before `app.js`, then call
`window.lucide.createIcons()` after the modal markup exists. Use Lucide camera
and close icons; do not hand-draw SVG paths.

- [ ] **Step 4: Implement MediaPipe and camera lifecycle**

`gesture-runtime.js` must:

```javascript
import {FilesetResolver, GestureRecognizer, DrawingUtils}
  from "/vendor/mediapipe/vision_bundle.mjs";

const recognizer = await GestureRecognizer.createFromOptions(
  await FilesetResolver.forVisionTasks("/vendor/mediapipe/wasm"),
  {
    baseOptions: {modelAssetPath: "/vendor/mediapipe/gesture_recognizer.task"},
    runningMode: "VIDEO",
    numHands: 1,
  },
);
```

It must draw `GestureRecognizer.HAND_CONNECTIONS` and landmarks over a mirrored
canvas, update labels and progress, emit
`groundstation:gesture-command`, stop every `MediaStreamTrack` on close, and
reset on `visibilitychange` or `groundstation:aircraft-target`.

- [ ] **Step 5: Run UI tests and syntax checks**

Run:

```bash
node --check ground_station/public/gesture-core.js
node --check ground_station/public/app.js
node --test ground_station/test/state.test.js
```

Expected: all pass.

- [ ] **Step 6: Commit the camera modal**

```bash
git add ground_station/public/index.html ground_station/public/style.css ground_station/public/gesture-runtime.js ground_station/test/state.test.js
git commit -m "feat: add edge AI gesture camera window"
```

### Task 4: Shared Aircraft Command Dispatcher

**Files:**
- Modify: `ground_station/public/app.js`
- Modify: `ground_station/test/state.test.js`

- [ ] **Step 1: Write a failing integration assertion**

```javascript
assert.match(app, /function dispatchAircraftCommand/);
assert.match(app, /groundstation:gesture-command/);
assert.match(app, /groundstation:aircraft-target/);
assert.doesNotMatch(app, /gesture-command[\s\S]{0,400}fetch\(/);
```

- [ ] **Step 2: Run the integration test and confirm RED**

Run: `node --test ground_station/test/state.test.js`

Expected: FAIL because buttons still contain their own inline command logic.

- [ ] **Step 3: Extract and wire the shared dispatcher**

```javascript
function dispatchAircraftCommand(command, target = selectedAircraft) {
  if (!latestState || !Core.aircraftCommandsAllowed(latestState, target)) {
    showToast(target === "aircraft" ? "OPTICAL LINK BLOCKED" : "SLAVE OFFLINE", "danger");
    return Promise.resolve({ok: false, error: "command gated"});
  }
  return postCommand({command, target});
}

for (const button of document.querySelectorAll("[data-aircraft-command]")) {
  button.onclick = () => dispatchAircraftCommand(button.dataset.aircraftCommand);
}

window.addEventListener("groundstation:gesture-command", (event) => {
  const {command, target} = event.detail || {};
  if (!["aircraft_arm", "aircraft_disarm"].includes(command)) return;
  void dispatchAircraftCommand(command, target);
});
```

`setAircraftTab` must emit a local target event after updating
`selectedAircraft`; this event resets any in-progress hold.

- [ ] **Step 4: Run all ground-station tests**

Run: `node --test ground_station/test/*.test.js`

Expected: all tests pass.

- [ ] **Step 5: Commit command integration**

```bash
git add ground_station/public/app.js ground_station/test/state.test.js
git commit -m "feat: route gestures through aircraft controls"
```

### Task 5: Browser and Regression Verification

**Files:**
- Modify only if verification exposes a defect in files from Tasks 1-4.

- [ ] **Step 1: Start fake-cloud ground station**

Run:

```bash
FAKE_TUYA=1 HOST=127.0.0.1 PORT=5179 node ground_station/server.js
```

Expected: `Tuya cloud ground station: http://127.0.0.1:5179/`.

- [ ] **Step 2: Verify desktop and mobile layout in the browser**

Check `1440x900` and `390x844`: no overlap, video preserves aspect ratio,
controls fit, and closing the modal returns focus to the gesture button.

- [ ] **Step 3: Verify camera and recognition behavior**

Grant camera permission, then verify skeleton alignment, Victory progress,
closed-fist progress, target reset, release-to-rearm, background-page reset, and
camera track shutdown. Use fake cloud to confirm exactly one command per hold.

- [ ] **Step 4: Run the complete regression suite**

Run:

```bash
node --test ground_station/test/*.test.js
PYTHONPATH=. python3 -m unittest discover -s tests -t .
git diff --check
```

Expected: all Node and Python tests pass and `git diff --check` emits no output.

- [ ] **Step 5: Commit verification fixes, if any**

```bash
git add ground_station/public ground_station/test
git commit -m "fix: polish gesture control verification"
```

Do not create an empty commit if verification required no changes.
