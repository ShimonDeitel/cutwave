"""YouTube Shorts pipeline: take one existing long-form video, auto-pick its
most visually active window to match a user-chosen target duration, track
whatever's moving and reframe to a screen-filling 9:16 crop that follows it
with a slow push-in, and keep the source's own audio and on-screen text
untouched (this mode doesn't try to avoid text -- it's the source's own
content, not incidental b-roll overlay). Optionally burns in auto-transcribed
subtitles and/or appends a fade-to-blur outro card with a call-to-action line.

    1. probe the video; if it's already at or under the target duration,
       use all of it -- otherwise scan the whole thing for the most visually
       engaging contiguous window of that length (motion/brightness/
       contrast, same scoring as the long-form b-roll picker)
    2. track the largest moving region across that window (reframe.py)
    3. render every frame: interpolate the smoothed track, crop, scale,
       write, with a gentle continuous zoom-in for some cinematic motion
    4. optionally transcribe that window's own speech and burn in subtitles
    5. optionally append an outro card (outro.py)
    6. mux the original audio back in, trimmed to the same window, fading
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

MAX_PUSH_IN = 0.08                # slow zoom from 1.0x to 1.0+MAX_PUSH_IN across the clip
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
        samples.append((t, quality))
    cap.release()
    return samples


def find_highlight_window(samples, target_duration, total_duration):
    if not samples or total_duration <= target_duration:
        return 0.0, min(target_duration, total_duration)

    times = np.array([s[0] for s in samples])
    scores = np.array([s[1] for s in samples])

    best_start, best_avg = 0.0, -1.0
    for t0 in times:
        t1 = t0 + target_duration
        if t1 > total_duration:
            break
        mask = (times >= t0) & (times < t1)
        if not mask.any():
            continue
        avg = float(scores[mask].mean())
        if avg > best_avg:
            best_avg, best_start = avg, float(t0)
    return best_start, target_duration


def _render_reframed(video_path, start, duration, track, out_path, progress_cb=None):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    start_frame = int(start * fps)
    n_frames = max(1, int(duration * fps))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (OUTPUT_W, OUTPUT_H))

    if track:
        track_t = np.array([p[0] for p in track])
        track_x = np.array([p[1] for p in track])
        track_y = np.array([p[2] for p in track])
    else:
        track_t = np.array([])

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    for i in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            break
        t = i / fps
        center = None
        if len(track_t):
            cx = float(np.interp(t, track_t, track_x))
            cy = float(np.interp(t, track_t, track_y))
            center = (cx, cy)
        zoom = 1.0 + MAX_PUSH_IN * min(1.0, t / duration)
        x0, y0, cw, ch = reframe.compute_crop_box(w, h, center, TARGET_RATIO, zoom=zoom)
        cropped = frame[y0:y0 + ch, x0:x0 + cw]
        resized = cv2.resize(cropped, (OUTPUT_W, OUTPUT_H), interpolation=cv2.INTER_LINEAR)
        writer.write(resized)
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


def run_short_job(job_id, video_path, work_dir, output_path,
                   target_duration=DEFAULT_DURATION, add_subtitles=False,
                   add_outro=False, outro_text=""):
    import jobs

    def _progress(frac, message):
        jobs.update_job(job_id, progress=round(frac, 3), message=message)

    os.makedirs(work_dir, exist_ok=True)
    target_duration = max(MIN_DURATION, min(MAX_DURATION, float(target_duration or DEFAULT_DURATION)))

    _progress(0.05, "Reading the video...")
    info = ffmpeg_utils.probe(video_path)
    total_duration = info["duration"]

    if total_duration <= target_duration:
        start, duration = 0.0, total_duration
        _progress(0.12, f"Video is already short ({duration:.0f}s) -- reframing all of it...")
    else:
        _progress(0.12, "Scanning the full video for the most engaging moment...")
        samples = _scan_quality(video_path)
        start, duration = find_highlight_window(samples, target_duration, total_duration)
        _progress(0.28, f"Found a highlight at {start:.0f}s-{start + duration:.0f}s...")

    _progress(0.32, "Tracking the action to keep it in frame...")
    track, used_face = reframe.track_subject(video_path, start, duration)

    _progress(0.38, "Reframing to fill a vertical screen...")
    silent_path = os.path.join(work_dir, "reframed.mp4")

    def _render_progress(frac):
        _progress(0.38 + 0.32 * frac, "Reframing to fill a vertical screen...")

    _render_reframed(video_path, start, duration, track, silent_path, progress_cb=_render_progress)
    current_silent = silent_path

    lyric_lines = []
    if add_subtitles:
        _progress(0.72, "Transcribing speech for subtitles...")
        audio_wav = os.path.join(work_dir, "short_audio.wav")
        ffmpeg_utils.extract_audio_window(video_path, start, duration, audio_wav)
        try:
            lyric_lines = transcribe.transcribe_song(audio_wav)
        except Exception:
            lyric_lines = []
        if lyric_lines:
            _progress(0.80, "Burning in subtitles...")
            captioned_path = os.path.join(work_dir, "captioned.mp4")
            captions.burn_lyric_captions(current_silent, captioned_path, lyric_lines, [0.0])
            current_silent = captioned_path

    audio_fade_out = 0.0
    outro_added = False
    if add_outro:
        _progress(0.86, "Building the outro card...")
        last = _last_frame(current_silent)
        if last is not None:
            outro_path = os.path.join(work_dir, "outro.mp4")
            cta_text = (outro_text or "").strip() or DEFAULT_OUTRO_TEXT
            outro_mod.render_outro_card(last, OUTPUT_FPS, cta_text, outro_path)
            combined_path = os.path.join(work_dir, "combined.mp4")
            ffmpeg_utils.concat_segments([current_silent, outro_path], combined_path)
            current_silent = combined_path
            audio_fade_out = outro_mod.BLACK_DURATION
            outro_added = True

    _progress(0.94, "Mixing the original audio back in...")
    ffmpeg_utils.mux_trimmed_source_audio(current_silent, video_path, start, duration, output_path,
                                           audio_fade_out=audio_fade_out)

    thumb_path = output_path.rsplit(".", 1)[0] + "_thumb.jpg"
    ffmpeg_utils.make_thumbnail(output_path, thumb_path, at_seconds=min(1.0, duration / 2))

    total_output_duration = duration + (outro_mod.TOTAL_DURATION if outro_added else 0.0)

    jobs.update_job(
        job_id, status="done", progress=1.0, message="Done.",
        result={
            "video_path": output_path,
            "thumb_path": thumb_path,
            "mode": "short",
            "source_duration": total_duration,
            "highlight_start": start,
            "duration": duration,
            "total_duration": total_output_duration,
            "tracked": bool(track),
            "face_tracked": used_face,
            "aspect_ratio": "9:16",
            "subtitled": bool(lyric_lines),
            "outro": outro_added,
        },
    )
