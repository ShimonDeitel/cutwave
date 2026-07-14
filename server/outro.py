"""Outro card for YouTube Shorts: the last frame fades to black, crossfades
into a heavily blurred freeze-frame, then a call-to-action line fades in on
top and holds -- e.g. pointing viewers to the full song/video elsewhere.

Built the same way as captions.py (a PIL text sprite alpha-blended per
frame with OpenCV), since this ffmpeg build has no drawtext/fontconfig.
"""
import textwrap

import cv2
import numpy as np
from PIL import Image, ImageDraw

import captions

BLACK_DURATION = 0.5
BLUR_DURATION = 0.7
HOLD_DURATION = 2.3           # default CTA hold time -- overridable per job
TEXT_FADE_IN = 0.6
TOTAL_DURATION = BLACK_DURATION + BLUR_DURATION + HOLD_DURATION  # kept for callers using the default

YOUTUBE_RED = (255, 0, 0)


def _ease(x):
    """Ease-in-out cubic -- smoother than a linear crossfade for both the
    black and blur transitions."""
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - ((-2 * x + 2) ** 3) / 2


def _heavy_blur(frame_bgr):
    h, w = frame_bgr.shape[:2]
    small = cv2.resize(frame_bgr, (max(1, w // 24), max(1, h // 24)), interpolation=cv2.INTER_LINEAR)
    small = cv2.GaussianBlur(small, (0, 0), sigmaX=3)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _wrap_text(text, width_chars=26):
    return "\n".join(textwrap.wrap(text, width=width_chars)) or text


def _youtube_icon_sprite(size):
    """A simple play-button icon (rounded red rect + white triangle) drawn
    from scratch with PIL -- not a downloaded logo asset, just the familiar
    "video/play" shape to reinforce a YouTube call-to-action."""
    w, h = size, int(size * 0.7)
    scale = 4  # supersample for clean rounded-rect/triangle edges
    canvas = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([0, 0, w * scale - 1, h * scale - 1], radius=int(h * scale * 0.28),
                            fill=YOUTUBE_RED + (255,))
    tw, th = w * scale * 0.32, h * scale * 0.36
    cx, cy = w * scale / 2, h * scale / 2
    draw.polygon([
        (cx - tw / 2, cy - th), (cx - tw / 2, cy + th), (cx + tw * 0.9, cy),
    ], fill=(255, 255, 255, 255))
    canvas = canvas.resize((w, h), Image.LANCZOS)
    return np.array(canvas)


def render_outro_card(last_frame_bgr, fps, text, out_path, hold_duration=HOLD_DURATION):
    """Writes a silent mp4v clip: last_frame -> black -> blurred(last_frame),
    with a YouTube-style play icon + `text` sliding up and fading in on top
    of the blurred frame, holding for `hold_duration` seconds. Returns the
    clip's total duration in seconds."""
    h, w = last_frame_bgr.shape[:2]
    blurred = _heavy_blur(last_frame_bgr)

    font_size = max(24, int(h * 0.058))
    wrapped = _wrap_text(text)
    max_w = w * 0.85
    sprite = captions._render_text_sprite(wrapped, font_size, fill=(255, 255, 255, 255),
                                           stroke_fill=(10, 10, 10, 235))
    while sprite.shape[1] > max_w and font_size > 16:
        font_size = int(font_size * 0.88)
        sprite = captions._render_text_sprite(wrapped, font_size, fill=(255, 255, 255, 255),
                                               stroke_fill=(10, 10, 10, 235))

    icon = _youtube_icon_sprite(int(w * 0.16))
    ih, iw = icon.shape[:2]
    gap = int(h * 0.03)

    sh, sw = sprite.shape[:2]
    block_h = ih + gap + sh
    icon_x, icon_y = (w - iw) // 2, (h - block_h) // 2
    text_x, text_y = (w - sw) // 2, icon_y + ih + gap

    n_black = max(1, int(BLACK_DURATION * fps))
    n_blur = max(1, int(BLUR_DURATION * fps))
    n_hold = max(1, int(hold_duration * fps))
    n_entrance = max(1, int(min(TEXT_FADE_IN, hold_duration) * fps))

    black = np.zeros_like(last_frame_bgr)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (w, h))

    for i in range(n_black):
        a = _ease((i + 1) / n_black)
        writer.write(cv2.addWeighted(last_frame_bgr, 1 - a, black, a, 0))

    for i in range(n_blur):
        a = _ease((i + 1) / n_blur)
        writer.write(cv2.addWeighted(black, 1 - a, blurred, a, 0))

    for i in range(n_hold):
        frame = blurred.copy()
        progress = _ease(min(1.0, (i + 1) / n_entrance))
        rise = int((1 - progress) * h * 0.04)

        faded_icon = icon.copy()
        faded_icon[:, :, 3] = (faded_icon[:, :, 3].astype(np.float32) * progress).astype(np.uint8)
        captions._alpha_blend(frame, faded_icon, icon_x, icon_y + rise)

        faded_sprite = sprite.copy()
        faded_sprite[:, :, 3] = (faded_sprite[:, :, 3].astype(np.float32) * progress).astype(np.uint8)
        captions._alpha_blend(frame, faded_sprite, text_x, text_y + rise)

        writer.write(frame)

    writer.release()
    return BLACK_DURATION + BLUR_DURATION + hold_duration
