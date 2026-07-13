"""cutwave: Flask app serving the frontend + the editing job API.
Everything runs on localhost -- no cloud calls, no accounts."""
import os
import sys

from flask import Flask, jsonify, request, send_file, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jobs
import pipeline

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
WORK_DIR = os.path.join(BASE_DIR, "work")

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
    song = request.files.get("song")
    broll_files = request.files.getlist("broll")
    aspect_ratio = request.form.get("aspect_ratio", "9:16")
    caption_text = request.form.get("caption", "").strip()

    if not song or not song.filename:
        return jsonify({"error": "a song file is required"}), 400
    if not broll_files:
        return jsonify({"error": "at least one b-roll clip is required"}), 400
    if aspect_ratio not in ASPECT_CHOICES:
        return jsonify({"error": f"unknown aspect ratio '{aspect_ratio}'"}), 400

    job_id = jobs.create_job()
    job_upload_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_upload_dir, exist_ok=True)

    song_path = os.path.join(job_upload_dir, "song_" + _safe_name(song.filename))
    song.save(song_path)

    broll_paths = []
    for i, f in enumerate(broll_files):
        p = os.path.join(job_upload_dir, f"broll_{i:02d}_" + _safe_name(f.filename))
        f.save(p)
        broll_paths.append(p)

    work_dir = os.path.join(WORK_DIR, job_id)
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}.mp4")

    jobs.run_in_background(
        job_id, pipeline.run_job,
        song_path, broll_paths, aspect_ratio, caption_text, work_dir, output_path,
    )
    return jsonify({"job_id": job_id})


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
