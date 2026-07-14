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


def _rgba_over(base_rgba, overlay_rgba, x0, y0):
    """Paste overlay_rgba onto base_rgba (both RGBA numpy arrays, same
    channel order) using standard premultiplied-alpha 'over' compositing,
    in place, with bounds clipping."""
    h, w = overlay_rgba.shape[:2]
    H, W = base_rgba.shape[:2]
    x1, y1 = x0 + w, y0 + h
    ox0, oy0 = max(0, -x0), max(0, -y0)
    x0c, y0c = max(0, x0), max(0, y0)
    x1c, y1c = min(W, x1), min(H, y1)
    if x1c <= x0c or y1c <= y0c:
        return
    ox1 = ox0 + (x1c - x0c)
    oy1 = oy0 + (y1c - y0c)

    fg = overlay_rgba[oy0:oy1, ox0:ox1].astype(np.float32)
    bg = base_rgba[y0c:y1c, x0c:x1c].astype(np.float32)
    fg_a = fg[:, :, 3:4] / 255.0
    bg_a = bg[:, :, 3:4] / 255.0

    fg_premult = fg[:, :, :3] * fg_a
    bg_premult = bg[:, :, :3] * bg_a
    out_premult = fg_premult + bg_premult * (1 - fg_a)
    out_a = fg_a + bg_a * (1 - fg_a)
    out_rgb = np.divide(out_premult, out_a, out=np.zeros_like(out_premult), where=out_a > 1e-6)

    result = np.concatenate([out_rgb, out_a * 255.0], axis=2)
    base_rgba[y0c:y1c, x0c:x1c] = np.clip(result, 0, 255).astype(np.uint8)


def _composite_pulsed(frame, main_sprite, shadow_sprite, video_w, video_h, env, sign, y_frac=0.80):
    """Warp+blend a (main, shadow) sprite pair onto a BGR video frame with
    the shared beat-pulse scale/shear/bounce motion."""
    sh, sw = main_sprite.shape[:2]
    pad_canvas = (int(sw * 1.35), int(sh * 1.35))

    main_scale = 1.0 + 0.20 * env
    main_shear = 0.10 * env * sign
    shadow_scale = 1.0 + 0.12 * env
    shadow_shear = 0.06 * env * sign

    warped_shadow = _warp_sprite(shadow_sprite, shadow_scale, shadow_shear, pad_canvas)
    warped_main = _warp_sprite(main_sprite, main_scale, main_shear, pad_canvas)

    cx = video_w // 2 - pad_canvas[0] // 2
    base_y = int(video_h * y_frac) - pad_canvas[1] // 2
    bounce = -int(0.05 * video_h * env)

    _alpha_blend(frame, warped_shadow, cx + 10, base_y + bounce + 12)
    _alpha_blend(frame, warped_main, cx, base_y + bounce)


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
        _composite_pulsed(frame, main_sprite, shadow_sprite, video_w, video_h, env, sign)

        writer.write(frame)
        frame_idx += 1
        if progress_cb and frame_idx % 30 == 0:
            progress_cb(min(0.99, frame_idx / n_frames))

    cap.release()
    writer.release()
    return out_path


def _prepare_lyric_line(line, font_size, pad=40):
    font = _load_font(font_size)
    stroke_w = max(2, font_size // 22)
    dummy = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(dummy)

    text = line["text"]
    words = line["words"]
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    canvas_w, canvas_h = tw + pad * 2, th + pad * 2
    origin = (pad - bbox[0], pad - bbox[1])

    base = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(base).text(origin, text, font=font, fill=(235, 235, 245, 255),
                               stroke_width=stroke_w, stroke_fill=(10, 10, 10, 235))

    shadow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text(origin, text, font=font, fill=(0, 0, 0, 120))

    highlight_full = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(highlight_full).text(origin, text, font=font, fill=(255, 209, 102, 255),
                                         stroke_width=stroke_w, stroke_fill=(70, 40, 0, 235))
    highlight_arr = np.array(highlight_full)

    space_w = d.textlength(" ", font=font)
    cursor = 0.0
    margin = stroke_w + 2
    word_crops = []
    for w in words:
        ww = d.textlength(w["text"], font=font)
        x0 = origin[0] + cursor
        x1 = x0 + ww
        crop_box = (max(0, int(x0 - margin)), 0, min(canvas_w, int(x1 + margin)), canvas_h)
        word_crops.append({
            "start": w["start"], "end": w["end"],
            "crop": highlight_arr[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]].copy(),
            "paste_x": crop_box[0],
        })
        cursor += ww + space_w

    return {
        "start": line["start"], "end": line["end"],
        "base": np.array(base), "shadow": np.array(shadow),
        "words": word_crops, "width": canvas_w,
    }


def _fit_lyric_line(line, base_font_size, max_width):
    font_size = base_font_size
    prepared = _prepare_lyric_line(line, font_size)
    while prepared["width"] > max_width and font_size > 12:
        font_size = int(font_size * 0.9)
        prepared = _prepare_lyric_line(line, font_size)
    return prepared


def _active_line_index(starts, ends, t, grace=0.35):
    idx = int(np.searchsorted(starts, t, side="right")) - 1
    if idx < 0:
        return -1
    if t > ends[idx] + grace:
        return -1
    return idx


