FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 library \
    && useradd --uid 1000 --gid 1000 --no-create-home library \
    && mkdir /data && chown library:library /data
COPY --chown=library:library kodi_library ./kodi_library
USER library
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "kodi_library.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
