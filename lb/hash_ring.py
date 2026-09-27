"""Consistent hash ring used by the load balancer.

The ring has a fixed number of slots. Each physical server is placed on the
ring several times (virtual replicas) so that load spreads more evenly. A
request is hashed to a slot and is served by the first server found when
moving clockwise from that slot.

Because each server only "owns" the arcs of the ring just before its own
slots, adding or removing a server only moves the requests on those arcs.
Every other request keeps going to the same server.

Two hashing schemes are available:

* "assignment": the polynomial functions from the course spec
      H(i)      = i + 2i^2 + 17        (request i)
      Phi(i, j) = i + j + 2j^2 + 25    (server i, virtual replica j)
* "sha256": a general purpose hash, used to compare how evenly load spreads.
"""

import hashlib

NUM_SLOTS = 512
VIRTUAL_REPLICAS = 9  # log2(512)
HASH_MODES = ("assignment", "sha256")


def _sha_slot(text: str, num_slots: int) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest(), 16) % num_slots


def request_hash(request_id: int, num_slots: int = NUM_SLOTS, mode: str = "assignment") -> int:
    if mode == "sha256":
        return _sha_slot(f"req-{request_id}", num_slots)
    return (request_id + 2 * request_id ** 2 + 17) % num_slots


def virtual_server_hash(server_id: int, name: str, replica: int,
                        num_slots: int = NUM_SLOTS, mode: str = "assignment") -> int:
    if mode == "sha256":
        return _sha_slot(f"{name}#{replica}", num_slots)
    return (server_id + replica + 2 * replica ** 2 + 25) % num_slots


class ConsistentHashRing:
    def __init__(self, num_slots: int = NUM_SLOTS, replicas: int = VIRTUAL_REPLICAS,
                 mode: str = "assignment"):
        if mode not in HASH_MODES:
            raise ValueError(f"unknown hash mode '{mode}', pick one of {HASH_MODES}")
        self.num_slots = num_slots
        self.replicas = replicas
        self.mode = mode
        self._slots = [None] * num_slots  # slot index -> server name (or None)
        self._servers = {}                # server name -> (server_id, [slots])
        self._next_id = 1

    # ---------- public API ----------

    @property
    def servers(self) -> list:
        return list(self._servers)

    def __len__(self) -> int:
        return len(self._servers)

    def __contains__(self, name: str) -> bool:
        return name in self._servers

    def add_server(self, name: str) -> list:
        """Place `name` on the ring. Returns the slots it was given."""
        if name in self._servers:
            raise ValueError(f"server '{name}' is already on the ring")
        if self._slots.count(None) < self.replicas:
            raise RuntimeError("not enough free slots on the ring")

        server_id = self._next_id
        self._next_id += 1

        placed = []
        for j in range(self.replicas):
            start = virtual_server_hash(server_id, name, j, self.num_slots, self.mode)
            slot = self._find_free_slot(start)
            self._slots[slot] = name
            placed.append(slot)

        self._servers[name] = (server_id, placed)
        return placed

    def remove_server(self, name: str) -> None:
        if name not in self._servers:
            raise KeyError(f"server '{name}' is not on the ring")
        _, placed = self._servers.pop(name)
        for slot in placed:
            self._slots[slot] = None

    def get_server(self, request_id: int) -> str:
        """Return the server that should handle `request_id`."""
        if not self._servers:
            raise LookupError("no servers on the ring")
        start = request_hash(request_id, self.num_slots, self.mode)
        for step in range(self.num_slots):
            owner = self._slots[(start + step) % self.num_slots]
            if owner is not None:
                return owner
        raise LookupError("no servers on the ring")  # unreachable

    def slots_of(self, name: str) -> list:
        return list(self._servers[name][1])

    # ---------- internals ----------

    def _find_free_slot(self, start: int) -> int:
        """Resolve collisions with quadratic probing, then linear as a fallback.

        Quadratic probing does not always visit every slot, so the linear
        pass guarantees we find a gap whenever one exists.
        """
        for k in range(self.num_slots):
            slot = (start + k * k) % self.num_slots
            if self._slots[slot] is None:
                return slot
        for k in range(self.num_slots):
            slot = (start + k) % self.num_slots
            if self._slots[slot] is None:
                return slot
        raise RuntimeError("ring is full")