def burn_lyric_captions(silent_video_path, out_path, lyric_lines, beat_times,
                         base_font_frac=0.062, progress_cb=None):
    """Karaoke-style captions: whichever short lyric line is active plays,
    with the currently-sung word highlighted, riding the same beat-pulse
    3D motion as the single-caption mode."""
    cap = cv2.VideoCapture(silent_video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    base_font_size = max(18, int(video_h * base_font_frac))
    max_width = video_w * 0.88 / 1.35
    prepared_lines = [_fit_lyric_line(line, base_font_size, max_width) for line in lyric_lines]

    starts = np.array([p["start"] for p in prepared_lines]) if prepared_lines else np.array([])
    ends = [p["end"] for p in prepared_lines]

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

        if len(starts):
            li = _active_line_index(starts, ends, t)
            if li >= 0:
                line = prepared_lines[li]
                composite = line["base"].copy()
                word_starts = [w["start"] for w in line["words"]]
                wi = int(np.searchsorted(word_starts, t, side="right")) - 1
                if wi >= 0:
                    wc = line["words"][wi]
                    _rgba_over(composite, wc["crop"], wc["paste_x"], 0)
                _composite_pulsed(frame, composite, line["shadow"], video_w, video_h, env, sign)

        writer.write(frame)
        frame_idx += 1
        if progress_cb and frame_idx % 30 == 0:
            progress_cb(min(0.99, frame_idx / n_frames))

    cap.release()
    writer.release()
    return out_path


SUBTITLE_PALETTE = [
    (255, 209, 102),  # gold
    (108, 231, 255),  # cyan
    (255, 107, 168),  # pink
    (154, 255, 140),  # mint
    (255, 158, 100),  # orange
]


def _prepare_subtitle_line(line, font_size, color, pad=36):
    """Like _prepare_lyric_line, but single-color per line (no beat-pulse
    shadow layer needed) -- unspoken words render dim, the current word
    renders at full brightness in that line's assigned color."""
    font = _load_font(font_size)
    stroke_w = max(2, font_size // 20)
    dummy = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(dummy)

    text = line["text"]
    words = line["words"]
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    canvas_w, canvas_h = tw + pad * 2, th + pad * 2
    origin = (pad - bbox[0], pad - bbox[1])

    dim_fill = tuple(int(c * 0.55) for c in color) + (235,)
    bright_fill = color + (255,)

    base = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(base).text(origin, text, font=font, fill=dim_fill,
                               stroke_width=stroke_w, stroke_fill=(8, 8, 12, 220))

    highlight_full = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(highlight_full).text(origin, text, font=font, fill=bright_fill,
                                         stroke_width=stroke_w, stroke_fill=(8, 8, 12, 235))
    highlight_arr = np.array(highlight_full)

    space_w = d.textlength(" ", font=font)
    cursor = 0.0
    margin = stroke_w + 2
    word_crops = []
    for w in words:
        ww = d.textlength(w["text"], font=font)
        x0 = origin[0] + cursor
        x1 = x0 + ww
        crop_box = (max(0, int(x0 - margin)), 0, min(canvas_w, int(x1 + margin)), canvas_h)
        word_crops.append({
            "start": w["start"], "end": w["end"],
            "crop": highlight_arr[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]].copy(),
            "paste_x": crop_box[0],
        })
        cursor += ww + space_w

    return {
        "start": line["start"], "end": line["end"],
        "base": np.array(base), "words": word_crops, "width": canvas_w, "height": canvas_h,
    }


def _fit_subtitle_line(line, base_font_size, max_width, color):
    font_size = base_font_size
    prepared = _prepare_subtitle_line(line, font_size, color)
    while prepared["width"] > max_width and font_size > 12:
        font_size = int(font_size * 0.9)
        prepared = _prepare_subtitle_line(line, font_size, color)
    return prepared


def burn_subtitles(silent_video_path, out_path, lines, base_font_frac=0.058,
                    slide_duration=0.22, y_frac=0.80, progress_cb=None):
    """Short-form dialogue subtitles: each line cycles through a bright
    color palette (rather than one fixed color) and eases in with a
    slide-up + fade-in as it starts, instead of just appearing. No
    beat-pulse -- there's no beat grid for speech, so the motion here is a
    one-shot entrance rather than a per-beat bounce."""
    cap = cv2.VideoCapture(silent_video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    base_font_size = max(18, int(video_h * base_font_frac))
    max_width = video_w * 0.86
    prepared_lines = [
        _fit_subtitle_line(line, base_font_size, max_width, SUBTITLE_PALETTE[i % len(SUBTITLE_PALETTE)])
        for i, line in enumerate(lines)
    ]
    starts = np.array([p["start"] for p in prepared_lines]) if prepared_lines else np.array([])
    ends = [p["end"] for p in prepared_lines]

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (video_w, video_h))

    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_idx / fps

        if len(starts):
            li = _active_line_index(starts, ends, t)
            if li >= 0:
                line = prepared_lines[li]
                composite = line["base"].copy()
                word_starts = [w["start"] for w in line["words"]]
                wi = int(np.searchsorted(word_starts, t, side="right")) - 1
                if wi >= 0:
                    wc = line["words"][wi]
                    _rgba_over(composite, wc["crop"], wc["paste_x"], 0)

                since_start = t - line["start"]
                ease = min(1.0, max(0.0, since_start / slide_duration))
                ease = 1 - (1 - ease) ** 3  # ease-out cubic
                offset_y = int((1 - ease) * video_h * 0.05)

                if ease < 1.0:
                    composite = composite.copy()
                    composite[:, :, 3] = (composite[:, :, 3].astype(np.float32) * ease).astype(np.uint8)

                cw, ch = composite.shape[1], composite.shape[0]
                cx = video_w // 2 - cw // 2
                cy = int(video_h * y_frac) - ch // 2 + offset_y
                _alpha_blend(frame, composite, cx, cy)

        writer.write(frame)
        frame_idx += 1
        if progress_cb and frame_idx % 30 == 0:
            progress_cb(min(0.99, frame_idx / n_frames))

    cap.release()
    writer.release()
    return out_path
