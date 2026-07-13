"""The editing pipeline: song in, beat-cut b-roll music video out.

    1. detect beats/tempo in the song           (beat_detect.py)
    2. scan every b-roll clip for on-screen text
       and shot quality                          (text_detect.py, scene_score.py)
    3. turn the beat grid into a list of cut points, respecting min/max
       shot length
    4. for every cut, pick the best-scoring clip + time range + crop
       window (least on-screen text, healthiest motion/exposure, and not
       an immediate repeat of the last clip used)
    5. render each chosen segment (crop -> scale -> encode), concatenate
    6. optionally burn in a beat-synced cinematic caption
    7. mux the song back in, trimmed to the final duration

Everything runs through ffmpeg/ffprobe subprocesses plus numpy/scipy/OpenCV
-- no cloud APIs, no ML models to download.
"""
import os

import cv2
import numpy as np

import beat_detect
import captions
import ffmpeg_utils
import jobs
import scene_score
import text_detect

ASPECT_RATIOS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "4:3": (1440, 1080),
}

MIN_SHOT = 0.35
MAX_SHOT = 3.2
MAX_SONG_SECONDS = 600
SAMPLE_EVERY = 0.35


def compute_cut_points(beat_times, total_duration, bpm, min_shot=MIN_SHOT, max_shot=MAX_SHOT):
    beat_interval = 60.0 / max(bpm, 1e-6)
    beats_per_cut = max(1, int(np.ceil(min_shot / beat_interval))) if beat_interval > 0 else 1
    beats = sorted(t for t in beat_times if 0 < t < total_duration)
    chosen = beats[::beats_per_cut]

    points = [0.0]
    for t in chosen:
        last = points[-1]
        if t - last < min_shot:
            continue
        gap = t - last
        if gap > max_shot:
            n_extra = int(gap // max_shot)
            for k in range(1, n_extra + 1):
                points.append(last + gap * k / (n_extra + 1))
        points.append(t)

    tail = total_duration - points[-1]
    if tail > min_shot:
        if tail > max_shot:
            n_extra = int(tail // max_shot)
            last = points[-1]
            for k in range(1, n_extra + 1):
                points.append(last + tail * k / (n_extra + 1))
        points.append(total_duration)
    else:
        points[-1] = total_duration

    durations = [points[i + 1] - points[i] for i in range(len(points) - 1)]
    return points, durations


def analyze_clip(path, sample_every=SAMPLE_EVERY):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = (n_frames / fps) if fps else 0.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    step = max(1, int(fps * sample_every))
    records = []
    prev_gray_small = None
    for f in range(0, max(n_frames, 1), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        t = f / fps
        boxes = text_detect.find_text_boxes(frame)
        small = cv2.resize(frame, (160, 90))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        brightness = float(gray.mean())
        contrast = float(gray.std())
        motion = float(cv2.absdiff(gray, prev_gray_small).mean()) if prev_gray_small is not None else 6.0
        prev_gray_small = gray
        records.append({"t": t, "boxes": boxes, "brightness": brightness, "contrast": contrast, "motion": motion})
    cap.release()
    return {"path": path, "duration": duration, "width": w, "height": h, "fps": fps, "records": records}


def _quality_from_records(records):
    if not records:
        return 0.4
    motion = np.mean([r["motion"] for r in records])
    brightness = np.mean([r["brightness"] for r in records])
    contrast = np.mean([r["contrast"] for r in records])
    motion_score = scene_score._bell(motion, center=8, width=14)
    brightness_score = scene_score._bell(brightness, center=120, width=90)
    contrast_score = min(1.0, contrast / 40.0)
    return 0.5 * motion_score + 0.3 * brightness_score + 0.2 * contrast_score


def _range_overlap_frac(used_ranges, s, e):
    if not used_ranges:
        return 0.0
    dur = e - s
    if dur <= 0:
        return 0.0
    covered = 0.0
    for (us, ue) in used_ranges:
        covered += max(0.0, min(e, ue) - max(s, us))
    return min(1.0, covered / dur)


def select_segments(clip_analyses, cut_durations, target_ratio):
    used_ranges = {i: [] for i in range(len(clip_analyses))}
    segments = []
    last_clip_idx = None
    n_clips = len(clip_analyses)

    for dur in cut_durations:
        best = None
        for ci, analysis in enumerate(clip_analyses):
            clip_dur = analysis["duration"]
            if clip_dur < dur * 0.98:
                continue
            span = max(clip_dur - dur, 0.001)
            n_positions = max(3, min(24, int(span / max(dur, 0.4))))
            starts = np.linspace(0, span, n_positions)
            for s in starts:
                e = s + dur
                recs = [r for r in analysis["records"] if s <= r["t"] < e]
                if not recs and analysis["records"]:
                    recs = [min(analysis["records"], key=lambda r: abs(r["t"] - (s + e) / 2))]
                quality = _quality_from_records(recs)
                boxes = [b for r in recs for b in r["boxes"]]
                text_penalty = text_detect.text_score(boxes, analysis["width"] * analysis["height"]) * 0.6
                overlap_penalty = _range_overlap_frac(used_ranges[ci], s, e) * 0.5
                repeat_penalty = 0.2 if (ci == last_clip_idx and n_clips > 1) else 0.0
                score = quality - text_penalty - overlap_penalty - repeat_penalty
                if best is None or score > best[0]:
                    best = (score, ci, float(s), float(e), boxes, analysis["width"], analysis["height"])

        if best is None:
            ci = 0
            s, e = 0.0, min(dur, clip_analyses[0]["duration"])
            boxes = []
            w, h = clip_analyses[0]["width"], clip_analyses[0]["height"]
        else:
            _, ci, s, e, boxes, w, h = best

        crop = text_detect.best_crop(boxes, w, h, target_ratio)
        segments.append({"clip_index": ci, "path": clip_analyses[ci]["path"], "start": s, "dur": e - s, "crop": crop})
        used_ranges[ci].append((s, e))
        last_clip_idx = ci

    return segments


def _progress(job_id, frac, message):
    jobs.update_job(job_id, progress=round(frac, 3), message=message)


def run_job(job_id, song_path, broll_paths, aspect_ratio, caption_text, work_dir, output_path):
    os.makedirs(work_dir, exist_ok=True)
    target_w, target_h = ASPECT_RATIOS.get(aspect_ratio, ASPECT_RATIOS["9:16"])
    target_ratio = target_w / target_h

    _progress(job_id, 0.03, "Listening to the track...")
    song_info = ffmpeg_utils.probe(song_path)
    song_duration = min(song_info["duration"], MAX_SONG_SECONDS)
    beats = beat_detect.detect_beats(song_path, work_dir)
    bpm = beats["bpm"]
    beat_times = beats["beat_times"]

    _progress(job_id, 0.10, f"Found the beat: {bpm:.0f} BPM. Mapping cuts to it...")
    cut_points, cut_durations = compute_cut_points(beat_times, song_duration, bpm)
    total_cuts = len(cut_durations)

    _progress(job_id, 0.15, f"Scanning {len(broll_paths)} b-roll clip(s) for on-screen text...")
    clip_analyses = []
    for i, p in enumerate(broll_paths):
        clip_analyses.append(analyze_clip(p))
        _progress(job_id, 0.15 + 0.25 * ((i + 1) / max(1, len(broll_paths))),
                   f"Scanned clip {i + 1}/{len(broll_paths)} for text and motion...")

    _progress(job_id, 0.42, f"Cutting {total_cuts} shots on the beat...")
    segments = select_segments(clip_analyses, cut_durations, target_ratio)

    seg_dir = os.path.join(work_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    seg_paths = []
    for i, seg in enumerate(segments):
        out_seg = os.path.join(seg_dir, f"seg_{i:04d}.mp4")
        ffmpeg_utils.extract_and_crop(
            seg["path"], seg["start"], seg["dur"], seg["crop"], target_w, target_h, 30, out_seg,
        )
        seg_paths.append(out_seg)
        _progress(job_id, 0.42 + 0.33 * ((i + 1) / max(1, total_cuts)),
                   f"Cropping & cutting shot {i + 1}/{total_cuts}...")

    _progress(job_id, 0.78, "Stitching the sequence together...")
    silent_path = os.path.join(work_dir, "silent.mp4")
    ffmpeg_utils.concat_segments(seg_paths, silent_path)

    video_for_mux = silent_path
    if caption_text and caption_text.strip():
        _progress(job_id, 0.83, "Adding cinematic beat-synced captions...")
        captioned_path = os.path.join(work_dir, "captioned.mp4")

        def _cap_progress(frac):
            _progress(job_id, 0.83 + 0.10 * frac, "Adding cinematic beat-synced captions...")

        captions.burn_beat_captions(silent_path, captioned_path, caption_text.strip(), beat_times,
                                     progress_cb=_cap_progress)
        video_for_mux = captioned_path

    _progress(job_id, 0.95, "Mixing in the track...")
    ffmpeg_utils.finalize_with_audio(video_for_mux, song_path, output_path, song_duration)

    thumb_path = output_path.rsplit(".", 1)[0] + "_thumb.jpg"
    ffmpeg_utils.make_thumbnail(output_path, thumb_path, at_seconds=min(1.0, song_duration / 2))

    jobs.update_job(
        job_id, status="done", progress=1.0, message="Done.",
        result={
            "video_path": output_path,
            "thumb_path": thumb_path,
            "bpm": bpm,
            "cuts": total_cuts,
            "duration": song_duration,
            "aspect_ratio": aspect_ratio,
        },
    )
