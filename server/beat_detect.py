"""Beat and tempo detection written from scratch with numpy + scipy only.

No ML model, no librosa. The approach:
  1. ffmpeg decodes the uploaded song to mono PCM (any input format).
  2. Band-pass filter to the kick-drum/bass range, rectify, smooth -> an
     "onset strength" envelope (classic energy-based onset detection).
  3. Autocorrelation of that envelope finds the dominant beat period (BPM).
  4. Peak-picking with adaptive threshold + minimum spacing gives concrete
     beat timestamps used later to decide where the video cuts land.
"""
import os
import subprocess
import wave

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks

SAMPLE_RATE = 22050


def decode_to_wav(src_path, wav_path, sample_rate=SAMPLE_RATE):
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ac", "1", "-ar", str(sample_rate),
        "-vn", wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to decode audio: {result.stderr.decode(errors='ignore')[-2000:]}")


def read_wav_mono(wav_path):
    with wave.open(wav_path, "rb") as wf:
        n = wf.getnframes()
        sr = wf.getframerate()
        raw = wf.readframes(n)
        sampwidth = wf.getsampwidth()
    if sampwidth == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    return data, sr


def bandpass_envelope(signal, sr, low=40.0, high=180.0):
    nyq = sr / 2.0
    b, a = butter(4, [low / nyq, high / nyq], btype="band")
    filtered = filtfilt(b, a, signal)
    rectified = np.abs(filtered)
    win = max(1, int(sr * 0.04))
    kernel = np.ones(win) / win
    return np.convolve(rectified, kernel, mode="same")


def onset_strength(signal, sr, hop=512):
    envelope = bandpass_envelope(signal, sr)
    n_frames = len(envelope) // hop
    if n_frames < 2:
        return np.array([0.0]), hop / sr
    framed = envelope[: n_frames * hop].reshape(n_frames, hop).mean(axis=1)
    diff = np.diff(framed, prepend=framed[0])
    flux = np.clip(diff, 0, None)
    return flux, hop / sr


def estimate_tempo(flux, frame_dt, min_bpm=70, max_bpm=180):
    flux = flux - flux.mean()
    if len(flux) < 8 or not np.any(flux):
        return 120.0
    frame_rate = 1.0 / frame_dt
    corr = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    min_lag = max(1, int(frame_rate * 60 / max_bpm))
    max_lag = min(int(frame_rate * 60 / min_bpm), len(corr) - 1)
    if max_lag <= min_lag:
        return 120.0
    window = corr[min_lag:max_lag]
    best_lag = min_lag + int(np.argmax(window))
    return float(60.0 * frame_rate / best_lag)


def detect_beats(audio_path, work_dir):
    wav_path = os.path.join(work_dir, "_analysis.wav")
    decode_to_wav(audio_path, wav_path)
    try:
        signal, sr = read_wav_mono(wav_path)
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

    duration = len(signal) / sr if sr else 0.0
    if duration <= 0.5:
        return {"bpm": 120.0, "beat_times": [0.0], "duration": duration}

    flux, frame_dt = onset_strength(signal, sr)
    times = np.arange(len(flux)) * frame_dt

    bpm = estimate_tempo(flux, frame_dt)
    min_spacing_s = max(0.15, 60.0 / bpm * 0.5)
    min_distance = max(1, int(min_spacing_s / frame_dt))

    threshold = flux.mean() + 0.5 * flux.std()
    peaks, _ = find_peaks(flux, height=threshold, distance=min_distance)
    beat_times = times[peaks].tolist()

    if len(beat_times) < 2:
        step = 60.0 / bpm
        beat_times = list(np.arange(0, duration, step))

    return {"bpm": round(bpm, 2), "beat_times": beat_times, "duration": duration}
