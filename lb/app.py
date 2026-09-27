"""The load balancer: a Flask API in front of N server replicas.

Endpoints
  GET    /rep          list the replicas currently on the ring
  POST   /add          add replicas     body: {"n": 2, "hostnames": ["S5", "S6"]}
  DELETE /rm           remove replicas  body: {"n": 1, "hostnames": ["S2"]}
  GET    /<path>       forwarded to whichever replica the hash ring picks

A background thread pings every replica's /heartbeat. A replica that misses
too many heartbeats in a row is taken off the ring and replaced.

Run it:
  python -m lb.app --backend local     (no Docker needed)
  python -m lb.app --backend docker    (what docker-compose uses)
"""

import argparse
import atexit
import logging
import os
import random
import signal
import sys
import threading
import uuid

import requests
from flask import Flask, jsonify, request

from .hash_ring import HASH_MODES, ConsistentHashRing

log = logging.getLogger("lb")


def default_forward(url: str):
    """Send a GET to a replica and return (body, status_code, content_type)."""
    resp = requests.get(url, timeout=5)
    return resp.content, resp.status_code, resp.headers.get("Content-Type", "text/plain")


def default_ping(url: str) -> bool:
    try:
        return requests.get(f"{url}/heartbeat", timeout=2).status_code == 200
    except requests.RequestException:
        return False


class LoadBalancer:
    def __init__(self, backend, ring: ConsistentHashRing = None):
        self.backend = backend
        # `ring or ...` would be wrong here: an empty ring has len() == 0,
        # so it counts as False and would be silently replaced.
        self.ring = ring if ring is not None else ConsistentHashRing()
        self.urls = {}  # replica name -> base URL
        self.lock = threading.RLock()

    def replicas(self) -> list:
        with self.lock:
            return sorted(self.urls)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.urls)

    def new_names(self, count: int) -> list:
        names = []
        while len(names) < count:
            name = f"server-{uuid.uuid4().hex[:5]}"
            if name not in self.urls and name not in names:
                names.append(name)
        return names

    def add(self, names: list) -> None:
        for name in names:
            url = self.backend.start(name)
            with self.lock:
                try:
                    self.ring.add_server(name)
                except Exception:
                    self.backend.stop(name)
                    raise
                self.urls[name] = url
            log.info("added %s at %s", name, url)

    def remove(self, names: list) -> None:
        for name in names:
            with self.lock:
                if name in self.ring:
                    self.ring.remove_server(name)
                self.urls.pop(name, None)
            self.backend.stop(name)
            log.info("removed %s", name)

    def replace(self, name: str) -> str:
        """Swap a failed replica for a fresh one. Returns the new name."""
        self.remove([name])
        new_name = self.new_names(1)[0]
        self.add([new_name])
        log.warning("replaced failed replica %s with %s", name, new_name)
        return new_name

    def pick(self, request_id: int):
        with self.lock:
            name = self.ring.get_server(request_id)
            return name, self.urls[name]

    def shutdown(self) -> None:
        self.remove(self.replicas())


class HealthMonitor(threading.Thread):
    def __init__(self, lb: LoadBalancer, interval: float = 5.0,
                 max_misses: int = 2, ping=default_ping):
        super().__init__(daemon=True, name="health-monitor")
        self.lb = lb
        self.interval = interval
        self.max_misses = max_misses
        self.ping = ping
        self.misses = {}
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            self.check_once()

    def stop(self) -> None:
        self._stop_event.set()

    def check_once(self) -> list:
        """Ping every replica once. Returns the names that were replaced."""
        replaced = []
        for name, url in self.lb.snapshot().items():
            if self.ping(url):
                self.misses[name] = 0
                continue
            self.misses[name] = self.misses.get(name, 0) + 1
            log.warning("%s missed heartbeat (%d/%d)", name, self.misses[name], self.max_misses)
            if self.misses[name] >= self.max_misses:
                self.misses.pop(name, None)
                try:
                    self.lb.replace(name)
                    replaced.append(name)
                except Exception:
                    log.exception("could not replace %s", name)
        return replaced


