from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from home_media_catalog.app import create_app
from home_media_catalog.settings import Settings
from home_media_catalog.store import Store

ADMIN = "unit-test-admin-token-with-at-least-24-characters"
READ = "unit-test-reader-token-with-at-least-24-characters"
INGRESS = "unit-test-ingress-token-with-at-least-24-characters"


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "catalogue.db")
    value.initialize()
    return value


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(
            data_dir=tmp_path,
            api_token=ADMIN,
            read_token=READ,
            ingress_token=INGRESS,
        )
    )
    with TestClient(app) as value:
        yield value


@pytest.fixture
def admin():
    return {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture
def clz_xml():
    return (Path(__file__).parent / "fixtures" / "clz.xml").read_bytes()
