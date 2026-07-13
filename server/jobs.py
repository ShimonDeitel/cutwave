"""In-memory job store + background thread runner for the (single-user,
localhost-only) processing queue."""
import threading
import traceback
import uuid

_jobs = {}
_lock = threading.Lock()


def create_job():
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id, "status": "pending", "progress": 0.0,
            "message": "queued", "error": None, "result": None,
        }
    return job_id


def update_job(job_id, **kwargs):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def get_job(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def run_in_background(job_id, fn, *args, **kwargs):
    def _target():
        try:
            update_job(job_id, status="running")
            fn(job_id, *args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            update_job(job_id, status="error", error=str(e), message=f"failed: {e}")

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t
