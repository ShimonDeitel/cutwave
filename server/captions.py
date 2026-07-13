"""Beat-synced cinematic caption burn-in, written from scratch with PIL +
OpenCV -- no ffmpeg drawtext (this machine's ffmpeg build has no
libfreetype/fontconfig, so drawtext isn't available at all).

The caption text is rendered once into an RGBA sprite, then composited
per-frame with a small affine transform (scale pulse + shear + vertical
pop + a lagging drop-shadow layer) driven by an exponential "hit" envelope
timed to the detected beat grid -- a cheap but effective parallax/3D-ish
look without needing a full 3D renderer.
"""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _load_font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _render_text_sprite(text, font_size, fill, stroke_fill, pad=50):
    font = _load_font(font_size)
    stroke_w = max(2, font_size // 22)
    dummy = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    canvas = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=fill,
               stroke_width=stroke_w, stroke_fill=stroke_fill)
    return np.array(canvas)


def _beat_envelope(t, beat_times, decay=9.0):
    idx = np.searchsorted(beat_times, t, side="right") - 1
    if idx < 0:
        return 0.0, -1
    dt = t - beat_times[idx]
    if dt < 0:
        return 0.0, idx
    return float(np.exp(-decay * dt)), idx


def _warp_sprite(sprite, scale, shear, canvas_size):
    h, w = sprite.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    # scale + shear about the sprite's own center, output sized to canvas_size
    M = np.float32([
        [scale, shear, cx - scale * cx - shear * cy],
        [0, scale, cy - scale * cy],
    ])
    return cv2.warpAffine(
        sprite, M, canvas_size, flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
    )


def _alpha_blend(bg_bgr, overlay_rgba, x0, y0):
    h, w = overlay_rgba.shape[:2]
    H, W = bg_bgr.shape[:2]
    x1, y1 = x0 + w, y0 + h
    ox0, oy0 = max(0, -x0), max(0, -y0)
    x0c, y0c = max(0, x0), max(0, y0)
    x1c, y1c = min(W, x1), min(H, y1)
    if x1c <= x0c or y1c <= y0c:
        return
    ox1 = ox0 + (x1c - x0c)
    oy1 = oy0 + (y1c - y0c)
    region = overlay_rgba[oy0:oy1, ox0:ox1]
    alpha = region[:, :, 3:4].astype(np.float32) / 255.0
    rgb = region[:, :, [2, 1, 0]].astype(np.float32)
    bg_region = bg_bgr[y0c:y1c, x0c:x1c].astype(np.float32)
    bg_bgr[y0c:y1c, x0c:x1c] = (rgb * alpha + bg_region * (1 - alpha)).astype(np.uint8)


def burn_beat_captions(silent_video_path, out_path, text, beat_times, base_font_frac=0.072,
                        progress_cb=None):
    cap = cv2.VideoCapture(silent_video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    base_font_size = max(20, int(video_h * base_font_frac))
    max_sprite_w = video_w * 0.9 / 1.35  # leaves headroom for the beat-pulse scale-up
    while base_font_size > 14:
        probe_sprite = _render_text_sprite(text, base_font_size, fill=(255, 255, 255, 255),
                                            stroke_fill=(10, 10, 10, 235))
        if probe_sprite.shape[1] <= max_sprite_w:
            main_sprite = probe_sprite
            break
        base_font_size = int(base_font_size * 0.88)
    else:
        main_sprite = probe_sprite
    shadow_sprite = _render_text_sprite(text, base_font_size, fill=(0, 0, 0, 130),
                                         stroke_fill=(0, 0, 0, 0))
    sh, sw = main_sprite.shape[:2]
    pad_canvas = (int(sw * 1.35), int(sh * 1.35))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (video_w, video_h))
    beat_arr = np.array(beat_times) if len(beat_times) else np.array([0.0])

    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_idx / fps
        env, beat_idx = _beat_envelope(t, beat_arr)
        sign = 1 if beat_idx % 2 == 0 else -1

        main_scale = 1.0 + 0.20 * env
        main_shear = 0.10 * env * sign
        shadow_scale = 1.0 + 0.12 * env
        shadow_shear = 0.06 * env * sign

        warped_shadow = _warp_sprite(shadow_sprite, shadow_scale, shadow_shear, pad_canvas)
        warped_main = _warp_sprite(main_sprite, main_scale, main_shear, pad_canvas)

        cx = video_w // 2 - pad_canvas[0] // 2
        base_y = int(video_h * 0.80) - pad_canvas[1] // 2
        bounce = -int(0.05 * video_h * env)

        _alpha_blend(frame, warped_shadow, cx + 10, base_y + bounce + 12)
        _alpha_blend(frame, warped_main, cx, base_y + bounce)

        writer.write(frame)
        frame_idx += 1
        if progress_cb and frame_idx % 30 == 0:
            progress_cb(min(0.99, frame_idx / n_frames))

    cap.release()
    writer.release()
    return out_path
