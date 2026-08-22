import {DrawingUtils, FilesetResolver, GestureRecognizer}
  from "/vendor/mediapipe/vision_bundle.mjs";

const TARGET_LABELS = {aircraft: "主机 1", aircraft_2: "从机 2"};
const CAMERA_START_TIMEOUT_MS = 8000;

const elements = {
  open: document.getElementById("gestureControlBtn"),
  modal: document.getElementById("gestureModal"),
  close: document.getElementById("closeGestureModal"),
  enabled: document.getElementById("gestureEnabled"),
  video: document.getElementById("gestureVideo"),
  canvas: document.getElementById("gestureCanvas"),
  cameraState: document.getElementById("gestureCameraState"),
  target: document.getElementById("gestureTarget"),
  gesture: document.getElementById("gestureGesture"),
  confidence: document.getElementById("gestureConfidence"),
  status: document.getElementById("gestureStatus"),
  progress: document.getElementById("gestureProgress"),
};

let selectedTarget = "aircraft";
let recognizer = null;
let recognizerPromise = null;
let stream = null;
let animationFrame = 0;
let sessionId = 0;
let lastVideoTime = -1;
let drawingUtils = null;
let restoreFocus = null;

const gestureMachine = window.GroundStationGestureCore.createGestureStateMachine(
  ({command, target}) => {
    window.dispatchEvent(new CustomEvent("groundstation:gesture-command", {
      detail: {command, target, source: "gesture"},
    }));
  },
);

function setCameraState(message) {
  elements.cameraState.textContent = message;
}

function setStatus(message) {
  elements.status.textContent = message;
}

function resetReadout(message = "待命") {
  elements.gesture.textContent = "-";
  elements.confidence.textContent = "-";
  elements.progress.value = 0;
  setStatus(message);
}

function updateTarget(target) {
  selectedTarget = target || "aircraft";
  elements.target.textContent = TARGET_LABELS[selectedTarget] || selectedTarget;
  gestureMachine.setTarget(selectedTarget);
  resetReadout(elements.enabled.checked ? "保持已取消" : "待命");
}

async function createRecognizer(delegate) {
  const vision = await FilesetResolver.forVisionTasks("/vendor/mediapipe/wasm");
  return GestureRecognizer.createFromOptions(vision, {
    baseOptions: {modelAssetPath: "/vendor/mediapipe/gesture_recognizer.task", delegate},
    runningMode: "VIDEO",
    numHands: 1,
    minHandDetectionConfidence: 0.6,
    minHandPresenceConfidence: 0.6,
    minTrackingConfidence: 0.6,
  });
}

async function loadRecognizer() {
  if (recognizer) return recognizer;
  if (recognizerPromise) return recognizerPromise;
  setCameraState("正在加载手势模型");
  recognizerPromise = (async () => {
    try {
      recognizer = await createRecognizer("GPU");
    } catch (gpuError) {
      console.warn("Gesture GPU delegate unavailable, using CPU", gpuError);
      recognizer = await createRecognizer("CPU");
    }
    return recognizer;
  })();
  try {
    return await recognizerPromise;
  } catch (error) {
    recognizerPromise = null;
    throw error;
  }
}

function resizeCanvas() {
  const width = elements.video.videoWidth;
  const height = elements.video.videoHeight;
  if (!width || !height) return false;
  if (elements.canvas.width !== width || elements.canvas.height !== height) {
    elements.canvas.width = width;
    elements.canvas.height = height;
    drawingUtils = new DrawingUtils(elements.canvas.getContext("2d"));
  }
  return true;
}

function clearCanvas() {
  const context = elements.canvas.getContext("2d");
  context.clearRect(0, 0, elements.canvas.width, elements.canvas.height);
}

function drawSkeleton(landmarks) {
  clearCanvas();
  if (!landmarks || !resizeCanvas()) return;
  const context = elements.canvas.getContext("2d");
  context.save();
  context.translate(elements.canvas.width, 0);
  context.scale(-1, 1);
  drawingUtils.drawConnectors(
    landmarks,
    GestureRecognizer.HAND_CONNECTIONS,
    {color: "#22c55e", lineWidth: 4},
  );
  drawingUtils.drawLandmarks(landmarks, {
    color: "#ffffff",
    fillColor: "#2463df",
    lineWidth: 2,
    radius: 4,
  });
  context.restore();
}

function renderResult(result, now) {
  const landmarks = result.landmarks?.[0];
  const category = result.gestures?.[0]?.[0];
  drawSkeleton(landmarks);

  const gesture = category?.categoryName || "";
  const confidence = Number(category?.score);
  elements.gesture.textContent = gesture || "未识别";
  elements.confidence.textContent = Number.isFinite(confidence)
    ? `${(confidence * 100).toFixed(0)}%`
    : "-";

  const state = gestureMachine.update({
    gesture,
    confidence,
    now,
    target: selectedTarget,
  });
  elements.progress.value = state.progress;
  const labels = {
    disabled: "手势控制已关闭",
    idle: "等待有效手势",
    holding: "保持手势",
    latched: "指令已触发",
  };
  setStatus(labels[state.state] || "待命");
}

