(function () {
  "use strict";

  const state = {
    contentMode: "long", song: null, broll: [], video: null,
    ratio: "16:9", captionMode: "off", jobId: null, pollTimer: null, lyrics: null,
    duration: 45, subtitles: false, addOutro: false, outroDuration: 2.3,
  };

  const tagline = document.getElementById("tagline");
  const contentModePicker = document.getElementById("content-mode-picker");
  const longFormFields = document.getElementById("long-form-fields");
  const shortFormFields = document.getElementById("short-form-fields");

  const dzSong = document.getElementById("dz-song");
  const dzBroll = document.getElementById("dz-broll");
  const dzVideo = document.getElementById("dz-video");
  const inputSong = document.getElementById("input-song");
  const inputBroll = document.getElementById("input-broll");
  const inputVideo = document.getElementById("input-video");
  const songLabel = document.getElementById("song-file-label");
  const brollLabel = document.getElementById("broll-file-label");
  const videoLabel = document.getElementById("video-file-label");
  const ratioPicker = document.getElementById("ratio-picker");
  const captionModePicker = document.getElementById("caption-mode-picker");
  const captionText = document.getElementById("caption-text");
  const captionHint = document.getElementById("caption-hint");
  const generateBtn = document.getElementById("generate-btn");
  const generateBtnLabel = document.getElementById("generate-btn-label");
  const errorMsg = document.getElementById("error-msg");

  const durationSlider = document.getElementById("duration-slider");
  const durationValue = document.getElementById("duration-value");
  const subtitlesToggle = document.getElementById("subtitles-toggle");
  const outroToggle = document.getElementById("outro-toggle");
  const outroTextInput = document.getElementById("outro-text");
  const outroDurationRow = document.getElementById("outro-duration-row");
  const outroDurationSlider = document.getElementById("outro-duration-slider");
  const outroDurationValue = document.getElementById("outro-duration-value");

  const CAPTION_HINTS = {
    off: "",
    custom: "Type the words you want pulsing on screen, timed to the beat.",
    auto: "Transcribes sung vocals automatically and shows them karaoke-style, word by word. Instrumental tracks are skipped gracefully.",
  };

  const TAGLINES = {
    long: "drop a song + b-roll → get a beat-cut music video. runs entirely on this machine.",
    short: "drop an existing video → get a reframed, screen-filling YouTube Short. runs entirely on this machine.",
  };
  const GENERATE_LABELS = { long: "Generate music video", short: "Generate YouTube Short" };

  // Set this to your LemonSqueezy checkout URL once the $5/mo product exists
  // (Store -> Products -> your product -> Share -> copy checkout link).
  const CHECKOUT_URL = "";

  const licenseBadge = document.getElementById("license-badge");
  const licenseBadgeText = document.getElementById("license-badge-text");
  const licensePanel = document.getElementById("license-panel");
  const licensePitchLinked = document.getElementById("license-pitch-linked");
  const licensePitchUnlinked = document.getElementById("license-pitch-unlinked");
  const licenseBuyLink = document.getElementById("license-buy-link");
  const licenseKeyInput = document.getElementById("license-key-input");
  const licenseActivateBtn = document.getElementById("license-activate-btn");
  const licenseMsg = document.getElementById("license-msg");

  if (CHECKOUT_URL) {
    licenseBuyLink.href = CHECKOUT_URL;
    licensePitchLinked.classList.remove("hidden");
  } else {
    licensePitchUnlinked.classList.remove("hidden");
  }

  function renderLicenseStatus(st) {
    licenseBadge.classList.toggle("licensed", !!st.licensed);
    licenseBadge.classList.toggle("empty", !st.licensed && st.free_remaining_today === 0);
    licenseBadgeText.textContent = st.licensed
      ? "Unlimited"
      : st.free_remaining_today > 0
        ? `${st.free_remaining_today} free video today`
        : "Free video used today";
  }

  function refreshLicenseStatus() {
    return fetch("/api/license/status").then((r) => r.json()).then((st) => {
      renderLicenseStatus(st);
      return st;
    }).catch(() => null);
  }

  licenseBadge.addEventListener("click", () => {
    licensePanel.classList.toggle("hidden");
  });

  licenseActivateBtn.addEventListener("click", () => {
    const key = licenseKeyInput.value.trim();
    if (!key) return;
    licenseActivateBtn.disabled = true;
    licenseMsg.textContent = "Checking...";
    licenseMsg.className = "license-msg";
    fetch("/api/license/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ license_key: key }),
    })
      .then((r) => r.json())
      .then((data) => {
        licenseMsg.textContent = data.message || (data.ok ? "Unlocked." : "Something went wrong.");
        licenseMsg.className = "license-msg " + (data.ok ? "ok" : "err");
        renderLicenseStatus(data);
        if (data.ok) {
          updateGenerateEnabled();
          setTimeout(() => licensePanel.classList.add("hidden"), 1500);
        }
      })
      .catch(() => {
        licenseMsg.textContent = "Couldn't reach the license server. Check your connection.";
        licenseMsg.className = "license-msg err";
      })
      .finally(() => { licenseActivateBtn.disabled = false; });
  });

  refreshLicenseStatus();

  const panelSetup = document.getElementById("panel-setup");
  const panelProgress = document.getElementById("panel-progress");
  const panelResult = document.getElementById("panel-result");
  const progressFill = document.getElementById("progress-fill");
  const progressMsg = document.getElementById("progress-msg");

  const resultVideo = document.getElementById("result-video");
  const downloadLink = document.getElementById("download-link");
  const statRowBpm = document.getElementById("stat-row-bpm");
  const statRowCuts = document.getElementById("stat-row-cuts");
  const statRowHighlight = document.getElementById("stat-row-highlight");
  const statBpm = document.getElementById("stat-bpm");
  const statCuts = document.getElementById("stat-cuts");
  const statHighlight = document.getElementById("stat-highlight");
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
    if (state.contentMode === "short") {
      generateBtn.disabled = !state.video;
      return;
    }
    const captionOk = state.captionMode !== "custom" || captionText.value.trim().length > 0;
    generateBtn.disabled = !(state.song && state.broll.length > 0 && captionOk);
  }

  // --- content mode (long-form vs short) ---
  contentModePicker.addEventListener("click", (e) => {
    const btn = e.target.closest(".content-mode-btn");
    if (!btn) return;
    contentModePicker.querySelectorAll(".content-mode-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.contentMode = btn.dataset.contentMode;
    longFormFields.classList.toggle("hidden", state.contentMode !== "long");
    shortFormFields.classList.toggle("hidden", state.contentMode !== "short");
    tagline.textContent = TAGLINES[state.contentMode];
    generateBtnLabel.textContent = GENERATE_LABELS[state.contentMode];
    updateGenerateEnabled();
  });

  // --- video dropzone (short mode) ---
  dzVideo.addEventListener("click", () => inputVideo.click());
  inputVideo.addEventListener("change", (e) => setVideo(e.target.files[0]));
  ["dragover", "dragleave", "drop"].forEach((evt) => {
    dzVideo.addEventListener(evt, (e) => {
      e.preventDefault();
      dzVideo.classList.toggle("dragover", evt === "dragover");
      if (evt === "drop" && e.dataTransfer.files.length) setVideo(e.dataTransfer.files[0]);
    });
  });

  function setVideo(file) {
    if (!file) return;
    state.video = file;
    dzVideo.classList.add("filled");
    videoLabel.textContent = `${file.name} — ${fmtBytes(file.size)}`;
    updateGenerateEnabled();
  }

  // --- short mode: duration slider, subtitles, outro ---
  durationSlider.addEventListener("input", () => {
    state.duration = parseInt(durationSlider.value, 10);
    durationValue.textContent = state.duration + "s";
  });

  subtitlesToggle.addEventListener("change", () => {
    state.subtitles = subtitlesToggle.checked;
  });

  outroToggle.addEventListener("change", () => {
    state.addOutro = outroToggle.checked;
    outroTextInput.classList.toggle("hidden", !state.addOutro);
    outroDurationRow.classList.toggle("hidden", !state.addOutro);
  });

  outroDurationSlider.addEventListener("input", () => {
    state.outroDuration = parseFloat(outroDurationSlider.value);
    outroDurationValue.textContent = state.outroDuration.toFixed(1) + "s";
  });

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

  // --- caption mode ---
  captionModePicker.addEventListener("click", (e) => {
    const btn = e.target.closest(".mode-btn");
    if (!btn) return;
    captionModePicker.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.captionMode = btn.dataset.mode;
    captionText.disabled = state.captionMode !== "custom";
    captionHint.textContent = CAPTION_HINTS[state.captionMode];
    if (state.captionMode === "custom") captionText.focus();
    updateGenerateEnabled();
  });
  captionText.addEventListener("input", updateGenerateEnabled);

  // --- generate ---
  generateBtn.addEventListener("click", async () => {
    errorMsg.textContent = "";
    const fd = new FormData();
    fd.append("mode", state.contentMode);
    if (state.contentMode === "short") {
      fd.append("video", state.video);
      fd.append("duration", state.duration);
      fd.append("subtitles", state.subtitles ? "1" : "0");
      fd.append("add_outro", state.addOutro ? "1" : "0");
      fd.append("outro_text", outroTextInput.value.trim());
      fd.append("outro_duration", state.outroDuration);
    } else {
      fd.append("song", state.song);
      state.broll.forEach((f) => fd.append("broll", f));
      fd.append("aspect_ratio", state.ratio);
      fd.append("caption_mode", state.captionMode);
      fd.append("caption", state.captionMode === "custom" ? captionText.value.trim() : "");
    }

    generateBtn.disabled = true;
    try {
      const res = await fetch("/api/generate", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        if (data.paywall) {
          licenseMsg.textContent = data.error;
          licenseMsg.className = "license-msg err";
          licensePanel.classList.remove("hidden");
          generateBtn.disabled = false;
          return;
        }
        throw new Error(data.error || "upload failed");
      }
      state.jobId = data.job_id;
      refreshLicenseStatus();
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

  function activeLyricText(lyrics, t) {
    let lo = 0, hi = lyrics.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (lyrics[mid].start <= t) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    if (ans === -1) return "";
    const line = lyrics[ans];
    return t > line.end + 0.35 ? "" : line.text;
  }

  function onLyricTimeUpdate() {
    caption3dText.textContent = activeLyricText(state.lyrics, resultVideo.currentTime);
  }

  function onDone(job) {
    showPanel("result");
    resultVideo.removeEventListener("timeupdate", onLyricTimeUpdate);
    resultVideo.src = `/api/preview/${state.jobId}`;
    downloadLink.href = `/api/download/${state.jobId}`;
    statDuration.textContent = (job.result.total_duration || job.result.duration).toFixed(1) + "s";
    statRatio.textContent = job.result.aspect_ratio;

    state.lyrics = null;

    if (job.result.mode === "short") {
      statRowBpm.classList.add("hidden");
      statRowCuts.classList.add("hidden");
      statRowHighlight.classList.remove("hidden");
      const src = job.result.source_duration;
      const a = job.result.highlight_start;
      const b = a + job.result.duration;
      const windowText = src > job.result.duration + 0.5
        ? `${a.toFixed(0)}s-${b.toFixed(0)}s of ${src.toFixed(0)}s`
        : "whole video";
      statHighlight.textContent = windowText + (job.result.face_tracked ? " · face-tracked" : "");
      live3dRow.style.display = "none";
      return;
    }

    statRowBpm.classList.remove("hidden");
    statRowCuts.classList.remove("hidden");
    statRowHighlight.classList.add("hidden");
    statBpm.textContent = Math.round(job.result.bpm) + " BPM";
    statCuts.textContent = job.result.cuts;

    if (job.result.caption_mode === "custom" && captionText.value.trim()) {
      caption3dText.textContent = captionText.value.trim();
      live3dRow.style.display = "flex";
    } else if (job.result.caption_mode === "auto" && job.result.lyrics && job.result.lyrics.length) {
      state.lyrics = job.result.lyrics;
      caption3dText.textContent = "";
      resultVideo.addEventListener("timeupdate", onLyricTimeUpdate);
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
    resultVideo.removeEventListener("timeupdate", onLyricTimeUpdate);
    state.song = null;
    state.broll = [];
    state.video = null;
    state.jobId = null;
    state.lyrics = null;
    state.captionMode = "off";
    state.contentMode = "long";
    contentModePicker.querySelectorAll(".content-mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.contentMode === "long"));
    longFormFields.classList.remove("hidden");
    shortFormFields.classList.add("hidden");
    tagline.textContent = TAGLINES.long;
    generateBtnLabel.textContent = GENERATE_LABELS.long;
    dzSong.classList.remove("filled");
    dzBroll.classList.remove("filled");
    dzVideo.classList.remove("filled");
    songLabel.textContent = "";
    brollLabel.textContent = "";
    videoLabel.textContent = "";
    inputSong.value = "";
    inputBroll.value = "";
    inputVideo.value = "";
    state.duration = 45;
    state.subtitles = false;
    state.addOutro = false;
    state.outroDuration = 2.3;
    durationSlider.value = 45;
    durationValue.textContent = "45s";
    subtitlesToggle.checked = false;
    outroToggle.checked = false;
    outroTextInput.value = "";
    outroTextInput.classList.add("hidden");
    outroDurationRow.classList.add("hidden");
    outroDurationSlider.value = 2.3;
    outroDurationValue.textContent = "2.3s";
    captionModePicker.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === "off"));
    captionText.disabled = true;
    captionText.value = "";
    captionHint.textContent = "";
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
