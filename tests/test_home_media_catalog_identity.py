"""Keep user-visible naming consistent without breaking installed clients."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_renamed_package_and_entry_points():
    pyproject = (ROOT / "pyproject.toml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert (ROOT / "home_media_catalog" / "app.py").is_file()
    assert not (ROOT / "kodi_library").exists()
    assert 'name = "home-media-catalog"' in pyproject
    assert 'packages = ["home_media_catalog"]' in pyproject
    assert "home_media_catalog.app:create_app" in dockerfile


def test_web_branding_and_persistent_browser_state():
    page = (ROOT / "home_media_catalog/static/index.html").read_text()
    script = (ROOT / "home_media_catalog/static/app.js").read_text()
    assert "<title>Home Media Catalog</title>" in page
    assert "Home Media Catalog" in script
    # Previous localStorage keys are intentionally stable across upgrades.
    assert "kodi_library_preferences_v1" in script
    assert "kodi_library_view" in script


def test_api_compatibility_and_backup_format():
    app = (ROOT / "home_media_catalog/app.py").read_text()
    backup = (ROOT / "home_media_catalog/backup.py").read_text()
    assert 'title="Home Media Catalog API"' in app
    assert '"service": "home-kodi-library"' in app
    assert 'FORMAT = "home-kodi-library"' in backup


def test_compose_identifiers_can_upgrade_existing_nas():
    compose = (ROOT / "compose.yaml").read_text()
    host = (ROOT / "compose.host.yaml").read_text()
    for content in (compose, host):
        assert "home-media-catalog:local" in content
        assert "HOME_MEDIA_CATALOG_CONTAINER_NAME" in content
        assert "KODI_LIBRARY_CONTAINER_NAME" in content
        assert "./data:/data" in content
