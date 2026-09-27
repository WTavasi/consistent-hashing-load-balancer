import random

import pytest

from lb.hash_ring import ConsistentHashRing, request_hash


@pytest.fixture(params=["assignment", "sha256"])
def ring(request):
    r = ConsistentHashRing(mode=request.param)
    for name in ("S1", "S2", "S3"):
        r.add_server(name)
    return r


def test_each_server_gets_one_slot_per_virtual_replica(ring):
    all_slots = []
    for name in ring.servers:
        slots = ring.slots_of(name)
        assert len(slots) == ring.replicas
        all_slots += slots
    assert len(all_slots) == len(set(all_slots)), "two virtual servers share a slot"


def test_same_request_always_goes_to_same_server(ring):
    for rid in random.sample(range(100000, 999999), 200):
        assert ring.get_server(rid) == ring.get_server(rid)


def test_request_hash_stays_on_the_ring():
    for rid in range(10000):
        assert 0 <= request_hash(rid) < 512


def test_removing_a_server_only_moves_its_own_requests(ring):
    ids = random.sample(range(100000, 999999), 2000)
    before = {rid: ring.get_server(rid) for rid in ids}
    ring.remove_server("S2")
    after = {rid: ring.get_server(rid) for rid in ids}
    for rid in ids:
        if before[rid] != "S2":
            assert after[rid] == before[rid]
        assert after[rid] != "S2"


def test_removed_slots_are_freed(ring):
    ring.remove_server("S1")
    assert "S1" not in ring
    ring.add_server("S1")  # can be added again
    assert "S1" in ring


def test_duplicate_and_unknown_servers_are_rejected(ring):
    with pytest.raises(ValueError):
        ring.add_server("S1")
    with pytest.raises(KeyError):
        ring.remove_server("nope")


def test_empty_ring_raises():
    with pytest.raises(LookupError):
        ConsistentHashRing().get_server(123)


def test_ring_refuses_to_overfill():
    small = ConsistentHashRing(num_slots=16, replicas=4)
    for i in range(4):
        small.add_server(f"S{i}")
    with pytest.raises(RuntimeError):
        small.add_server("one-too-many")


def test_unknown_hash_mode():
    with pytest.raises(ValueError):
        ConsistentHashRing(mode="md5")
