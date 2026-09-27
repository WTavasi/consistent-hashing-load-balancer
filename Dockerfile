# Image for the load balancer itself.
# It starts and stops server replicas through the host's Docker socket,
# which docker-compose.yml mounts into the container.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lb/ lb/

EXPOSE 5000
CMD ["python", "-m", "lb.app", "--backend", "docker"]
