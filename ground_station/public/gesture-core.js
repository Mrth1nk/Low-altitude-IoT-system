(function initGroundStationGestureCore(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else if (root) root.GroundStationGestureCore = api;
})(typeof globalThis === "object" ? globalThis : this, function groundStationGestureCore() {
  "use strict";

  const DEFAULT_MINIMUM_CONFIDENCE = 0.75;
  const DEFAULT_VICTORY_HOLD_MS = 1500;
  const DEFAULT_CLOSED_FIST_HOLD_MS = 1000;

  function createGestureSessionGate() {
    let generation = 0;
    return {
      begin() {
        generation += 1;
        return generation;
      },
      invalidate() {
        generation += 1;
      },
      isCurrent(session) {
        return session === generation;
      },
    };
  }

  function validatePositiveDuration(value, gesture) {
    if (!Number.isFinite(value) || value <= 0) {
      throw new RangeError(`${gesture} hold duration must be positive`);
    }
  }

  function createGestureStateMachine(onTrigger, options = {}) {
    if (typeof onTrigger !== "function") {
      throw new TypeError("gesture callback must be a function");
    }
    if (!options || typeof options !== "object" || Array.isArray(options)) {
      throw new TypeError("gesture options must be an object");
    }

    const {
      minimumConfidence = DEFAULT_MINIMUM_CONFIDENCE,
      victoryHoldMs = DEFAULT_VICTORY_HOLD_MS,
      closedFistHoldMs = DEFAULT_CLOSED_FIST_HOLD_MS,
    } = options;
    if (!Number.isFinite(minimumConfidence)
        || minimumConfidence < 0
        || minimumConfidence > 1) {
      throw new RangeError("minimum confidence must be between 0 and 1");
    }
    validatePositiveDuration(victoryHoldMs, "Victory");
    validatePositiveDuration(closedFistHoldMs, "Closed_Fist");

    const definitions = {
      Victory: {command: "aircraft_arm", holdMs: victoryHoldMs},
      Closed_Fist: {command: "aircraft_disarm", holdMs: closedFistHoldMs},
    };
    let enabled = false;
    let target = "aircraft";
    let activeGesture = "";
    let startedAt = 0;
    let latched = false;

    function resetHold() {
      activeGesture = "";
      startedAt = 0;
      latched = false;
    }

    function snapshot(state, progress = 0) {
      return {state, progress};
    }

    function update(sample = {}) {
      if (!enabled) {
        resetHold();
        return snapshot("disabled");
      }

      const definition = Object.prototype.hasOwnProperty.call(definitions, sample.gesture)
        ? definitions[sample.gesture]
        : null;
      const confidence = Number(sample.confidence);
      const now = Number(sample.now);
      if (sample.target !== target
          || !definition
          || !Number.isFinite(confidence)
          || confidence < minimumConfidence
          || !Number.isFinite(now)) {
        resetHold();
        return snapshot("idle");
      }

      if (latched && activeGesture === sample.gesture) {
        return snapshot("latched", 1);
      }
      if (activeGesture !== sample.gesture) {
        activeGesture = sample.gesture;
        startedAt = now;
        latched = false;
      }

      const elapsed = Math.max(0, now - startedAt);
      const progress = Math.min(1, elapsed / definition.holdMs);
      if (progress >= 1) {
        latched = true;
        onTrigger({command: definition.command, target});
        return snapshot("latched", 1);
      }
      return snapshot("holding", progress);
    }

    return {
      enable(nextTarget) {
        enabled = true;
        target = nextTarget;
        resetHold();
      },
      disable() {
        enabled = false;
        resetHold();
      },
      setTarget(nextTarget) {
        if (nextTarget === target) return;
        target = nextTarget;
        resetHold();
      },
      update,
    };
  }

  return {createGestureSessionGate, createGestureStateMachine};
});
