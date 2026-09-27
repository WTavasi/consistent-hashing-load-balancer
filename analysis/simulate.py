"""Offline load analysis: how evenly does the ring spread requests?

This uses the hash ring directly (no servers, no network), so it runs in a
second and makes it easy to compare hash functions.

  A-1  10,000 requests with N = 3, count per server  -> bar chart
  A-2  N = 2..6, average and spread of load           -> line chart

Usage:
  python analysis/simulate.py                 # both hash modes
  python analysis/simulate.py --hash sha256
"""

import argparse
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lb.hash_ring import HASH_MODES, ConsistentHashRing  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"


def distribute(n_servers: int, n_requests: int, mode: str, seed: int = 42) -> Counter:
    ring = ConsistentHashRing(mode=mode)
    names = [f"S{i}" for i in range(1, n_servers + 1)]
    for name in names:
        ring.add_server(name)
    rng = random.Random(seed)
    counts = Counter({name: 0 for name in names})
    for _ in range(n_requests):
        counts[ring.get_server(rng.randint(100000, 999999))] += 1
    return counts


def run(mode: str, n_requests: int) -> None:
    print(f"\n=== hash mode: {mode} ===")

    a1 = distribute(3, n_requests, mode)
    print("A-1 (N=3):", dict(a1))

    a2 = {}
    for n in range(2, 7):
        counts = list(distribute(n, n_requests, mode).values())
        a2[n] = (statistics.mean(counts), statistics.pstdev(counts), max(counts))
        print(f"A-2 N={n}: mean={a2[n][0]:.0f} stdev={a2[n][1]:.0f} busiest={a2[n][2]}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed, skipping charts")
        return

    OUT.mkdir(exist_ok=True)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(list(a1), list(a1.values()), color="#4C72B0")
    ax.axhline(n_requests / 3, color="gray", linestyle="--", label="perfectly even")
    ax.set_title(f"A-1: {n_requests:,} requests over 3 servers ({mode})")
    ax.set_ylabel("requests handled")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / f"a1_{mode}.png", dpi=120)
    plt.close(fig)

    ns = list(a2)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(ns, [a2[n][0] for n in ns], marker="o", label="average load")
    ax.plot(ns, [a2[n][2] for n in ns], marker="s", label="busiest server")
    ax.set_title(f"A-2: load as N grows ({mode})")
    ax.set_xlabel("number of servers (N)")
    ax.set_ylabel("requests handled")
    ax.set_xticks(ns)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / f"a2_{mode}.png", dpi=120)
    plt.close(fig)
    print(f"charts saved to {OUT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash", choices=HASH_MODES)
    parser.add_argument("--requests", type=int, default=10000)
    args = parser.parse_args()
    for mode in ([args.hash] if args.hash else HASH_MODES):
        run(mode, args.requests)
