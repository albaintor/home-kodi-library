from dataclasses import replace

import pytest
from conftest import ADMIN, READ
from fastapi.testclient import TestClient

from home_media_catalog.app import create_app
from home_media_catalog.settings import Settings


def settings(tmp_path, **kwargs):
    return Settings(
        data_dir=tmp_path,
        api_token=ADMIN,
        read_token=READ,
        local_auth_networks=["192.168.10.0/24", "fd12:3456::/64"],
        **kwargs,
    )


def test_local_access_requires_no_token_but_write_csrf(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app, client=("192.168.10.21", 1234)) as client:
        session = client.get("/api/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["access_method"] == "local" and session.json()["role"] == "admin"
        assert client.get("/api/v1/media").status_code == 200
        assert not client.cookies
        assert client.post("/api/v1/lists", json={"name": "LAN"}).status_code == 403
        headers = {"X-CSRF-Token": session.json()["csrf_token"]}
        assert (
            client.post("/api/v1/lists", headers=headers, json={"name": "LAN"}).status_code == 201
        )
        assert (
            client.get("/api/v1/media", headers={"Authorization": "Bearer bad"}).status_code == 401
        )
        assert (
            client.post(
                "/api/v1/lists",
                headers={**headers, "Authorization": f"Bearer {READ}"},
                json={"name": "Restricted"},
            ).status_code
            == 403
        )
    # The LAN principal never creates a reusable session outside the LAN.
    with TestClient(app, client=("203.0.113.7", 1234)) as client:
        assert (
            client.get("/api/v1/media", headers={"X-Forwarded-For": "192.168.10.21"}).status_code
            == 401
        )
        assert (
            client.get(
                "/api/v1/media",
                headers={"Forwarded": "for=192.168.10.21", "X-Real-IP": "192.168.10.21"},
            ).status_code
            == 401
        )
        assert (
            client.get("/api/v1/media", headers={"Authorization": f"Bearer {ADMIN}"}).status_code
            == 200
        )


@pytest.mark.parametrize("address", ["fd12:3456::5", "::ffff:192.168.10.21"])
def test_ipv6_and_mapped_local_addresses(tmp_path, address):
    with TestClient(create_app(settings(tmp_path)), client=(address, 1234)) as client:
        assert client.get("/api/v1/media").status_code == 200


@pytest.mark.parametrize(
    "forwarded,expected",
    [
        ("192.168.10.21", 200),
        ("192.168.10.21, 203.0.113.7", 401),
        ("192.168.10.21, 172.18.0.9", 200),
        ("invalid, 192.168.10.21", 401),
        ("", 401),
        (None, 401),
        ("172.18.0.8", 401),
    ],
)
def test_only_explicit_trusted_proxy_chain_is_used(tmp_path, forwarded, expected):
    app = create_app(settings(tmp_path, trusted_proxy_networks=["172.18.0.0/24"]))
    with TestClient(app, client=("172.18.0.2", 1234)) as client:
        headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
        assert client.get("/api/v1/media", headers=headers).status_code == expected


@pytest.mark.parametrize(
    "networks", [["0.0.0.0/0"], ["::/0"], ["192.168.1.7/24"], ["bad"], "192.168.1.0/24"]
)
def test_invalid_or_unrestricted_network_configuration_rejected(tmp_path, networks):
    with pytest.raises(ValueError):
        replace(settings(tmp_path), local_auth_networks=networks).validate()
