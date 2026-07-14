"""Native desktop entry point. Runs the exact same Flask app as app.py, but
inside a real window (pywebview + the system WebView -- WKWebView on macOS)
instead of a browser tab: launch this and you get a proper app window with
its own icon and dock presence, not a localhost URL in Safari."""
import os
import socket
import sys
import threading
import time

if not getattr(sys, "frozen", False):
    # Only needed for `python server/desktop_app.py` dev mode. In a frozen
    # build this would put the bundle's Frameworks dir ahead of cv2's own
    # sys.path entry for its native-extension swap trick, breaking cv2's
    # import with a self-recursion error.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Dispatch to the transcription worker *before* importing app.py (which pulls
# in cv2/opencv). This must happen first: faster-whisper's `av` and OpenCV's
# bundled libavdevice collide at the Objective-C runtime level on macOS if
# both end up loaded in the same process (see transcribe.py / transcribe_worker.py).
# A frozen app has no separate python + script to shell out to for the worker
# -- sys.executable IS this same bundled binary -- so transcribe.py re-execs
# it with this sentinel flag instead.
if len(sys.argv) > 1 and sys.argv[1] == "--cutwave-transcribe-worker":
    import transcribe_worker
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    transcribe_worker.main()
    sys.exit(0)

import webview

import app as flask_app


def _find_free_port(preferred=3000, tries=20):
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


def _run_flask(port):
    flask_app.app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)


def main():
    port = _find_free_port()
    threading.Thread(target=_run_flask, args=(port,), daemon=True).start()

    # give Flask a brief moment to bind before pointing the window at it
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)

    webview.create_window(
        "cutwave", f"http://127.0.0.1:{port}",
        width=1320, height=880, min_size=(960, 660),
    )
    webview.start()


if __name__ == "__main__":
    main()
