# Waypoint Distance And Aircraft Return Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each waypoint segment distance and append the aircraft's valid upload-time position as the final return waypoint.

**Architecture:** Add deterministic route helpers to the existing browser-compatible `GroundStationCore` module, then keep Leaflet rendering and DOM updates in `app.js`. The existing mission command schema and Tuya/RDK/ELF upload path remain unchanged; the return location is simply one more validated aircraft waypoint.

**Tech Stack:** Browser JavaScript, Node.js built-in test runner, Leaflet, HTML/CSS.

---

### Task 1: Test And Implement Route Preparation Helpers

**Files:**
- Modify: `ground_station/public/core.js`
- Modify: `ground_station/test/mission.test.js`

- [ ] **Step 1: Write failing tests for distances and the aircraft return point**

Add tests that import `routeSegmentDistances` and
`prepareAircraftUploadRoute` and assert:

```javascript
const origin = {lat: 32.1197, lng: 118.9531};
const route = [
  {lat: 32.1198, lng: 118.9531},
  {lat: 32.1198, lng: 118.9533},
];
const distances = routeSegmentDistances(route, origin);
assert.equal(distances.length, 2);
assert.ok(distances[0] > 10 && distances[0] < 12);
assert.ok(distances[1] > 18 && distances[1] < 20);

const prepared = prepareAircraftUploadRoute(
  [...route, {lat: 1, lng: 1, autoReturn: true}],
  {state_fresh: true, aircraft: {link_active: true, blocked: false,
    lat: 32.1197, lng: 118.9531}},
);
assert.equal(prepared.length, 3);
assert.deepEqual(prepared.at(-1), {
  lat: 32.1197, lng: 118.9531, autoReturn: true,
});
assert.equal(prepared.filter((point) => point.autoReturn).length, 1);
```

Also assert rejection for stale state, blocked link, missing position, and
`0,0`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
node --test ground_station/test/mission.test.js
```

Expected: FAIL because the two helpers are not exported.

- [ ] **Step 3: Implement the minimal pure helpers**

Add and export:

```javascript
function distanceMeters(lat1, lon1, lat2, lon2) {
  const radius = 6371000;
  const radians = (value) => Number(value) * Math.PI / 180;
  const dLat = radians(Number(lat2) - Number(lat1));
  const dLon = radians(Number(lon2) - Number(lon1));
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(radians(lat1)) * Math.cos(radians(lat2))
    * Math.sin(dLon / 2) ** 2;
  return radius * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function routeSegmentDistances(points, origin) {
  return points.map((point, index) => {
    const previous = index === 0 ? origin : points[index - 1];
    if (!previous || !validCoordinate(Number(previous.lat), Number(previous.lng ?? previous.lon))) {
      return null;
    }
    return distanceMeters(previous.lat, previous.lng ?? previous.lon,
      point.lat, point.lng ?? point.lon);
  });
}

function prepareAircraftUploadRoute(points, state) {
  if (!state?.state_fresh) throw new ValueError("aircraft state is stale");
  if (!aircraftCommandsAllowed(state)) throw new ValueError("OPTICAL LINK BLOCKED");
  const aircraft = state.aircraft || {};
  if (!validCoordinate(Number(aircraft.lat), Number(aircraft.lng))) {
    throw new ValueError("aircraft current position is unavailable");
  }
  return points
    .filter((point) => point?.autoReturn !== true)
    .concat({lat: Number(aircraft.lat), lng: Number(aircraft.lng), autoReturn: true});
}
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run `node --test ground_station/test/mission.test.js`.

Expected: all mission tests pass.

### Task 2: Integrate Distances And Upload-Time Return Point

**Files:**
- Modify: `ground_station/public/app.js`
- Modify: `ground_station/public/style.css`

- [ ] **Step 1: Remove the old generated return point before manual edits**

In `addWaypoint`, remove `autoReturn` points from the selected route before
pushing the new manual point. This keeps the generated return point last.

- [ ] **Step 2: Append and display the upload-time aircraft position**

In `uploadMission`, call `Core.prepareAircraftUploadRoute(route, latestState)`
before `Core.buildMissionCommand`. Replace the displayed aircraft route with
the prepared route, then call `renderQueue()` and `redrawRoutes()` before
posting the command. The log item count must use the prepared route length.

- [ ] **Step 3: Render segment distance in map tooltips and queue rows**

Create a `currentVehiclePosition(vehicle)` helper from `latestState.telemetry`
or `latestState.aircraft`, call `Core.routeSegmentDistances`, and render:

```javascript
const distanceLabel = Number.isFinite(distance)
  ? `${index === 0 ? "距当前位置" : "距上一点"} ${distance.toFixed(1)} m`
  : `${index === 0 ? "距当前位置" : "距上一点"} -`;
```

Append this label to each marker tooltip and queue row. Mark generated return
points as `返航` while preserving numeric waypoint order.

- [ ] **Step 4: Keep route layout stable**

Expand `.queue-row` to four stable columns and add a compact muted distance
style. Ensure long coordinates and the return label wrap without changing the
map dimensions.

### Task 3: Clarify Rover Speed And Verify The Complete Ground Station

**Files:**
- Modify: `ground_station/public/index.html`
- Modify: `ground_station/public/style.css`
- Test: `ground_station/test/mission.test.js`

- [ ] **Step 1: Make the Rover speed unit visible**

Wrap `speedInput` in a compact field container with a visible `速度` label and
`m/s` suffix. Preserve `id="speedInput"`, value `0.8`, validation range, and
the existing hide/show behavior when switching to aircraft mission mode.

- [ ] **Step 2: Run all ground-station tests**

Run:

```bash
node --test ground_station/test/*.test.js
```

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run repository regression tests**

Run:

```bash
python3 -m unittest discover -s tests -t . -v
git diff --check
```

Expected: 257 or more tests pass, with only the existing host OpenCV skip.

- [ ] **Step 4: Start and visually verify the local ground station**

Start the existing server on an unused localhost port with the configured Tuya
environment, then verify desktop and mobile screenshots in the browser:

- waypoint numbers, path, and distance labels remain legible;
- Rover speed label does not overflow;
- aircraft upload adds exactly one numbered return point;
- the two-column desktop layout and single-column mobile layout do not overlap.

- [ ] **Step 5: Commit the focused implementation**

```bash
git add ground_station/public/core.js ground_station/public/app.js \
  ground_station/public/index.html ground_station/public/style.css \
  ground_station/test/mission.test.js \
  docs/superpowers/plans/2026-08-08-waypoint-distance-aircraft-return.md
git commit -m "feat: show waypoint distance and add aircraft return point"
```
