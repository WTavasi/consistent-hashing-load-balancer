"""Ways to start and stop server replicas.

DockerBackend  runs each replica as a container (the normal setup).
LocalBackend   runs each replica as a Python process on this machine, which
               is handy for quick experiments when Docker is not available.

Both expose the same three methods, so the load balancer does not care
which one it is using.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

SERVER_APP = Path(__file__).resolve().parent.parent / "server" / "app.py"


def wait_until_up(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{url}/heartbeat", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"replica at {url} did not come up within {timeout}s")


class LocalBackend:
    def __init__(self):
        self._procs = {}
        self._urls = {}

    def start(self, name: str) -> str:
        port = _free_port()
        env = {**os.environ, "SERVER_ID": name, "PORT": str(port)}
        proc = subprocess.Popen(
            [sys.executable, str(SERVER_APP)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{port}"
        self._procs[name] = proc
        self._urls[name] = url
        wait_until_up(url)
        return url

    def stop(self, name: str) -> None:
        proc = self._procs.pop(name, None)
        self._urls.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def kill(self, name: str) -> None:
        """Simulate a crash (used by the failure demo)."""
        proc = self._procs.get(name)
        if proc:
            proc.kill()


class DockerBackend:
    def __init__(self, image: str = "lb-server", network: str = "lbnet"):
        import docker  # imported here so local mode works without the SDK

        self._docker = docker
        self.client = docker.from_env()
        self.image = image
        self.network = network

    def start(self, name: str) -> str:
        self._remove_if_exists(name)
        self.client.containers.run(
            self.image,
            name=name,
            hostname=name,
            network=self.network,
            environment={"SERVER_ID": name},
            detach=True,
        )
        url = f"http://{name}:5000"
        wait_until_up(url)
        return url

    def stop(self, name: str) -> None:
        self._remove_if_exists(name)

    def kill(self, name: str) -> None:
        try:
            self.client.containers.get(name).kill()
        except self._docker.errors.NotFound:
            pass

    def _remove_if_exists(self, name: str) -> None:
        try:
            self.client.containers.get(name).remove(force=True)
        except self._docker.errors.NotFound:
            pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
