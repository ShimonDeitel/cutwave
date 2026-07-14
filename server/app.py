"""cutwave: Flask app serving the frontend + the editing job API.
Everything runs on localhost -- no cloud calls, no accounts."""
import os
import sys

from flask import Flask, jsonify, request, send_file, send_from_directory

if not getattr(sys, "frozen", False):
    # Only needed for `python server/app.py` dev mode, where these sibling
    # modules aren't on sys.path yet. A frozen build's own import machinery
    # already resolves them -- and inserting this directory here would put
    # the bundle's Frameworks dir ahead of cv2's own sys.path entry for its
    # native-extension swap trick, breaking cv2's import (self-recursion).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jobs
import licensing
import paths
import pipeline
import shorts

STATIC_DIR = os.path.join(paths.bundle_root(), "static")
DATA_DIR = paths.user_data_dir()
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
WORK_DIR = os.path.join(DATA_DIR, "work")

for d in (UPLOAD_DIR, OUTPUT_DIR, WORK_DIR):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # b-roll clips can run long

ASPECT_CHOICES = set(pipeline.ASPECT_RATIOS.keys())


def _safe_name(name):
    name = os.path.basename(name or "file")
    keep = [c if (c.isalnum() or c in "._-") else "_" for c in name]
    return "".join(keep)[:120] or "file"


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/aspect-ratios")
def aspect_ratios():
    return jsonify(pipeline.ASPECT_RATIOS)


@app.route("/api/generate", methods=["POST"])
def generate():
    mode = request.form.get("mode", "long")
    if mode not in ("long", "short"):
        return jsonify({"error": f"unknown mode '{mode}'"}), 400

    allowed, reason = licensing.can_generate()
    if not allowed:
        return jsonify({"error": reason, "paywall": True}), 402

    job_id = jobs.create_job()
    job_upload_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_upload_dir, exist_ok=True)
    work_dir = os.path.join(WORK_DIR, job_id)
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}.mp4")

    if mode == "short":
        video = request.files.get("video")
        if not video or not video.filename:
            return jsonify({"error": "a video file is required"}), 400
        video_path = os.path.join(job_upload_dir, "video_" + _safe_name(video.filename))
        video.save(video_path)

        try:
            duration = float(request.form.get("duration", shorts.DEFAULT_DURATION))
        except ValueError:
            duration = shorts.DEFAULT_DURATION
        add_subtitles = request.form.get("subtitles") == "1"
        add_outro = request.form.get("add_outro") == "1"
        outro_text = request.form.get("outro_text", "")
        try:
            outro_duration = float(request.form["outro_duration"]) if add_outro and "outro_duration" in request.form else None
        except ValueError:
            outro_duration = None

        jobs.run_in_background(
            job_id, shorts.run_short_job,
            video_path, work_dir, output_path,
            target_duration=duration, add_subtitles=add_subtitles,
            add_outro=add_outro, outro_text=outro_text, outro_duration=outro_duration,
        )
        licensing.record_generation()
        return jsonify({"job_id": job_id})

    song = request.files.get("song")
    broll_files = request.files.getlist("broll")
    aspect_ratio = request.form.get("aspect_ratio", "9:16")
    caption_mode = request.form.get("caption_mode", "off")
    caption_text = request.form.get("caption", "").strip()

    if not song or not song.filename:
        return jsonify({"error": "a song file is required"}), 400
    if not broll_files:
        return jsonify({"error": "at least one b-roll clip is required"}), 400
    if aspect_ratio not in ASPECT_CHOICES:
        return jsonify({"error": f"unknown aspect ratio '{aspect_ratio}'"}), 400
    if caption_mode not in ("off", "custom", "auto"):
        return jsonify({"error": f"unknown caption mode '{caption_mode}'"}), 400
    if caption_mode == "custom" and not caption_text:
        return jsonify({"error": "custom caption mode needs caption text"}), 400

    song_path = os.path.join(job_upload_dir, "song_" + _safe_name(song.filename))
    song.save(song_path)

    broll_paths = []
    for i, f in enumerate(broll_files):
        p = os.path.join(job_upload_dir, f"broll_{i:02d}_" + _safe_name(f.filename))
        f.save(p)
        broll_paths.append(p)

    jobs.run_in_background(
        job_id, pipeline.run_job,
        song_path, broll_paths, aspect_ratio, caption_mode, caption_text, work_dir, output_path,
    )
    licensing.record_generation()
    return jsonify({"job_id": job_id})


@app.route("/api/license/status")
def license_status():
    return jsonify(licensing.status())


@app.route("/api/license/activate", methods=["POST"])
def license_activate():
    key = (request.get_json(silent=True) or {}).get("license_key") or request.form.get("license_key", "")
    ok, message = licensing.activate(key)
    return jsonify({"ok": ok, "message": message, **licensing.status()})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = jobs.get_job(job_id)
    if job is None:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/api/download/<job_id>")
def download(job_id):
    job = jobs.get_job(job_id)
    if job is None or job.get("status") != "done":
        return jsonify({"error": "not ready"}), 404
    return send_file(job["result"]["video_path"], as_attachment=True,
                      download_name=f"cutwave_{job_id}.mp4")


@app.route("/api/preview/<job_id>")
def preview(job_id):
    job = jobs.get_job(job_id)
    if job is None or job.get("status") != "done":
        return jsonify({"error": "not ready"}), 404
    return send_file(job["result"]["video_path"], conditional=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print(f"cutwave running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
