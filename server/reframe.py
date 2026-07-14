"""Subject-tracking reframe for YouTube Shorts, written from scratch with
OpenCV frame-differencing -- no downloaded model. Samples the source video,
finds the largest moving blob per sample (dilate + contours on a frame
diff, not a raw pixel-mean, so scattered background noise doesn't pull the
centroid off the real subject), and smooths it into a track the renderer
interpolates into a per-frame crop -- so a screen-filling 9:16 crop follows
the action instead of a static center-crop.

(OpenCV 5.x dropped the classic `cv2.CascadeClassifier` Haar-cascade face
detector from these bindings, and its replacement, `FaceDetectorYN`, needs a
separately downloaded ONNX model -- so face tracking isn't available without
fetching an external asset. Motion is a genuinely useful signal on its own:
it finds whatever's actually moving/talking/acting in the shot.)
"""
import cv2
import numpy as np

DETECT_SCALE = 0.5
MOTION_DIFF_THRESHOLD = 25
MOTION_MIN_PIXELS = 80
EMA_ALPHA = 0.35


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


def track_subject(video_path, start_time, duration, sample_every=0.15):
    """Return a list of (t_relative, cx, cy) sample points tracking the
    largest moving region across [start_time, start_time+duration),
    smoothed with an EMA so the crop doesn't jitter. Returns [] if nothing
    with meaningful motion was found (caller falls back to a static
    center crop)."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    start_frame = int(start_time * fps)
    end_frame = int((start_time + duration) * fps)
    step = max(1, int(fps * sample_every))

    prev_small = None
    raw = []
    for f in range(start_frame, end_frame, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        t = f / fps - start_time
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (0, 0), fx=DETECT_SCALE, fy=DETECT_SCALE)
        center = _motion_centroid(prev_small, small) if prev_small is not None else None
        prev_small = small
        if center is not None:
            raw.append((t, center[0], center[1]))
    cap.release()

    if not raw:
        return []

    smoothed = []
    sx, sy = raw[0][1], raw[0][2]
    for t, x, y in raw:
        sx += EMA_ALPHA * (x - sx)
        sy += EMA_ALPHA * (y - sy)
        smoothed.append((t, sx, sy))
    return smoothed


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
