"""API tests. A fake backend stands in for Docker so these run anywhere."""

import pytest

from lb.app import HealthMonitor, LoadBalancer, create_app


class FakeBackend:
    def __init__(self):
        self.running = set()

    def start(self, name):
        self.running.add(name)
        return f"http://{name}"

    def stop(self, name):
        self.running.discard(name)


def fake_forward(url):
    name, path = url.removeprefix("http://").split("/", 1)
    if path == "home":
        return f'{{"message": "Hello from Server: {name}"}}', 200, "application/json"
    return "not found", 404, "text/plain"


@pytest.fixture
def lb():
    balancer = LoadBalancer(FakeBackend())
    balancer.add(["S1", "S2", "S3"])
    return balancer


@pytest.fixture
def client(lb):
    return create_app(lb, forward=fake_forward).test_client()


def test_rep_lists_replicas(client):
    body = client.get("/rep").get_json()
    assert body["status"] == "successful"
    assert body["message"] == {"N": 3, "replicas": ["S1", "S2", "S3"]}


def test_add_with_names_and_random_fill(client, lb):
    body = client.post("/add", json={"n": 3, "hostnames": ["S4"]}).get_json()
    assert body["message"]["N"] == 6
    assert "S4" in body["message"]["replicas"]
    assert lb.backend.running == set(body["message"]["replicas"])


def test_add_rejects_too_many_hostnames(client):
    resp = client.post("/add", json={"n": 1, "hostnames": ["A", "B"]})
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "failure"


def test_add_rejects_existing_hostname(client):
    assert client.post("/add", json={"n": 1, "hostnames": ["S1"]}).status_code == 400


def test_add_rejects_bad_n(client):
    for bad in (0, -1, "2", True, None):
        assert client.post("/add", json={"n": bad}).status_code == 400


def test_rm_named_and_random(client, lb):
    body = client.delete("/rm", json={"n": 2, "hostnames": ["S1"]}).get_json()
    assert body["message"]["N"] == 1
    assert "S1" not in body["message"]["replicas"]
    assert len(lb.backend.running) == 1


def test_rm_rejects_unknown_and_too_many(client):
    assert client.delete("/rm", json={"n": 1, "hostnames": ["ghost"]}).status_code == 400
    assert client.delete("/rm", json={"n": 10}).status_code == 400


def test_requests_are_routed_to_a_replica(client):
    body = client.get("/home").get_json()
    assert body["message"].split(": ")[1] in {"S1", "S2", "S3"}


def test_unknown_endpoint_returns_400(client):
    resp = client.get("/other")
    assert resp.status_code == 400
    assert "does not exist" in resp.get_json()["message"]


def test_no_replicas_returns_503(client):
    client.delete("/rm", json={"n": 3})
    assert client.get("/home").status_code == 503


def test_custom_empty_ring_is_kept():
    from lb.hash_ring import ConsistentHashRing
    ring = ConsistentHashRing(mode="sha256")
    assert LoadBalancer(FakeBackend(), ring).ring is ring


def test_health_monitor_replaces_dead_replica(lb):
    dead = {"http://S2"}
    monitor = HealthMonitor(lb, max_misses=2, ping=lambda url: url not in dead)

    assert monitor.check_once() == []          # first miss, still tolerated
    assert "S2" in lb.replicas()
    assert monitor.check_once() == ["S2"]      # second miss, replaced
    assert "S2" not in lb.replicas()
    assert len(lb.replicas()) == 3             # N is kept the same


def test_health_monitor_resets_misses_after_recovery(lb):
    state = {"up": False}
    monitor = HealthMonitor(lb, max_misses=2,
                            ping=lambda url: state["up"] or url != "http://S1")
    monitor.check_once()
    state["up"] = True
    monitor.check_once()
    state["up"] = False
    assert monitor.check_once() == []          # counter restarted, not replaced
    assert "S1" in lb.replicas()
