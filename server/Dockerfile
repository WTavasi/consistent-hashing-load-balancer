FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir flask==3.1.*
COPY app.py .

ENV PORT=5000
EXPOSE 5000
CMD ["python", "app.py"]