function runInference(activeSession) {
  if (activeSession !== sessionId || !stream || document.hidden) return;
  try {
    if (elements.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
        && elements.video.currentTime !== lastVideoTime) {
      lastVideoTime = elements.video.currentTime;
      const now = performance.now();
      renderResult(recognizer.recognizeForVideo(elements.video, now), now);
    }
  } catch (error) {
    stopSession();
    setCameraState(`识别不可用：${error.message || "运行错误"}`);
    setStatus("识别已停止");
    return;
  }
  animationFrame = requestAnimationFrame(() => runInference(activeSession));
}

function stopSession(message = "摄像头未启动") {
  sessionId += 1;
  if (animationFrame) cancelAnimationFrame(animationFrame);
  animationFrame = 0;
  if (stream) {
    for (const track of stream.getTracks()) track.stop();
  }
  stream = null;
  elements.video.pause();
  elements.video.srcObject = null;
  elements.enabled.checked = false;
  lastVideoTime = -1;
  gestureMachine.disable();
  clearCanvas();
  resetReadout("手势控制已关闭");
  setCameraState(message);
}

function waitForVideoMetadata(video) {
  if (video.readyState >= HTMLMediaElement.HAVE_METADATA) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(
      () => finish(reject, new Error("摄像头画面启动超时")),
      CAMERA_START_TIMEOUT_MS,
    );
    const onLoaded = () => finish(resolve);
    const onError = () => finish(reject, new Error("摄像头画面不可用"));
    function finish(callback, value) {
      window.clearTimeout(timeout);
      video.removeEventListener("loadedmetadata", onLoaded);
      video.removeEventListener("error", onError);
      callback(value);
    }
    video.addEventListener("loadedmetadata", onLoaded);
    video.addEventListener("error", onError);
  });
}

async function requestCamera() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("浏览器不支持摄像头");
  const request = navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {facingMode: "user", width: {ideal: 1280}, height: {ideal: 720}},
  });
  let timeoutId;
  const timeout = new Promise((resolve, reject) => {
    timeoutId = window.setTimeout(
      () => reject(new Error("摄像头权限请求超时")),
      CAMERA_START_TIMEOUT_MS,
    );
  });
  try {
    return await Promise.race([request, timeout]);
  } catch (error) {
    request.then((lateStream) => {
      for (const track of lateStream.getTracks()) track.stop();
    }).catch(() => {});
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function startSession() {
  const activeSession = ++sessionId;
  resetReadout("正在启动");
  try {
    await loadRecognizer();
    if (activeSession !== sessionId || !elements.enabled.checked) return;
    setCameraState("正在请求摄像头");
    const nextStream = await requestCamera();
    if (activeSession !== sessionId || !elements.enabled.checked) {
      for (const track of nextStream.getTracks()) track.stop();
      return;
    }
    stream = nextStream;
    for (const track of stream.getVideoTracks()) {
      track.addEventListener("ended", () => {
        stopSession("摄像头连接已结束");
        setStatus("摄像头已停止");
      }, {once: true});
    }
    elements.video.srcObject = stream;
    await waitForVideoMetadata(elements.video);
    await elements.video.play();
    if (activeSession !== sessionId) return;
    resizeCanvas();
    gestureMachine.enable(selectedTarget);
    setCameraState("摄像头运行中");
    setStatus("等待有效手势");
    runInference(activeSession);
  } catch (error) {
    stopSession(`不可用：${error.message || "启动失败"}`);
    setStatus("启动失败");
  }
}

function openModal() {
  restoreFocus = document.activeElement;
  elements.modal.hidden = false;
  elements.close.focus();
  setCameraState("正在加载手势模型");
  loadRecognizer()
    .then(() => {
      if (!elements.enabled.checked) setCameraState("摄像头未启动");
    })
    .catch((error) => {
      setCameraState(`模型不可用：${error.message || "加载失败"}`);
      setStatus("模型加载失败");
    });
}

function closeModal() {
  stopSession();
  elements.modal.hidden = true;
  if (restoreFocus instanceof HTMLElement) restoreFocus.focus();
  restoreFocus = null;
}

elements.open.addEventListener("click", openModal);
elements.close.addEventListener("click", closeModal);
elements.modal.querySelector("[data-gesture-close]").addEventListener("click", closeModal);
elements.enabled.addEventListener("change", () => {
  if (elements.enabled.checked) startSession();
  else stopSession();
});

window.addEventListener("groundstation:aircraft-target", (event) => {
  updateTarget(event.detail?.target || event.detail);
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) return;
  stopSession("页面已隐藏");
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.modal.hidden) closeModal();
});

updateTarget(selectedTarget);
