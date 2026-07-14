"""Outro card for YouTube Shorts: the last frame fades to black, crossfades
into a heavily blurred freeze-frame, then a call-to-action line fades in on
top and holds -- e.g. pointing viewers to the full song/video elsewhere.

Built the same way as captions.py (a PIL text sprite alpha-blended per
frame with OpenCV), since this ffmpeg build has no drawtext/fontconfig.
"""
import textwrap

import cv2
import numpy as np

import captions

BLACK_DURATION = 0.5
BLUR_DURATION = 0.7
HOLD_DURATION = 2.3           # default CTA hold time -- overridable per job
TEXT_FADE_IN = 0.6
TOTAL_DURATION = BLACK_DURATION + BLUR_DURATION + HOLD_DURATION  # kept for callers using the default


def _heavy_blur(frame_bgr):
    h, w = frame_bgr.shape[:2]
    small = cv2.resize(frame_bgr, (max(1, w // 24), max(1, h // 24)), interpolation=cv2.INTER_LINEAR)
    small = cv2.GaussianBlur(small, (0, 0), sigmaX=3)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _wrap_text(text, width_chars=26):
    return "\n".join(textwrap.wrap(text, width=width_chars)) or text


def render_outro_card(last_frame_bgr, fps, text, out_path, hold_duration=HOLD_DURATION):
    """Writes a silent mp4v clip: last_frame -> black -> blurred(last_frame),
    with `text` fading in on top of the blurred frame and holding for
    `hold_duration` seconds. Returns the clip's total duration in seconds."""
    h, w = last_frame_bgr.shape[:2]
    blurred = _heavy_blur(last_frame_bgr)

    font_size = max(20, int(h * 0.045))
    wrapped = _wrap_text(text)
    max_w = w * 0.82
    sprite = captions._render_text_sprite(wrapped, font_size, fill=(255, 255, 255, 255),
                                           stroke_fill=(10, 10, 10, 235))
    while sprite.shape[1] > max_w and font_size > 14:
        font_size = int(font_size * 0.88)
        sprite = captions._render_text_sprite(wrapped, font_size, fill=(255, 255, 255, 255),
                                               stroke_fill=(10, 10, 10, 235))

    sh, sw = sprite.shape[:2]
    tx, ty = (w - sw) // 2, (h - sh) // 2

    n_black = max(1, int(BLACK_DURATION * fps))
    n_blur = max(1, int(BLUR_DURATION * fps))
    n_hold = max(1, int(hold_duration * fps))
    n_text_fade = max(1, int(min(TEXT_FADE_IN, hold_duration) * fps))

    black = np.zeros_like(last_frame_bgr)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (w, h))

    for i in range(n_black):
        a = (i + 1) / n_black
        writer.write(cv2.addWeighted(last_frame_bgr, 1 - a, black, a, 0))

    for i in range(n_blur):
        a = (i + 1) / n_blur
        writer.write(cv2.addWeighted(black, 1 - a, blurred, a, 0))

    for i in range(n_hold):
        frame = blurred.copy()
        text_a = min(1.0, (i + 1) / n_text_fade)
        faded_sprite = sprite.copy()
        faded_sprite[:, :, 3] = (faded_sprite[:, :, 3].astype(np.float32) * text_a).astype(np.uint8)
        captions._alpha_blend(frame, faded_sprite, tx, ty)
        writer.write(frame)

    writer.release()
    return BLACK_DURATION + BLUR_DURATION + hold_duration
