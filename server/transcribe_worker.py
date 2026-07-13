#!/usr/bin/env python3
"""Standalone subprocess worker for song transcription -- deliberately run
as its own process rather than imported into the Flask app. faster-whisper
depends on `av`, which bundles its own libavdevice; OpenCV bundles a
*different* libavdevice. Loading both in one process crashes on macOS with
an Objective-C class collision ("Class AVFFrameReceiver is implemented in
both ..."). Keeping this in a separate process sidesteps it entirely.
"""
import json
import sys

MODEL_SIZE = "base"


def transcribe_song(audio_path, min_word_prob=0.4, max_no_speech_prob=0.6,
                     max_words_per_line=6, max_line_duration=2.6, gap_break=0.6):
    """Return short, on-screen-sized lyric lines:
    [{text, start, end, words: [{text,start,end}]}].

    Whisper's own segments are full sentences (often 5+ seconds) -- too
    long to show as one caption line, so words are re-chunked into short
    bursts that break on punctuation, a word-count/duration cap, or a
    silence gap. Voice-activity filtering + a no-speech-probability cutoff
    mean instrumental tracks come back with zero lines instead of
    hallucinated captions.
    """
    from faster_whisper import WhisperModel
    try:
        # avoid a Hugging Face Hub network round-trip once the model is cached
        model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8", local_files_only=True)
    except Exception:
        model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        audio_path, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )

    lines = []
    current = []
    for seg in segments:
        if seg.no_speech_prob > max_no_speech_prob:
            if current:
                lines.append(current)
                current = []
            continue
        for w in (seg.words or []):
            text = w.word.strip()
            if not text or w.probability < min_word_prob:
                continue
            word = {"text": text, "start": float(w.start), "end": float(w.end)}
            if current:
                gap = word["start"] - current[-1]["end"]
                duration = word["end"] - current[0]["start"]
                prev_ends_clause = current[-1]["text"].rstrip().endswith((",", ".", "!", "?"))
                if gap > gap_break or len(current) >= max_words_per_line or \
                        duration > max_line_duration or prev_ends_clause:
                    lines.append(current)
                    current = []
            current.append(word)
        if current:
            lines.append(current)
            current = []

    if current:
        lines.append(current)

    return [
        {
            "text": " ".join(w["text"] for w in words),
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "words": words,
        }
        for words in lines if words
    ]


def main():
    audio_path = sys.argv[1]
    lines = transcribe_song(audio_path)
    json.dump(lines, sys.stdout)


if __name__ == "__main__":
    main()
