FROM python:3.12-slim

WORKDIR /app

# Dependencies first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spoolsync ./spoolsync

# Sync bookkeeping persists here; mount a volume so it survives restarts.
VOLUME ["/state"]
ENV STATE_PATH=/state/spoolsync.sqlite

ENTRYPOINT ["python", "-m", "spoolsync"]
