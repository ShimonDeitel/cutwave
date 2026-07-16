"""YouTube Shorts pipeline: take one existing long-form video, auto-pick its
most visually active window to match a user-chosen target duration (biased
against windows that span a hard scene cut, since real-world sources are
often multi-clip compilations), track whatever's moving and reframe to a
screen-filling 9:16 crop that follows it, zooming in only when a detected
face is small enough that it's worth bringing closer -- not a constant
cosmetic push-in -- and keep the source's own audio and on-screen text
untouched (this mode doesn't try to avoid text -- it's the source's own
content, not incidental b-roll overlay). Optionally burns in auto-transcribed
subtitles and/or appends a fade-to-blur outro card with a call-to-action line.

    1. probe the video; if it's already at or under the target duration,
       use all of it -- otherwise scan the whole thing for the most visually
       engaging contiguous window of that length (motion/brightness/
       contrast, same scoring as the long-form b-roll picker, penalized if
       a hard cut falls inside it)
    2. re-encode just that window to its own short file -- OpenCV's frame
       seeking is unreliable deep into a long (especially multi-clip) source
       file, so every later pass only ever touches a short, well-formed clip
    3. track the main subject across that window (reframe.py)
    4. render every frame: interpolate the smoothed track (position + a
       content-aware zoom), crop, scale, write
    5. optionally transcribe that window's own speech and burn in subtitles
    6. optionally append an outro card (outro.py)
    7. mux the original audio back in, trimmed to the same window, fading
       out under the outro if one was added
"""
import os

import cv2
import numpy as np

import captions
import ffmpeg_utils
import outro as outro_mod
import reframe
import scene_score
import transcribe

TARGET_RATIO = 9 / 16
OUTPUT_W, OUTPUT_H = 1080, 1920
OUTPUT_FPS = 30

MIN_DURATION = 10.0
MAX_DURATION = 60.0
DEFAULT_DURATION = 45.0
HIGHLIGHT_SAMPLE_EVERY = 0.5

CUT_MOTION_THRESHOLD = 34.0   # frame-to-frame diff above this = a hard scene cut, not just fast motion
CUT_PENALTY = 0.6             # subtracted from a candidate window's avg score per cut it contains

DEFAULT_OUTRO_TEXT = "Listen to the full song on our YouTube channel"


def _scan_quality(path, sample_every=HIGHLIGHT_SAMPLE_EVERY):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, int(fps * sample_every))
    prev_gray = None
    samples = []
    for f in range(0, max(n_frames, 1), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        t = f / fps
        small = cv2.resize(frame, (160, 90))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        brightness = float(gray.mean())
        contrast = float(gray.std())
        motion = float(cv2.absdiff(gray, prev_gray).mean()) if prev_gray is not None else 6.0
        prev_gray = gray
        motion_score = scene_score._bell(motion, center=8, width=14)
        brightness_score = scene_score._bell(brightness, center=120, width=90)
        contrast_score = min(1.0, contrast / 40.0)
        quality = 0.5 * motion_score + 0.3 * brightness_score + 0.2 * contrast_score
        samples.append((t, quality, motion))
    cap.release()
    return samples


def find_highlight_window(samples, target_duration, total_duration):
    if not samples or total_duration <= target_duration:
        return 0.0, min(target_duration, total_duration)

    times = np.array([s[0] for s in samples])
    scores = np.array([s[1] for s in samples])
    is_cut = np.array([s[2] > CUT_MOTION_THRESHOLD for s in samples])

    best_start, best_avg = 0.0, -1.0
    for t0 in times:
        t1 = t0 + target_duration
        if t1 > total_duration:
            break
        mask = (times >= t0) & (times < t1)
        if not mask.any():
            continue
        # a cut right at the very start of the window is fine (that's just
        # where the highlight begins) -- only penalize cuts *inside* it
        interior_cuts = int(is_cut[mask].sum()) - (1 if is_cut[mask][0] else 0)
        avg = float(scores[mask].mean()) - CUT_PENALTY * max(0, interior_cuts)
        if avg > best_avg:
            best_avg, best_start = avg, float(t0)
    return best_start, target_duration


def _render_reframed(video_path, track, out_path, progress_cb=None):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (OUTPUT_W, OUTPUT_H))

    if track:
        track_t = np.array([p[0] for p in track])
        track_x = np.array([p[1] for p in track])
        track_y = np.array([p[2] for p in track])
        track_z = np.array([p[3] for p in track])
    else:
        track_t = np.array([])

    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = i / fps
        center, zoom = None, 1.0
        if len(track_t):
            cx = float(np.interp(t, track_t, track_x))
            cy = float(np.interp(t, track_t, track_y))
            zoom = float(np.interp(t, track_t, track_z))
            center = (cx, cy)
        x0, y0, cw, ch = reframe.compute_crop_box(w, h, center, TARGET_RATIO, zoom=zoom)
        cropped = frame[y0:y0 + ch, x0:x0 + cw]
        resized = cv2.resize(cropped, (OUTPUT_W, OUTPUT_H), interpolation=cv2.INTER_LINEAR)
        writer.write(resized)
        i += 1
        if progress_cb and i % 30 == 0:
            progress_cb(min(0.99, i / n_frames))

    cap.release()
    writer.release()


