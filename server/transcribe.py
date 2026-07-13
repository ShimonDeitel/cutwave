"""Automatic subtitle/lyric transcription, via a subprocess wrapping
faster-whisper (CTranslate2). Fully offline after a one-time model download
(cached under ~/.cache/huggingface by default).

This shells out to transcribe_worker.py rather than importing faster-whisper
directly: faster-whisper depends on `av`, which bundles a libavdevice that
collides with OpenCV's bundled libavdevice at the Objective-C runtime level
on macOS, crashing the process if both are loaded together. Running the
model in its own process sidesteps that entirely.
"""
import json
import os
import subprocess
import sys

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcribe_worker.py")


def transcribe_song(audio_path, timeout=600):
    """Return short, on-screen-sized lyric lines:
    [{text, start, end, words: [{text,start,end}]}] -- see
    transcribe_worker.py for the actual transcription/chunking logic."""
    result = subprocess.run(
        [sys.executable, _WORKER, audio_path],
        capture_output=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "transcription failed: " + result.stderr.decode(errors="ignore")[-3000:]
        )
    return json.loads(result.stdout.decode())
