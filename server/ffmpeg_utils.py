"""Thin subprocess wrappers around the system ffmpeg/ffprobe binaries.
Every video/audio encode, crop, concat and mux in cutwave goes through here.
"""
import json
import os
import subprocess


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "command failed: " + " ".join(cmd) + "\n" +
            result.stderr.decode(errors="ignore")[-3000:]
        )
    return result


def probe(path):
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    data = json.loads(_run(cmd).stdout.decode())
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0) or 0)

    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    width = height = 0
    fps = 30.0
    if video_stream:
        width = int(video_stream.get("width", 0) or 0)
        height = int(video_stream.get("height", 0) or 0)
        rate_str = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "30/1"
        num, _, den = rate_str.partition("/")
        try:
            num_f, den_f = float(num), float(den or 1)
            if den_f:
                fps = num_f / den_f
        except ValueError:
            pass
        if not duration:
            duration = float(video_stream.get("duration", 0) or 0)

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps if fps > 0 else 30.0,
        "has_audio": audio_stream is not None,
        "has_video": video_stream is not None,
    }


def decode_audio_wav(src_path, wav_path, sample_rate=22050):
    cmd = ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", str(sample_rate), "-vn", wav_path]
    _run(cmd)


def extract_audio_window(src_path, start, duration, wav_path, sample_rate=16000):
    """Decode a [start, start+duration) slice of src's audio to mono WAV --
    used to transcribe just a Short's trimmed window (so the resulting
    timestamps are already 0-based, matching the reframed video's own
    timeline) rather than the whole source file."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src_path,
        "-ac", "1", "-ar", str(sample_rate), "-vn", wav_path,
    ]
    _run(cmd)


def extract_and_crop(src, start, dur, crop_rect, target_w, target_h, out_fps, out_path):
    x, y, cw, ch = crop_rect
    vf = f"crop={cw}:{ch}:{x}:{y},scale={target_w}:{target_h}:flags=lanczos,setsar=1,fps={out_fps}"
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
        "-vf", vf, "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    _run(cmd)


def concat_segments(segment_paths, out_path):
    """Concat our own previously-encoded segment files (controlled filenames,
    safe to quote simply) into one silent video via the ffmpeg concat demuxer."""
    list_path = out_path + ".list.txt"
    with open(list_path, "w") as f:
        for p in segment_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path]
        _run(cmd)
    finally:
        os.remove(list_path)


def finalize_with_audio(video_path, audio_path, out_path, duration):
    """Re-encode the (possibly mp4v intermediate) composited video to proper
    H.264 and mux in the song, trimmed/padded to `duration`."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", "0", "-t", f"{duration:.3f}", "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.3f}",
        "-shortest",
        "-movflags", "+faststart",
        out_path,
    ]
    _run(cmd)


def make_thumbnail(video_path, out_path, at_seconds=0.5):
    cmd = ["ffmpeg", "-y", "-ss", f"{at_seconds:.3f}", "-i", video_path, "-vframes", "1", out_path]
    _run(cmd)


def mux_trimmed_source_audio(rendered_video_path, source_path, start, duration, out_path,
                              audio_fade_out=0.0):
    """Mux a rendered (silent) video with a trimmed slice of a *different*
    source file's own audio track -- used for Shorts, where the reframed
    render starts its own timeline at 0 but the matching audio is some
    [start, start+duration) window of the original long-form video. The `?`
    on the audio map makes it optional so silent source videos don't fail.

    `rendered_video_path` may run longer than `duration` (e.g. an outro card
    appended after the main clip) -- the video is used in full and only the
    audio is trimmed, so playback isn't cut short. If `audio_fade_out` > 0,
    the trimmed audio fades to silence over its last that-many seconds
    instead of cutting off abruptly right where the outro begins."""
    af = []
    if audio_fade_out > 0:
        fade_start = max(0.0, duration - audio_fade_out)
        af = ["-af", f"afade=t=out:st={fade_start:.3f}:d={audio_fade_out:.3f}"]
    cmd = [
        "ffmpeg", "-y",
        "-i", rendered_video_path,
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", source_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        *af,
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    _run(cmd)
