# Multi-arch base image (amd64 and arm64); no platform pin.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.lock .
RUN pip install -r requirements.lock

COPY . .
# The data directory is a mount point for persistent runtime data.
RUN mkdir -p /app/data && chmod 0777 /app/data

# Non-root by default; compose may override the UID/GID to match a host directory.
RUN useradd --system --uid 10001 --no-create-home app
USER 10001

EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
