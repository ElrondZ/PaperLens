# -*- coding: utf-8 -*-
"""Desktop entry: start Flask backend and open a PyWebView window."""
import socket
import threading
import time
import urllib.request

import webview

import server


def find_free_port(start=7860, attempts=20):
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free local port found for PaperLens.")


def wait_until_ready(url, timeout=12):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"PaperLens backend did not become ready: {last_error}")


def run_server(port):
    server.app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


def main():
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    thread.start()
    wait_until_ready(url)
    webview.create_window(
        "PaperLens",
        url,
        width=980,
        height=860,
        min_size=(760, 620),
        background_color="#f5f5f7",
    )
    webview.start()


if __name__ == "__main__":
    main()