def _last_frame(video_path):
    cap = cv2.VideoCapture(video_path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - 1))
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def _render_one_clip(video_path, start, duration, work_dir, output_path,
                      add_subtitles, add_outro, outro_text, outro_hold,
                      progress_cb=None):
    """Runs the full per-window pipeline -- extract, track, reframe,
    optional subtitles, optional outro, mux audio -- for a single
    [start, start+duration) window of video_path, writing the finished clip
    to output_path. Shared by both the single auto-highlight job and the
    sequential-batch job so they don't duplicate this logic. progress_cb, if
    given, is called (frac, message) with frac in 0..1 across just this one
    clip. Returns a result dict describing what was produced."""
    os.makedirs(work_dir, exist_ok=True)

    def _p(frac, msg):
        if progress_cb:
            progress_cb(frac, msg)

    _p(0.0, "Extracting the selected window...")
    window_path = os.path.join(work_dir, "window.mp4")
    ffmpeg_utils.extract_window(video_path, start, duration, window_path, fps=OUTPUT_FPS)

    _p(0.10, "Tracking the action to keep it in frame...")
    track, used_face = reframe.track_subject(window_path, 0.0, duration)

    _p(0.20, "Reframing to fill a vertical screen...")
    silent_path = os.path.join(work_dir, "reframed.mp4")
    _render_reframed(window_path, track, silent_path,
                      progress_cb=lambda f: _p(0.20 + 0.40 * f, "Reframing to fill a vertical screen..."))
    current_silent = silent_path

    lyric_lines = []
    if add_subtitles:
        _p(0.62, "Transcribing speech for subtitles...")
        audio_wav = os.path.join(work_dir, "short_audio.wav")
        ffmpeg_utils.extract_audio_window(video_path, start, duration, audio_wav)
        try:
            lyric_lines = transcribe.transcribe_song(audio_wav)
        except Exception:
            lyric_lines = []
        if lyric_lines:
            _p(0.75, "Burning in subtitles...")
            captioned_path = os.path.join(work_dir, "captioned.mp4")
            captions.burn_subtitles(current_silent, captioned_path, lyric_lines)
            current_silent = captioned_path

    audio_fade_out = 0.0
    outro_added = False
    outro_total = 0.0
    if add_outro:
        _p(0.85, "Building the outro card...")
        last = _last_frame(current_silent)
        if last is not None:
            outro_path = os.path.join(work_dir, "outro.mp4")
            cta_text = (outro_text or "").strip() or DEFAULT_OUTRO_TEXT
            outro_total = outro_mod.render_outro_card(last, OUTPUT_FPS, cta_text, outro_path,
                                                        hold_duration=outro_hold)
            combined_path = os.path.join(work_dir, "combined.mp4")
            ffmpeg_utils.concat_segments([current_silent, outro_path], combined_path)
            current_silent = combined_path
            audio_fade_out = outro_mod.BLACK_DURATION
            outro_added = True

    _p(0.92, "Mixing the original audio back in...")
    ffmpeg_utils.mux_trimmed_source_audio(current_silent, video_path, start, duration, output_path,
                                           audio_fade_out=audio_fade_out)

    thumb_path = output_path.rsplit(".", 1)[0] + "_thumb.jpg"
    ffmpeg_utils.make_thumbnail(output_path, thumb_path, at_seconds=min(1.0, duration / 2))
    _p(1.0, "Done.")

    total_output_duration = duration + (outro_total if outro_added else 0.0)
    return {
        "video_path": output_path,
        "thumb_path": thumb_path,
        "start": start,
        "duration": duration,
        "total_duration": total_output_duration,
        "tracked": bool(track),
        "face_tracked": used_face,
        "subtitled": bool(lyric_lines),
        "outro": outro_added,
    }


