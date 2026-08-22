# Ground Station Gesture Control Design

## Goal

Add browser-side gesture control to the existing ground station so the
operator can arm or disarm the currently selected aircraft using the computer
camera. The camera preview must render the detected hand skeleton to make the
edge-AI processing visible during demonstrations.

Gesture recognition is an input method only. It must use the same frontend
command handler, `/api/command` endpoint, Tuya properties, optical-link gate,
and aircraft transport already used by the ARM and DISARM buttons.

## Scope

The feature supports two gestures:

- `Victory` held continuously for 1.5 seconds requests ARM.
- `Closed_Fist` held continuously for 1.0 second requests DISARM.

The command target is the aircraft selected by the existing `mainAircraftTab`
or `slaveAircraftTab`. No new Tuya property, backend route, device command,
or aircraft protocol is introduced.

## User Interface

Add a camera-icon gesture control button to the aircraft section heading,
beside the existing optical-link badge. The button opens a standalone modal
window instead of permanently consuming aircraft-panel space.

The modal contains:

- A mirrored computer-camera preview.
- A canvas overlay showing all detected hand landmarks and connections.
- The currently selected target, `Main 1` or `Slave 2`.
- The recognized gesture and confidence.
- A hold-progress indicator.
- An explicit gesture-control enable switch, disabled by default.
- A close command and camera/recognizer status.

Opening the modal may initialize the local model, but it must not send a
command. Camera capture starts only after explicit operator activation. Closing
the modal stops all camera tracks and recognition work.

## Edge-AI Runtime

Use MediaPipe Gesture Recognizer in the browser. Store the JavaScript runtime,
WASM assets, and gesture-recognizer model with the ground-station static files
so the feature remains available without Internet access.

Recognition runs only in the browser. For each processed frame it returns the
gesture category, confidence, and 21 hand landmarks. The landmarks are drawn
over the video using a dedicated canvas. Only one hand is considered for
commands; additional detected hands do not trigger actions.

## Gesture State Machine

The state machine has `disabled`, `idle`, `holding`, and `latched` states.

1. `disabled`: camera or gesture control is off. No gesture may send commands.
2. `idle`: waiting for one supported gesture above the confidence threshold.
3. `holding`: the same supported gesture must remain above threshold for its
   complete hold duration. A missing hand, different gesture, selected-aircraft
   change, hidden page, stale frame, or confidence drop resets the timer.
4. `latched`: exactly one command has been requested. The recognizer remains
   latched until the supported gesture disappears or changes, preventing
   repeated cloud commands while the operator continues holding the gesture.

Use a minimum confidence of 0.75. DISARM has the shorter hold duration, but it
does not bypass the explicit enable switch or existing command gate.

## Command Integration

Extract the existing aircraft-button behavior into one frontend function that:

1. Reads the currently selected aircraft.
2. Applies `Core.aircraftCommandsAllowed` exactly as the buttons do today.
3. Calls the existing `postCommand` with the existing command and target.
4. Displays the existing toast and cloud-operation log result.

Both physical button clicks and gesture triggers call this function. Gesture
recognition must never call the server or Tuya API directly.

Before sending a command, capture the selected target. If the selected tab
changes while a gesture is being held, cancel the hold and require a fresh
gesture. A command already in flight retains the target captured when it was
issued.

## Failure Handling

- Camera permission denied: show a local error and leave gesture control off.
- Model/WASM load failure: show a local error; normal aircraft buttons remain
  fully operational.
- Camera stream ended: disable gesture control and reset the gesture state.
- Aircraft blocked or offline: use the same existing error shown by the button.
- Command request failure: use the existing `postCommand` log and toast.
- Page hidden or modal closed: stop capture, reset state, and send no command.

The gesture feature is additive. Any recognition failure must leave the rest
of the ground station unchanged.

## Testing

Add deterministic unit tests for:

- Gesture hold timing and confidence threshold.
- Reset on gesture loss, target change, and disabled state.
- One-shot latching and re-arming after gesture release.
- Correct command mapping: `Victory` to ARM and `Closed_Fist` to DISARM.
- Current aircraft target capture.
- Button and gesture paths using the same command dispatcher.
- Required modal controls and offline static model assets.

Browser verification must cover camera permission handling, mirrored video,
skeleton alignment, target switching, hold progress, one-command latching,
modal cleanup, and responsive layout. Live aircraft actuation is not required
for visual testing; command dispatch can be verified against the local fake
Tuya mode before a controlled, propeller-free device test.

## Acceptance Criteria

- The operator can open and close the gesture window without affecting normal
  ground-station operation.
- Hand landmarks visibly track the operator over the camera image.
- A stable Victory gesture triggers one ARM request after 1.5 seconds.
- A stable closed fist triggers one DISARM request after 1.0 second.
- Holding a gesture does not repeat requests.
- The request targets the aircraft tab selected when the hold completes.
- Switching tabs during a hold cancels that hold.
- Gesture and button commands use the same existing command path.
- The feature works with the competition computer disconnected from the
  Internet after the ground station has started.
