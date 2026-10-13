# Development and testing

[← Overview](../README.md) · [Server installation](installation.md)

## Running locally

Requires Python 3.12 or later and a single server process. Sessions and import jobs are local to that process; do not configure multiple workers.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
export DATA_DIR="$PWD/data"
export API_TOKEN="$(openssl rand -hex 32)"
uvicorn kodi_library.app:create_app --factory --port 8099 --no-proxy-headers
```

```bash
pytest -q
ruff check kodi_library tests
ruff format --check kodi_library tests
```

Browser testing uses a synthetic catalog without personal data:

```bash
pip install -e '.[dev,browser]'
playwright install chromium
PYTHONPATH=. python tests/browser/check_catalogue.py
```

Tests use synthetic XML and simulated connections. No private export, password, or Kodi database is included in the repository. Real-world MariaDB, Docker host, and hass_ingress configuration must be validated on the target installation. The repository provides a standalone Docker service, not an installable Home Assistant integration or Supervisor add-on.
