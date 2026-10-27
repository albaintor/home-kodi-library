"""Web admin controls for safe catalogue SQLite cleanup and reset."""


def test_web_maintenance_controls_and_confirmation(client, admin):
    page = client.get("/static/app.js")
    assert page.status_code == 200
    source = page.text
    assert 'catalogue-db-preview' in source
    assert 'catalogue-db-clean' in source
    assert 'catalogue-db-reset' in source
    assert 'X-Confirm-Catalogue-Maintenance' in source
    assert "token!=='RÉINITIALISER'" in source
    assert "La base Kodi MyVideos" in source
    # The actual endpoints remain admin-only even if a client sends manual HTTP.
    assert client.get("/api/v1/catalogue/maintenance/preview").status_code == 401
    assert client.get("/api/v1/catalogue/maintenance/preview", headers=admin).status_code == 200
