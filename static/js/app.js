(function () {
  "use strict";

  const state = { song: null, broll: [], ratio: "16:9", jobId: null, pollTimer: null };

  const dzSong = document.getElementById("dz-song");
  const dzBroll = document.getElementById("dz-broll");
  const inputSong = document.getElementById("input-song");
  const inputBroll = document.getElementById("input-broll");
  const songLabel = document.getElementById("song-file-label");
  const brollLabel = document.getElementById("broll-file-label");
  const ratioPicker = document.getElementById("ratio-picker");
  const captionToggle = document.getElementById("caption-toggle");
  const captionText = document.getElementById("caption-text");
  const generateBtn = document.getElementById("generate-btn");
  const errorMsg = document.getElementById("error-msg");

  const panelSetup = document.getElementById("panel-setup");
  const panelProgress = document.getElementById("panel-progress");
  const panelResult = document.getElementById("panel-result");
  const progressFill = document.getElementById("progress-fill");
  const progressMsg = document.getElementById("progress-msg");

  const resultVideo = document.getElementById("result-video");
  const downloadLink = document.getElementById("download-link");
  const statBpm = document.getElementById("stat-bpm");
  const statCuts = document.getElementById("stat-cuts");
  const statDuration = document.getElementById("stat-duration");
  const statRatio = document.getElementById("stat-ratio");
  const resetBtn = document.getElementById("reset-btn");
  const live3dToggle = document.getElementById("live3d-toggle");
  const live3dRow = document.getElementById("live3d-row");
  const caption3dOverlay = document.getElementById("caption3d-overlay");
  const caption3dText = document.getElementById("caption3d-text");

  function fmtBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  function updateGenerateEnabled() {
    generateBtn.disabled = !(state.song && state.broll.length > 0);
  }

  // --- song dropzone ---
  dzSong.addEventListener("click", () => inputSong.click());
  inputSong.addEventListener("change", (e) => setSong(e.target.files[0]));
  ["dragover", "dragleave", "drop"].forEach((evt) => {
    dzSong.addEventListener(evt, (e) => {
      e.preventDefault();
      dzSong.classList.toggle("dragover", evt === "dragover");
      if (evt === "drop" && e.dataTransfer.files.length) setSong(e.dataTransfer.files[0]);
    });
  });

  function setSong(file) {
    if (!file) return;
    state.song = file;
    dzSong.classList.add("filled");
    songLabel.textContent = `${file.name} — ${fmtBytes(file.size)}`;
    updateGenerateEnabled();
  }

  // --- b-roll dropzone ---
  dzBroll.addEventListener("click", () => inputBroll.click());
  inputBroll.addEventListener("change", (e) => setBroll([...e.target.files]));
  ["dragover", "dragleave", "drop"].forEach((evt) => {
    dzBroll.addEventListener(evt, (e) => {
      e.preventDefault();
      dzBroll.classList.toggle("dragover", evt === "dragover");
      if (evt === "drop" && e.dataTransfer.files.length) setBroll([...e.dataTransfer.files]);
    });
  });

  function setBroll(files) {
    if (!files.length) return;
    state.broll = files;
    dzBroll.classList.add("filled");
    brollLabel.textContent = files.map((f) => `${f.name} (${fmtBytes(f.size)})`).join(", ");
    updateGenerateEnabled();
  }

  // --- aspect ratio ---
  ratioPicker.addEventListener("click", (e) => {
    const btn = e.target.closest(".ratio-btn");
    if (!btn) return;
    ratioPicker.querySelectorAll(".ratio-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.ratio = btn.dataset.ratio;
  });

  // --- caption toggle ---
  captionToggle.addEventListener("change", () => {
    captionText.disabled = !captionToggle.checked;
    if (captionToggle.checked) captionText.focus();
  });

  // --- generate ---
  generateBtn.addEventListener("click", async () => {
    errorMsg.textContent = "";
    const fd = new FormData();
    fd.append("song", state.song);
    state.broll.forEach((f) => fd.append("broll", f));
    fd.append("aspect_ratio", state.ratio);
    fd.append("caption", captionToggle.checked ? captionText.value.trim() : "");

    generateBtn.disabled = true;
    try {
      const res = await fetch("/api/generate", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "upload failed");
      state.jobId = data.job_id;
      showPanel("progress");
      pollStatus();
    } catch (err) {
      errorMsg.textContent = err.message;
      generateBtn.disabled = false;
    }
  });

  function pollStatus() {
    clearTimeout(state.pollTimer);
    fetch(`/api/status/${state.jobId}`)
      .then((r) => r.json())
      .then((job) => {
        progressFill.style.width = Math.round((job.progress || 0) * 100) + "%";
        progressMsg.textContent = job.message || "working...";
        if (job.status === "done") {
          onDone(job);
        } else if (job.status === "error") {
          showPanel("setup");
          errorMsg.textContent = job.error || "something went wrong";
          generateBtn.disabled = false;
        } else {
          state.pollTimer = setTimeout(pollStatus, 900);
        }
      })
      .catch(() => {
        state.pollTimer = setTimeout(pollStatus, 1500);
      });
  }

  function onDone(job) {
    showPanel("result");
    resultVideo.src = `/api/preview/${state.jobId}`;
    downloadLink.href = `/api/download/${state.jobId}`;
    statBpm.textContent = Math.round(job.result.bpm) + " BPM";
    statCuts.textContent = job.result.cuts;
    statDuration.textContent = job.result.duration.toFixed(1) + "s";
    statRatio.textContent = job.result.aspect_ratio;

    if (captionToggle.checked && captionText.value.trim()) {
      caption3dText.textContent = captionText.value.trim();
      live3dRow.style.display = "flex";
    } else {
      live3dRow.style.display = "none";
    }
  }

  live3dToggle.addEventListener("change", () => {
    caption3dOverlay.classList.toggle("active", live3dToggle.checked);
    if (live3dToggle.checked && window.cutwaveAudio3D) {
      window.cutwaveAudio3D.start(resultVideo, caption3dOverlay);
    } else if (window.cutwaveAudio3D) {
      window.cutwaveAudio3D.stop();
    }
  });

  resetBtn.addEventListener("click", () => {
    if (window.cutwaveAudio3D) window.cutwaveAudio3D.stop();
    state.song = null;
    state.broll = [];
    state.jobId = null;
    dzSong.classList.remove("filled");
    dzBroll.classList.remove("filled");
    songLabel.textContent = "";
    brollLabel.textContent = "";
    inputSong.value = "";
    inputBroll.value = "";
    captionToggle.checked = false;
    captionText.disabled = true;
    captionText.value = "";
    live3dToggle.checked = false;
    caption3dOverlay.classList.remove("active");
    generateBtn.disabled = true;
    progressFill.style.width = "0%";
    showPanel("setup");
  });

  function showPanel(which) {
    panelSetup.classList.toggle("hidden", which !== "setup");
    panelProgress.classList.toggle("hidden", which !== "progress");
    panelResult.classList.toggle("hidden", which !== "result");
  }
})();
