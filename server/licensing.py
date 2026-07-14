"""Local usage gating + LemonSqueezy license validation.

Free tier: a small number of generations per day, tracked in a local JSON
state file -- a soft gate appropriate for an indie product, not DRM (a
determined user could delete the file; that's an accepted tradeoff for v1).

Paid tier: a LemonSqueezy license key unlocks unlimited use. Validity is
re-checked against LemonSqueezy's API at most once a day, so a lapsed or
cancelled subscription actually stops granting unlimited access instead of
staying unlocked forever from one activation. If the network is unreachable,
the last-known status is kept rather than locking out an offline user.

LemonSqueezy License API reference: https://docs.lemonsqueezy.com/api/license-api
"""
import datetime
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid

import paths

STATE_PATH = os.path.join(paths.user_data_dir(), "license_state.json")
FREE_GENERATIONS_PER_DAY = 1
REVALIDATE_INTERVAL_HOURS = 24
LEMONSQUEEZY_API = "https://api.lemonsqueezy.com/v1/licenses"

_DEFAULT_STATE = {
    "license_key": None,
    "instance_id": None,
    "instance_name": None,
    "license_status": None,  # "active" once activated + validated
    "last_validated_at": None,
    "free_uses_date": None,
    "free_uses_count": 0,
}


def _today():
    return datetime.date.today().isoformat()


def _load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return {**_DEFAULT_STATE, **json.load(f)}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_STATE)


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _device_instance_name():
    return f"{socket.gethostname()}-{uuid.getnode():x}"


def _api_post(endpoint, params):
    url = f"{LEMONSQUEEZY_API}/{endpoint}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = None
        return body, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)


def activate(license_key):
    """Activate a license key for this device. Returns (ok, message)."""
    license_key = (license_key or "").strip()
    if not license_key:
        return False, "Enter a license key first."

    state = _load_state()
    instance_name = _device_instance_name()
    body, err = _api_post("activate", {"license_key": license_key, "instance_name": instance_name})
    if body is None:
        return False, f"Couldn't reach the license server ({err}). Check your connection and try again."
    if not body.get("activated"):
        return False, body.get("error") or "That license key isn't valid."

    lk = body.get("license_key", {}) or {}
    state.update({
        "license_key": license_key,
        "instance_id": (body.get("instance") or {}).get("id"),
        "instance_name": instance_name,
        "license_status": lk.get("status"),
        "last_validated_at": datetime.datetime.utcnow().isoformat(),
    })
    _save_state(state)
    ok = lk.get("status") == "active"
    return ok, ("Unlimited generations unlocked." if ok else f"License key status: {lk.get('status')}")


def _revalidate_if_stale(state):
    if not state.get("license_key"):
        return state
    last = state.get("last_validated_at")
    stale = True
    if last:
        try:
            age = datetime.datetime.utcnow() - datetime.datetime.fromisoformat(last)
            stale = age.total_seconds() > REVALIDATE_INTERVAL_HOURS * 3600
        except ValueError:
            stale = True
    if not stale:
        return state

    params = {"license_key": state["license_key"]}
    if state.get("instance_id"):
        params["instance_id"] = state["instance_id"]
    body, _err = _api_post("validate", params)
    if body is not None:
        lk = body.get("license_key", {}) or {}
        state["license_status"] = lk.get("status") if body.get("valid") else "invalid"
        state["last_validated_at"] = datetime.datetime.utcnow().isoformat()
        _save_state(state)
    # if the network call failed, keep the last-known status rather than
    # locking someone out just because they're offline right now
    return state


def status():
    """Everything the UI needs: licensed?, how many free generations are
    left today (None once licensed, since it's unlimited)."""
    state = _revalidate_if_stale(_load_state())
    licensed = state.get("license_status") == "active"
    used_today = state.get("free_uses_count", 0) if state.get("free_uses_date") == _today() else 0
    return {
        "licensed": licensed,
        "free_remaining_today": None if licensed else max(0, FREE_GENERATIONS_PER_DAY - used_today),
    }


def can_generate():
    """(allowed: bool, reason: str | None)."""
    st = status()
    if st["licensed"] or st["free_remaining_today"] > 0:
        return True, None
    return False, "You've used today's free video. Unlock unlimited for $5/mo, or come back tomorrow."


def record_generation():
    """Call once a job is actually queued, to consume a free use. No-op
    once licensed."""
    state = _load_state()
    if state.get("license_status") == "active":
        return
    today = _today()
    if state.get("free_uses_date") != today:
        state["free_uses_date"] = today
        state["free_uses_count"] = 0
    state["free_uses_count"] += 1
    _save_state(state)
