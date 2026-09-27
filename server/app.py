"""A tiny web server. The load balancer runs several copies of it.

Endpoints
  GET /home       says which server answered
  GET /heartbeat  empty 200 response, used for health checks
"""

import os

from flask import Flask, jsonify

SERVER_ID = os.environ.get("SERVER_ID", "unknown")
PORT = int(os.environ.get("PORT", "5000"))

app = Flask(__name__)


@app.get("/home")
def home():
    return jsonify(message=f"Hello from Server: {SERVER_ID}", status="successful"), 200


@app.get("/heartbeat")
def heartbeat():
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
