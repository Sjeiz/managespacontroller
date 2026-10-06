"""Small web page to control the spa without Home Assistant.

Runs in its own thread. It never touches the Spa: the main loop hands it a
snapshot of the state, and commands go into the same queue as MQTT commands.
The main loop answers each web command with the refusal reason (or None).
"""

import json
import logging
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webserver.html")
COMMAND_TIMEOUT_SECS = 5


class WebServer:
    def __init__(self, port, events):
        self.port = port
        self._events = events
        self._snapshot = {}
        self._lock = threading.Lock()
        self._server = None

    def start(self):
        """Start serving in a daemon thread. A failure is logged; the controller keeps running."""
        try:
            self._server = ThreadingHTTPServer(("", self.port), self._handler_class())
        except OSError as error:
            log.error("Web server not started on port %s: %s", self.port, error)
            return False
        threading.Thread(target=self._server.serve_forever, name="webserver", daemon=True).start()
        log.info("Web server listening on port %s", self.port)
        return True

    def stop(self):
        if self._server is not None:
            self._server.shutdown()

    def update(self, snapshot):
        """Called by the main loop with the current state."""
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def command(self, target, value):
        """Called by the web thread: queue the command and wait for the main loop's answer."""
        reply = queue.Queue(maxsize=1)
        self._events.put(("web_command", target, value, reply))
        try:
            return {"ok": True, "refused": reply.get(timeout=COMMAND_TIMEOUT_SECS)}
        except queue.Empty:
            return {"ok": False, "refused": "controller did not respond"}

    def _handler_class(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    try:
                        with open(PAGE_FILE, "rb") as file:
                            self._send(200, file.read(), "text/html; charset=utf-8")
                    except OSError:
                        self._send(500, b"Page not found", "text/plain")
                elif self.path == "/api/status":
                    self._send_json(200, server.snapshot())
                else:
                    self._send(404, b"Not found", "text/plain")

            def do_POST(self):
                if self.path != "/api/command":
                    self._send(404, b"Not found", "text/plain")
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    target, value = str(body["target"]), str(body["value"])
                except (ValueError, KeyError, TypeError):
                    self._send_json(400, {"ok": False, "refused": "invalid command"})
                    return
                self._send_json(200, server.command(target, value))

            def _send_json(self, code, data):
                self._send(code, json.dumps(data).encode("utf-8"), "application/json")

            def _send(self, code, body, content_type):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                # Keep the journal free of per-request lines
                pass

        return Handler
