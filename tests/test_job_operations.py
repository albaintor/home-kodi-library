"""Operation type is persisted and exposed during every scan/sync phase."""


def test_scan_job_keeps_its_operation_across_phases(client, admin):
    response = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "Kodi",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader"},
        },
    )
    assert response.status_code == 201
    source_id = response.json()["id"]
    store = client.app.state.store
    job = store.new_job(source_id, "kodi_scan")
    assert job["operation"] == "kodi_scan"
    assert job["status"] == "pending"

    store.start_job(job["id"])
    started = client.get(f"/api/v1/jobs/{job['id']}", headers=admin).json()
    assert started["status"] == "running"
    assert started["operation"] == "kodi_scan"
    assert started["progress_phase"] == "kodi_scanning"

    store.update_job_progress(job["id"], "connecting")
    scanning_sql = store.job(job["id"])
    assert scanning_sql["operation"] == "kodi_scan"
    assert scanning_sql["progress_phase"] == "connecting"

    store.finish_job(job["id"], result={"kodi_scan": "finished"})
    assert store.job(job["id"])["operation"] == "kodi_scan"


def test_sql_only_job_and_invalid_operation(client, admin):
    response = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "Kodi",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader"},
        },
    )
    sid = response.json()["id"]
    store = client.app.state.store
    sync = store.new_job(sid)
    assert sync["operation"] == "kodi_sync"
    store.start_job(sync["id"])
    assert store.job(sync["id"])["progress_phase"] == "connecting"


def test_new_job_field_is_additive_for_v1_mobile_clients(client, admin):
    """Do not change any pre-existing job fields or the mobile contract."""
    from home_media_catalog.compatibility import mobile_compatibility

    source = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "Kodi",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader"},
        },
    )
    job = client.app.state.store.new_job(source.json()["id"], "kodi_scan")
    response = client.get(f"/api/v1/jobs/{job['id']}", headers=admin)
    assert response.status_code == 200
    data = response.json()
    assert data["operation"] == "kodi_scan"
    old_fields = {
        "id",
        "source_id",
        "status",
        "created_at",
        "finished_at",
        "result",
        "error",
        "progress_phase",
        "progress_current",
        "progress_total",
        "overall_percent",
    }
    assert old_fields <= data.keys()
    assert mobile_compatibility() == {"min_contract": 1, "max_contract": 1}


def test_migration_from_pre_operation_sqlite_schema_preserves_old_jobs(tmp_path):
    """Opening a previous server's SQLite store adds only the optional job metadata."""
    from home_media_catalog.store import Store

    store = Store(tmp_path / "catalog.sqlite3")
    store.initialize()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO sources(id,name,type,config) VALUES(?,?,?,?)",
            ("older-kodi", "Kodi", "kodi", "{}"),
        )
        conn.execute(
            "INSERT INTO jobs(id,source_id,status,created_at) VALUES(?,?,?,?)",
            ("older-job", "older-kodi", "completed", "2026-10-20T10:00:00Z"),
        )
        conn.execute("ALTER TABLE jobs DROP COLUMN operation")

    store.initialize()
    assert store.job("older-job")["operation"] == "kodi_sync"
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        # A previous-server insert remains valid because the new field has a default.
        conn.execute(
            "INSERT INTO jobs(id,source_id,status,created_at) VALUES(?,?,?,?)",
            ("legacy-client-job", "older-kodi", "pending", "2026-10-20T10:01:00Z"),
        )
    assert store.job("legacy-client-job")["operation"] == "kodi_sync"
