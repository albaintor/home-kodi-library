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
        json={"name": "Kodi", "type": "kodi", "config": {"host": "nas", "username": "reader"}},
    )
    sid = response.json()["id"]
    store = client.app.state.store
    sync = store.new_job(sid)
    assert sync["operation"] == "kodi_sync"
    store.start_job(sync["id"])
    assert store.job(sync["id"])["progress_phase"] == "connecting"