def run_short_job(job_id, video_path, work_dir, output_path,
                   target_duration=DEFAULT_DURATION, add_subtitles=False,
                   add_outro=False, outro_text="", outro_duration=None):
    import jobs

    def _progress(frac, message):
        jobs.update_job(job_id, progress=round(frac, 3), message=message)

    target_duration = max(MIN_DURATION, min(MAX_DURATION, float(target_duration or DEFAULT_DURATION)))
    outro_hold = max(0.5, min(8.0, float(outro_duration))) if outro_duration else outro_mod.HOLD_DURATION

    _progress(0.05, "Reading the video...")
    info = ffmpeg_utils.probe(video_path)
    total_duration = info["duration"]

    if total_duration <= target_duration:
        start, duration = 0.0, total_duration
        _progress(0.10, f"Video is already short ({duration:.0f}s) -- reframing all of it...")
    else:
        _progress(0.10, "Scanning the full video for the most engaging moment...")
        samples = _scan_quality(video_path)
        start, duration = find_highlight_window(samples, target_duration, total_duration)
        _progress(0.18, f"Found a highlight at {start:.0f}s-{start + duration:.0f}s...")

    def _clip_progress(frac, message):
        _progress(0.18 + 0.80 * frac, message)

    result = _render_one_clip(video_path, start, duration, work_dir, output_path,
                               add_subtitles, add_outro, outro_text, outro_hold,
                               progress_cb=_clip_progress)

    jobs.update_job(
        job_id, status="done", progress=1.0, message="Done.",
        result={
            **result,
            "mode": "short",
            "source_duration": total_duration,
            "highlight_start": result["start"],
            "aspect_ratio": "9:16",
        },
    )


def run_short_batch_job(job_id, video_path, work_dir, output_dir,
                         count=3, target_duration=DEFAULT_DURATION,
                         add_subtitles=False, add_outro=False, outro_text="",
                         outro_duration=None):
    """Splits video_path into up to `count` sequential, non-overlapping
    windows of target_duration seconds each -- 0-D, D-2D, 2D-3D, ... in
    order, rather than auto-picking one highlight -- so a long source
    becomes a series of shorts walking through it in order. Stops early
    (producing fewer than `count`) once there isn't a full target_duration
    window of source left."""
    import jobs

    def _progress(frac, message):
        jobs.update_job(job_id, progress=round(frac, 3), message=message)

    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    count = max(1, min(20, int(count or 1)))
    target_duration = max(MIN_DURATION, min(MAX_DURATION, float(target_duration or DEFAULT_DURATION)))
    outro_hold = max(0.5, min(8.0, float(outro_duration))) if outro_duration else outro_mod.HOLD_DURATION

    _progress(0.02, "Reading the video...")
    info = ffmpeg_utils.probe(video_path)
    total_duration = info["duration"]

    if total_duration <= target_duration:
        planned = 1
    else:
        planned = min(count, max(1, int(total_duration // target_duration)))

    clips = []
    for i in range(planned):
        start = i * target_duration
        duration = target_duration if total_duration > target_duration else total_duration
        if start + duration > total_duration + 0.01:
            break

        clip_work_dir = os.path.join(work_dir, f"clip_{i + 1}")
        output_path = os.path.join(output_dir, f"clip_{i + 1}.mp4")

        def _clip_progress(frac, message, idx=i):
            overall = (idx + frac) / planned
            _progress(0.02 + 0.96 * overall, f"Clip {idx + 1}/{planned}: {message}")

        result = _render_one_clip(video_path, start, duration, clip_work_dir, output_path,
                                   add_subtitles, add_outro, outro_text, outro_hold,
                                   progress_cb=_clip_progress)
        clips.append({**result, "index": i + 1})

    jobs.update_job(
        job_id, status="done", progress=1.0, message="Done.",
        result={
            "mode": "short_batch",
            "clips": clips,
            "requested_count": count,
            "produced_count": len(clips),
            "source_duration": total_duration,
            "aspect_ratio": "9:16",
        },
    )
