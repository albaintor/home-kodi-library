FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libnfs14 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 library \
    && useradd --uid 1000 --gid 1000 --no-create-home library \
    && mkdir /data && chown library:library /data
COPY home_media_catalog ./home_media_catalog
# NAS checkouts may have mode 0600/0700, and Compose may select a different UID.
# Keep the application root-owned and readable by every runtime UID.
RUN chmod 755 /app \
    && find /app/home_media_catalog -type d -exec chmod 755 {} + \
    && find /app/home_media_catalog -type f -exec chmod 644 {} +
USER library
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "home_media_catalog.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
