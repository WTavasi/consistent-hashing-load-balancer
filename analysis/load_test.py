"""Live load test against a running load balancer.

Sends many GET /home requests in parallel and counts which replica
answered each one.

Usage:
  python analysis/load_test.py                       # 10,000 requests to localhost:5000
  python analysis/load_test.py --requests 2000 --lb http://localhost:5000
"""

import argparse
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests


def hit(session: requests.Session, url: str) -> str:
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            return f"error {resp.status_code}"
        return resp.json()["message"].split(": ", 1)[1]
    except requests.RequestException:
        return "error (no response)"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lb", default="http://localhost:5000")
    parser.add_argument("--requests", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=50)
    args = parser.parse_args()

    replicas = requests.get(f"{args.lb}/rep", timeout=5).json()["message"]
    print(f"replicas: {replicas}")

    session = requests.Session()
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = Counter(pool.map(lambda _: hit(session, f"{args.lb}/home"), range(args.requests)))
    elapsed = time.time() - started

    for name, count in sorted(results.items()):
        print(f"  {name:<16} {count:>6}  {'#' * (count * 50 // args.requests)}")
    print(f"{args.requests} requests in {elapsed:.1f}s ({args.requests / elapsed:.0f} req/s)")


if __name__ == "__main__":
    main()
