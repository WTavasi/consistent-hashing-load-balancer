"""Browser dashboard for testing and demonstrating the load balancer.

Open http://localhost:5000/dashboard while the balancer is running.

The page itself is static (lb/static/dashboard.html). It talks to these
JSON endpoints, which exist only to support it:

  GET  /api/state   ring layout, servers, missed heartbeats, recent events
  POST /api/send    route a batch of requests   body: {"count": 100}
  POST /api/crash   kill a replica to test recovery   body: {"name": "S2"}
  POST /api/mode    switch hash function   body: {"mode": "sha256"}

Adding and removing servers uses the normal /add and /rm endpoints.
"""

import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from .hash_ring import HASH_MODES

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BATCH = 2000


def make_dashboard(lb, forward, monitor=None) -> Blueprint:
    bp = Blueprint("dashboard", __name__)

    def fail(message, code=400):
        return jsonify(message=f"<Error> {message}", status="failure"), code

    @bp.get("/dashboard")
    def page():
        return send_from_directory(STATIC_DIR, "dashboard.html")

    @bp.get("/api/state")
    def state():
        with lb.lock:
            ring = lb.ring
            body = {
                "mode": ring.mode,
                "modes": list(HASH_MODES),
                "num_slots": ring.num_slots,
                "replicas_per_server": ring.replicas,
                "slots": ring.slot_owners(),
                "servers": [{"name": n, "slots": sorted(ring.slots_of(n))} for n in ring.servers],
                "events": list(lb.events),
            }
        body["misses"] = dict(monitor.misses) if monitor else {}
        body["can_crash"] = hasattr(lb.backend, "kill")
        return jsonify(body)

    @bp.post("/api/send")
    def send():
        payload = request.get_json(silent=True) or {}
        count = payload.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_BATCH:
            return fail(f"'count' must be a whole number from 1 to {MAX_BATCH}")
        if not lb.replicas():
            return fail("no server replicas are available", 503)

        def one(_):
            request_id = random.randint(100000, 999999)
            try:
                with lb.lock:
                    slot = lb.ring.slot_for(request_id)
                    name = lb.ring.get_server(request_id)
                    url = lb.urls[name]
            except (LookupError, KeyError):
                return {"request_id": request_id, "slot": None, "server": None, "ok": False}
            try:
                _, status, _ = forward(f"{url}/home")
                ok = status == 200
            except Exception:
                ok = False
            return {"request_id": request_id, "slot": slot, "server": name, "ok": ok}

        with ThreadPoolExecutor(max_workers=min(32, count)) as pool:
            results = list(pool.map(one, range(count)))

        counts = Counter(r["server"] for r in results if r["ok"])
        landed = Counter(r["slot"] for r in results if r["slot"] is not None)
        return jsonify(
            count=count,
            counts=dict(counts),
            failed=sum(not r["ok"] for r in results),
            landed=sorted(landed.items()),
            first=results[0],
        )

    @bp.post("/api/crash")
    def crash():
        name = (request.get_json(silent=True) or {}).get("name")
        if name not in lb.replicas():
            return fail(f"'{name}' is not running")
        if not hasattr(lb.backend, "kill"):
            return fail("this backend cannot simulate crashes")
        lb.backend.kill(name)
        lb.event("crash", f"{name} was crashed from the dashboard")
        return jsonify(message=f"{name} crashed", status="successful")

    @bp.post("/api/mode")
    def mode():
        new_mode = (request.get_json(silent=True) or {}).get("mode")
        if new_mode not in HASH_MODES:
            return fail(f"mode must be one of {list(HASH_MODES)}")
        lb.set_mode(new_mode)
        return jsonify(message=f"hash function is now {new_mode}", status="successful")

    return bp
