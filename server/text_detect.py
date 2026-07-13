"""On-screen text detection + text-avoiding crop selection, built from
scratch with OpenCV's MSER region detector -- no OCR, no downloaded model.

Text renders as a tight row of small, similarly-sized, high-contrast
strokes. That geometric signature is what MSER blob-grouping below picks
up on; it never needs to actually read the words.
"""
import cv2
import numpy as np


def find_text_boxes(frame_bgr):
    """Return a list of (x, y, w, h) boxes likely to contain on-screen text."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    mser = cv2.MSER_create()
    mser.setMinArea(20)
    mser.setMaxArea(int(0.02 * w * h))
    regions, _ = mser.detectRegions(gray)
    if regions is None or len(regions) == 0:
        return []

    mask = np.zeros((h, w), dtype=np.uint8)
    kept = 0
    for pts in regions:
        x, y, rw, rh = cv2.boundingRect(pts)
        if rw == 0 or rh == 0:
            continue
        ar = rw / rh
        if 0.08 <= ar <= 1.6 and 6 <= rh <= h * 0.15:
            mask[y:y + rh, x:x + rw] = 255
            kept += 1
    if kept < 4:
        return []

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 9))
    merged = cv2.dilate(mask, kernel, iterations=2)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        if area < 400:
            continue
        density = mask[y:y + ch, x:x + cw].mean() / 255.0
        if 0.03 <= density <= 0.55 and cw >= ch:
            boxes.append((x, y, cw, ch))
    return boxes


def sample_text_boxes(cap, fps, start_frame, end_frame, sample_every_s=0.5):
    """Scan a frame range of an already-open cv2.VideoCapture and return the
    union of text boxes seen across the sampled frames."""
    step = max(1, int(fps * sample_every_s))
    boxes_all = []
    for f in range(start_frame, end_frame, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        boxes_all.extend(find_text_boxes(frame))
    return boxes_all


def best_crop(boxes, src_w, src_h, target_ratio, zoom_steps=(1.0, 0.88, 0.76), pos_steps=5):
    """2D search over crop windows of aspect `target_ratio` within the
    source frame -- both position AND a modest amount of extra zoom-in are
    candidates, since a full-width caption band (top or bottom) can only be
    dodged by also shrinking the crop, not just sliding it sideways.
    Returns the (x, y, w, h) that overlaps detected text the least, with a
    small bias toward less zoom when overlap is a tie. Falls back to a
    centered, full-size crop when there's no text."""
    src_ratio = src_w / src_h

    if not boxes:
        if target_ratio <= src_ratio:
            crop_h = src_h
            crop_w = min(src_w, int(round(crop_h * target_ratio)))
        else:
            crop_w = src_w
            crop_h = min(src_h, int(round(crop_w / target_ratio)))
        x = (src_w - crop_w) // 2
        y = (src_h - crop_h) // 2
        return (x, y, crop_w, crop_h)

    best = None
    frame_area = src_w * src_h
    for zoom in zoom_steps:
        if target_ratio <= src_ratio:
            crop_h = int(round(src_h * zoom))
            crop_w = int(round(crop_h * target_ratio))
            if crop_w > src_w:
                crop_w = src_w
                crop_h = int(round(crop_w / target_ratio))
        else:
            crop_w = int(round(src_w * zoom))
            crop_h = int(round(crop_w / target_ratio))
            if crop_h > src_h:
                crop_h = src_h
                crop_w = int(round(crop_h * target_ratio))
        crop_w = max(2, min(crop_w, src_w))
        crop_h = max(2, min(crop_h, src_h))

        for x in _linspace_int(0, src_w - crop_w, pos_steps):
            for y in _linspace_int(0, src_h - crop_h, pos_steps):
                overlap = _overlap_area(boxes, x, y, crop_w, crop_h)
                zoom_penalty = (1.0 - zoom) * frame_area * 0.02
                score = overlap + zoom_penalty
                if best is None or score < best[0]:
                    best = (score, x, y, crop_w, crop_h)
    _, x, y, crop_w, crop_h = best
    return (x, y, crop_w, crop_h)


def _linspace_int(a, b, n):
    if b <= a:
        return [a]
    return sorted(set(int(round(a + (b - a) * i / (n - 1))) for i in range(n)))


def _overlap_area(boxes, cx, cy, cw, ch):
    total = 0
    cx2, cy2 = cx + cw, cy + ch
    for (bx, by, bw, bh) in boxes:
        bx2, by2 = bx + bw, by + bh
        ox = max(0, min(cx2, bx2) - max(cx, bx))
        oy = max(0, min(cy2, by2) - max(cy, by))
        total += ox * oy
    return total


def text_score(boxes, frame_area):
    """0..1 fraction of frame area covered by suspected text, used to
    penalize whole segments that are text-heavy no matter how we crop."""
    if not boxes or frame_area <= 0:
        return 0.0
    covered = sum(bw * bh for (_, _, bw, bh) in boxes)
    return min(1.0, covered / frame_area)
