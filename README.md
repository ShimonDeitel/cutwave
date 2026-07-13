# cutwave

Drop in a song and some b-roll clips, pick an aspect ratio, and cutwave cuts
the b-roll on the beat, crops out any on-screen text it finds, and (if you
want) burns in a cinematic caption that pulses with the music. Everything
runs locally in your browser and on your machine — nothing is uploaded
anywhere.

## Requirements

- Python 3.10+
- `ffmpeg` / `ffprobe` on your `PATH` (`brew install ffmpeg` on macOS)

## Run it

```bash
./run.sh
```

This creates a virtualenv, installs the pinned dependencies, and starts the
app at **http://localhost:3000**. On later runs it reuses the same venv.

Open that URL, drop in a song and one or more b-roll clips (any length —
10-minute clips are fine), pick an aspect ratio, optionally turn on the
cinematic caption, and hit **Generate music video**.

## How it works

Everything below is plain Python (numpy/scipy/OpenCV) and the system
`ffmpeg` binary — no cloud APIs, no downloaded ML models.

- **Beat detection** (`server/beat_detect.py`) — band-pass filters the song
  to the kick/bass range, builds an onset-strength envelope, and peak-picks
  beat times. Tempo is estimated by autocorrelating that envelope. This is a
  from-scratch implementation, not a wrapper around a beat-tracking library.
- **Cut planning** (`server/pipeline.py: compute_cut_points`) — turns the
  beat grid into a list of shot boundaries, respecting a minimum and maximum
  shot length so cuts feel intentional rather than chaotic.
- **Text detection** (`server/text_detect.py`) — uses OpenCV's MSER region
  detector to find clusters of small, glyph-shaped regions (the geometric
  signature of on-screen text/captions/watermarks), without OCR or a
  downloaded model.
- **Crop selection** — for every candidate shot, a 2D search over crop
  windows (position *and* a modest amount of extra zoom) finds the crop of
  your chosen aspect ratio that overlaps the least with detected text. A
  full-width caption band can only be dodged by also shrinking the crop, not
  just sliding it sideways, so both are searched.
- **Shot quality scoring** (`server/scene_score.py`) — frame-differencing
  and brightness/contrast checks bias selection toward shots with some
  motion and reasonable exposure, and away from frozen or blown-out frames.
- **Assembly** (`server/ffmpeg_utils.py`) — every crop, trim, concat and mux
  is a plain `ffmpeg`/`ffprobe` subprocess call.
- **Cinematic captions** (`server/captions.py`) — this machine's `ffmpeg`
  build has no `drawtext`/fontconfig support, so captions are composited
  frame-by-frame in Python instead: a PIL-rendered text sprite is
  scaled/sheared per frame from an exponential-decay "hit" envelope timed to
  the beat grid, plus a lagging drop-shadow layer for a cheap parallax/depth
  look.
- **Live 3D caption preview** (`static/js/audio3d.js`) — in the browser,
  once your video is ready, a Web Audio API analyser reads the result
  video's own audio in real time and drives CSS 3D transforms
  (`rotateX`/`rotateY`/`translateZ`) on an overlay caption — a bonus,
  purely client-side preview layered on top of the (already beat-cut and
  captioned) rendered file.

## Notes

- Aspect ratios: 9:16, 16:9, 1:1, 4:5, 4:3.
- Everything (uploads, intermediate renders, output) stays under this
  project's `uploads/`, `work/`, and `outputs/` folders on your disk.
- This is a single-user, localhost-only tool (an in-memory job queue, no
  auth) — don't expose it to the open internet as-is.
