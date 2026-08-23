# Slave Blocked Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the slave aircraft panel show a temporary optical-link-blocked state while the operator holds `B`.

**Architecture:** Keep the feature entirely in `ground_station/public/app.js`. A browser-local boolean augments only the selected slave aircraft's existing blocked calculation; keyboard release, window blur, and hidden-page events clear it and rerender the latest cloud state.

**Tech Stack:** Browser JavaScript, Node.js built-in test runner.

---

### Task 1: Add blocked-preview behavior

**Files:**
- Create: `ground_station/test/slave-blocked-key.test.js`
- Modify: `ground_station/public/app.js`

- [ ] **Step 1: Write the failing contract test**

Add assertions that the browser source declares a slave blocked preview state, includes it in the slave blocked and command-allowed calculations, handles `B` keydown/keyup outside editable elements, and clears the state on blur or hidden-page transitions.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test ground_station/test/slave-blocked-key.test.js`

Expected: FAIL because the `B` preview state and keyboard handling do not exist.

- [ ] **Step 3: Implement the minimal browser behavior**

Add `forceSlaveBlocked`, an editable-target guard, and a setter that rerenders `latestState`. Extend `renderState()` so the override applies only when `selectedAircraft === "aircraft_2"`, disables commands, and uses the existing blocked UI. Add `B` keydown/keyup handling and clear it on blur and hidden-page transitions.

- [ ] **Step 4: Verify GREEN and regressions**

Run:

```bash
node --test ground_station/test/slave-blocked-key.test.js
node --test ground_station/test/*.test.js
```

Expected: all tests PASS.

- [ ] **Step 5: Commit only this feature**

Stage the new test and only the relevant `app.js` hunks so unrelated worktree changes remain untouched, then commit with `feat: preview slave optical blockage with B key`.
