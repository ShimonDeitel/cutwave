/* Live cinematic 3D caption preview: Web Audio API analyser on the result
 * video's own audio track drives real-time CSS 3D transforms (rotate/scale/
 * translateZ) on the caption overlay, entirely client-side. This is a bonus
 * "watch it move in 3D while it plays" layer on top of the download, which
 * already has the beat-synced caption baked in server-side. */
window.cutwaveAudio3D = (function () {
  let audioCtx = null, analyser = null, source = null, dataArray = null;
  let rafId = null, textEl = null;

  function ensureGraph(video) {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    if (!source) {
      source = audioCtx.createMediaElementSource(video);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.75;
      source.connect(analyser);
      source.connect(audioCtx.destination);
      dataArray = new Uint8Array(analyser.frequencyBinCount);
    }
  }

  function start(video, overlay) {
    textEl = overlay.querySelector("span");
    try {
      ensureGraph(video);
    } catch (e) {
      console.warn("cutwave: falling back to idle 3D animation —", e.message);
      analyser = null;
    }
    if (!rafId) loop();
  }

  function loop() {
    rafId = requestAnimationFrame(loop);
    let bass, mid;
    if (analyser) {
      analyser.getByteFrequencyData(dataArray);
      const n = dataArray.length;
      bass = avg(dataArray, 0, Math.max(4, (n * 0.08) | 0)) / 255;
      mid = avg(dataArray, (n * 0.08) | 0, (n * 0.35) | 0) / 255;
    } else {
      const t = performance.now() / 1000;
      bass = 0.4 + 0.35 * Math.abs(Math.sin(t * 2));
      mid = 0.3 + 0.2 * Math.abs(Math.sin(t * 1.3 + 1));
    }
    const rotY = (bass - 0.3) * 34;
    const rotX = (mid - 0.3) * -18;
    const scale = 1 + bass * 0.35;
    const z = bass * 60;
    if (textEl) {
      textEl.style.transform =
        `translateZ(${z.toFixed(1)}px) rotateX(${rotX.toFixed(1)}deg) rotateY(${rotY.toFixed(1)}deg) scale(${scale.toFixed(3)})`;
    }
  }

  function avg(arr, start, end) {
    let s = 0, c = 0;
    for (let i = start; i < end && i < arr.length; i++) { s += arr[i]; c++; }
    return c ? s / c : 0;
  }

  function stop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  return { start, stop };
})();
