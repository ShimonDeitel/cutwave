"""Segment quality scoring, built from scratch with OpenCV frame
differencing -- no ML model. Used to prefer b-roll moments that are
actually watchable: some motion so it doesn't feel frozen, not blown-out
or crushed-black, reasonable contrast.
"""
import cv2
import numpy as np


def sample_quality(cap, fps, start_frame, end_frame, sample_every_s=0.5, max_samples=12):
    step = max(1, int(fps * sample_every_s))
    frame_ids = list(range(start_frame, end_frame, step))[:max_samples]
    prev_gray = None
    motion_scores = []
    brightness_scores = []
    contrast_scores = []
    for f in frame_ids:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        small = cv2.resize(frame, (160, 90))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        brightness_scores.append(gray.mean())
        contrast_scores.append(gray.std())
        if prev_gray is not None:
            motion_scores.append(cv2.absdiff(gray, prev_gray).mean())
        prev_gray = gray

    if not brightness_scores:
        return {"motion": 0.0, "brightness": 0.0, "contrast": 0.0, "quality": 0.0}

    motion = float(np.mean(motion_scores)) if motion_scores else 0.0
    brightness = float(np.mean(brightness_scores))
    contrast = float(np.mean(contrast_scores))

    motion_score = _bell(motion, center=8, width=14)
    brightness_score = _bell(brightness, center=120, width=90)
    contrast_score = min(1.0, contrast / 40.0)

    quality = 0.5 * motion_score + 0.3 * brightness_score + 0.2 * contrast_score
    return {"motion": motion, "brightness": brightness, "contrast": contrast, "quality": quality}


def _bell(x, center, width):
    return float(np.exp(-((x - center) ** 2) / (2 * width ** 2)))
