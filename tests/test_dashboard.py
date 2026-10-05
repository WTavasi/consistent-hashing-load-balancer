"""Tests for the dashboard's JSON endpoints, using a fake backend."""

import pytest

from lb.app import HealthMonitor, LoadBalancer, create_app


class FakeBackend:
    def __init__(self):
        self.running = set()
        self.killed = set()

    def start(self, name):
        self.running.add(name)
        return f"http://{name}"

    def stop(self, name):
        self.running.discard(name)

    def kill(self, name):
        self.killed.add(name)


def fake_forward(url):
    return '{"message": "ok"}', 200, "application/json"


@pytest.fixture
def lb():
    balancer = LoadBalancer(FakeBackend())
    balancer.add(["S1", "S2", "S3"])
    return balancer


@pytest.fixture
def client(lb):
    return create_app(lb, forward=fake_forward).test_client()


def test_dashboard_page_is_served(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"<svg" in resp.data or b"ring" in resp.data


def test_state_describes_the_ring(client):
    state = client.get("/api/state").get_json()
    assert state["mode"] == "assignment"
    assert len(state["slots"]) == 512
    assert [s["name"] for s in state["servers"]] == ["S1", "S2", "S3"]
    assert all(len(s["slots"]) == 9 for s in state["servers"])
    occupied = sum(1 for owner in state["slots"] if owner)
    assert occupied == 27
    assert state["can_crash"] is True


def test_send_batch_counts_every_request(client):
    result = client.post("/api/send", json={"count": 200}).get_json()
    assert result["count"] == 200
    assert sum(result["counts"].values()) == 200
    assert result["failed"] == 0
    assert sum(n for _, n in result["landed"]) == 200
    assert result["first"]["server"] in {"S1", "S2", "S3"}


def test_send_rejects_bad_counts(client):
    for bad in (0, 5000, "10", True):
        assert client.post("/api/send", json={"count": bad}).status_code == 400


def test_crash_kills_replica_and_logs_it(client, lb):
    assert client.post("/api/crash", json={"name": "S2"}).status_code == 200
    assert lb.backend.killed == {"S2"}
    assert lb.events[0]["kind"] == "crash"
    assert client.post("/api/crash", json={"name": "ghost"}).status_code == 400


def test_switching_mode_keeps_servers(client, lb):
    before = lb.replicas()
    assert client.post("/api/mode", json={"mode": "sha256"}).status_code == 200
    state = client.get("/api/state").get_json()
    assert state["mode"] == "sha256"
    assert lb.replicas() == before
    assert sum(1 for owner in state["slots"] if owner) == 27
    assert client.post("/api/mode", json={"mode": "md5"}).status_code == 400


def test_events_record_membership_changes(lb):
    kinds = [e["kind"] for e in lb.events]
    assert kinds.count("add") == 3
    lb.remove(["S1"])
    assert lb.events[0]["text"] == "S1 left the ring"


def test_state_reports_missed_heartbeats(lb):
    monitor = HealthMonitor(lb, max_misses=3, ping=lambda url: url != "http://S3")
    client = create_app(lb, forward=fake_forward, monitor=monitor).test_client()
    monitor.check_once()
    assert client.get("/api/state").get_json()["misses"]["S3"] == 1