def _error(message: str, code: int = 400):
    return jsonify(message=f"<Error> {message}", status="failure"), code


def create_app(lb: LoadBalancer, forward=default_forward) -> Flask:
    app = Flask(__name__)

    def rep_response():
        names = lb.replicas()
        return jsonify(message={"N": len(names), "replicas": names}, status="successful"), 200

    def parse_body():
        body = request.get_json(silent=True) or {}
        n = body.get("n")
        hostnames = body.get("hostnames", [])
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return None, None, "'n' must be a positive integer"
        if not isinstance(hostnames, list) or not all(isinstance(h, str) for h in hostnames):
            return None, None, "'hostnames' must be a list of strings"
        if len(hostnames) > n:
            return None, None, "Length of hostname list is more than newly added instances"
        if len(set(hostnames)) != len(hostnames):
            return None, None, "hostnames contain duplicates"
        return n, hostnames, None

    @app.get("/rep")
    def rep():
        return rep_response()

    @app.post("/add")
    def add():
        n, hostnames, err = parse_body()
        if err:
            return _error(err)
        taken = [h for h in hostnames if h in lb.replicas()]
        if taken:
            return _error(f"these hostnames are already in use: {taken}")
        names = hostnames + lb.new_names(n - len(hostnames))
        try:
            lb.add(names)
        except Exception as exc:  # e.g. Docker failed to start a container
            log.exception("add failed")
            return _error(f"could not add replicas: {exc}", 500)
        return rep_response()

    @app.delete("/rm")
    def rm():
        n, hostnames, err = parse_body()
        if err:
            if "more than newly added" in err:
                err = "Length of hostname list is more than removable instances"
            return _error(err)
        current = lb.replicas()
        if n > len(current):
            return _error(f"cannot remove {n} replicas, only {len(current)} running")
        unknown = [h for h in hostnames if h not in current]
        if unknown:
            return _error(f"these hostnames are not running: {unknown}")
        others = [h for h in current if h not in hostnames]
        names = hostnames + random.sample(others, n - len(hostnames))
        lb.remove(names)
        return rep_response()

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def route(path):
        if not lb.replicas():
            return _error("no server replicas are available", 503)
        request_id = random.randint(100000, 999999)
        name, url = lb.pick(request_id)
        try:
            body, status, content_type = forward(f"{url}/{path}")
        except requests.RequestException:
            return _error(f"replica {name} did not respond", 503)
        if status == 404:
            return _error(f"'/{path}' endpoint does not exist in server replicas")
        return body, status, {"Content-Type": content_type}

    return app


def build_backend(kind: str):
    if kind == "local":
        from .backends import LocalBackend
        return LocalBackend()
    from .backends import DockerBackend
    return DockerBackend(
        image=os.environ.get("SERVER_IMAGE", "lb-server"),
        network=os.environ.get("DOCKER_NETWORK", "lbnet"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Consistent hashing load balancer")
    parser.add_argument("--backend", choices=["docker", "local"],
                        default=os.environ.get("LB_BACKEND", "docker"))
    parser.add_argument("--replicas", type=int, default=int(os.environ.get("N_REPLICAS", 3)))
    parser.add_argument("--hash", choices=HASH_MODES, default=os.environ.get("HASH_MODE", "assignment"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LB_PORT", 5000)))
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between heartbeats")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    lb = LoadBalancer(build_backend(args.backend), ConsistentHashRing(mode=args.hash))
    atexit.register(lb.shutdown)
    # `docker stop` and `kill` send SIGTERM, which skips atexit by default.
    # Turning it into a normal exit makes sure replicas are cleaned up.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    lb.add([f"S{i}" for i in range(1, args.replicas + 1)])

    HealthMonitor(lb, interval=args.interval).start()
    create_app(lb).run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
