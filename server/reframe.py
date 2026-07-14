"""Subject-tracking reframe for YouTube Shorts. Prefers a detected face
(OpenCV's built-in YuNet DNN detector, via cv2.FaceDetectorYN) so the crop
follows an actual subject rather than raw motion; falls back to the largest
moving region (frame-diff + dilate + biggest contour centroid -- not a raw
pixel-mean, so scattered background motion doesn't drag the crop off the
real subject) whenever no face is found, so non-portrait footage (gameplay,
action, general b-roll) still tracks something sensible. Both signals feed
the same EMA smoothing and get interpolated into a per-frame crop by the
renderer.

(OpenCV 5.x dropped the classic `cv2.CascadeClassifier` Haar-cascade face
detector from these Python bindings. YuNet is the modern built-in
replacement, at the cost of one small (~230KB) ONNX model file fetched from
the official OpenCV Zoo -- models/face_detection_yunet_2023mar.onnx.)
"""
import os

import cv2
import numpy as np

_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "models", "face_detection_yunet_2023mar.onnx")

DETECT_SCALE = 0.5
FACE_SCORE_THRESHOLD = 0.6
MOTION_DIFF_THRESHOLD = 25
MOTION_MIN_PIXELS = 80
EMA_ALPHA = 0.35
ZOOM_EMA_ALPHA = 0.20
TARGET_FACE_FRAC = 0.32      # detected face height, as a fraction of crop height, that counts as "close enough"
MAX_NEEDED_ZOOM = 1.6        # cap how far we'll zoom in to reach that target -- avoids quality-destroying blow-ups

_face_detector = None
_face_detector_size = None


def _get_face_detector(w, h):
    global _face_detector, _face_detector_size
    if _face_detector is None:
        _face_detector = cv2.FaceDetectorYN_create(
            _MODEL_PATH, "", (w, h), score_threshold=FACE_SCORE_THRESHOLD,
        )
        _face_detector_size = (w, h)
    elif _face_detector_size != (w, h):
        _face_detector.setInputSize((w, h))
        _face_detector_size = (w, h)
    return _face_detector


def _detect_face(frame_bgr, small_bgr):
    """Returns (cx, cy, face_h) in source-frame pixels, or None."""
    h, w = small_bgr.shape[:2]
    detector = _get_face_detector(w, h)
    _, faces = detector.detect(small_bgr)
    if faces is None or len(faces) == 0:
        return None
    best = max(faces, key=lambda f: f[2] * f[3])
    cx = (best[0] + best[2] / 2) / DETECT_SCALE
    cy = (best[1] + best[3] / 2) / DETECT_SCALE
    face_h = best[3] / DETECT_SCALE
    return (float(cx), float(cy), float(face_h))


def _motion_centroid(prev_small_gray, small_gray):
    diff = cv2.absdiff(small_gray, prev_small_gray)
    mask = (diff > MOTION_DIFF_THRESHOLD).astype(np.uint8)
    if int(mask.sum()) < MOTION_MIN_PIXELS:
        return None
    kernel = np.ones((9, 9), np.uint8)
    merged = cv2.dilate(mask, kernel, iterations=2)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    m = cv2.moments(largest)
    if m["m00"] == 0:
        return None
    cx = m["m10"] / m["m00"]
    cy = m["m01"] / m["m00"]
    return (cx / DETECT_SCALE, cy / DETECT_SCALE)


def track_subject(video_path, start_time, duration, sample_every=0.1):
    """Return (track, used_face) where track is a list of
    (t_relative, cx, cy, zoom) sample points tracking the main subject
    across [start_time, start_time+duration), smoothed with an EMA so the
    crop doesn't jitter, and used_face reports whether a real face was found
    at all (vs. falling back to motion the whole way). track is [] if
    nothing trackable was found at all (caller falls back to a static
    center crop).

    `zoom` is 1.0 (no zoom) unless a detected *face* is small relative to
    the frame, in which case it's just enough to bring the face up to a
    comfortable on-screen size, capped at MAX_NEEDED_ZOOM -- motion-only
    tracking never zooms, since there's no reliable subject size to zoom
    towards, and constant cosmetic zoom independent of content just
    magnifies compression artifacts without making anything easier to see.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080.0
    start_frame = int(start_time * fps)
    end_frame = int((start_time + duration) * fps)
    step = max(1, int(fps * sample_every))

    prev_small_gray = None
    raw = []
    face_hits = 0
    for f in range(start_frame, end_frame, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        t = f / fps - start_time
        small_bgr = cv2.resize(frame, (0, 0), fx=DETECT_SCALE, fy=DETECT_SCALE)
        small_gray = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)

        face = _detect_face(frame, small_bgr)
        if face is not None:
            face_hits += 1
            cx, cy, face_h = face
            needed = TARGET_FACE_FRAC * frame_h / max(1.0, face_h)
            zoom = min(MAX_NEEDED_ZOOM, max(1.0, needed))
        else:
            zoom = 1.0
            center = _motion_centroid(prev_small_gray, small_gray) if prev_small_gray is not None else None
            cx, cy = center if center else (None, None)
        prev_small_gray = small_gray

        if cx is not None:
            raw.append((t, cx, cy, zoom))
    cap.release()

    if not raw:
        return [], False

    smoothed = []
    sx, sy, sz = raw[0][1], raw[0][2], raw[0][3]
    for t, x, y, z in raw:
        sx += EMA_ALPHA * (x - sx)
        sy += EMA_ALPHA * (y - sy)
        sz += ZOOM_EMA_ALPHA * (z - sz)
        smoothed.append((t, sx, sy, sz))
    return smoothed, face_hits > 0


def compute_crop_box(frame_w, frame_h, center, target_ratio, zoom=1.0):
    """(x0, y0, crop_w, crop_h) for a target_ratio (w/h) crop centered on
    `center`, clamped inside the frame. `zoom` > 1 shrinks the crop (pushing
    in) around the same center for a slow Ken-Burns-style push. Falls back
    to the frame center when `center` is None."""
    src_ratio = frame_w / frame_h
    if src_ratio > target_ratio:
        crop_h = frame_h
        crop_w = round(crop_h * target_ratio)
    else:
        crop_w = frame_w
        crop_h = round(crop_w / target_ratio)

    crop_w = max(2, int(round(crop_w / zoom)))
    crop_h = max(2, int(round(crop_h / zoom)))

    cx, cy = center if center else (frame_w / 2, frame_h / 2)
    x0 = int(round(cx - crop_w / 2))
    y0 = int(round(cy - crop_h / 2))
    x0 = max(0, min(frame_w - crop_w, x0))
    y0 = max(0, min(frame_h - crop_h, y0))
    return x0, y0, crop_w, crop_h
