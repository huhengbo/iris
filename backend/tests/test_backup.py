from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_sync import fresh_admin


def test_backup_list_create_safe_export_and_restore(
    client: TestClient,
) -> None:
    headers = fresh_admin(client)
    assert client.get("/api/admin/backups", headers=headers).json() == []

    created = client.post("/api/admin/backups", headers=headers)
    assert created.status_code == 201, created.text
    backup = created.json()
    assert backup["name"].startswith("iris-")
    assert backup["name"].endswith(".tar.gz")
    assert backup["size"] > 0
    assert backup["schema_version"] >= 1
    assert isinstance(backup["task_id"], int)
    task = client.get(f"/api/admin/tasks/{backup['task_id']}", headers=fresh_admin(client))
    assert task.status_code == 200
    assert task.json()["kind"] == "backup_create"
    assert task.json()["status"] == "succeeded"

    listed = client.get("/api/admin/backups", headers=headers)
    assert [item["name"] for item in listed.json()] == [backup["name"]]

    client.put(
        "/api/admin/settings/plugins",
        json={"proxy": "http://user:hunter2@127.0.0.1:7890"},
        headers=headers,
    )
    exported = client.get("/api/admin/backups/config/export", headers=headers)
    assert exported.status_code == 200
    assert exported.json()["secrets_excluded"] is True
    assert "secret_key" not in exported.text
    assert "tmdb" not in exported.text.lower()
    assert "hunter2" not in exported.text
    assert "cookie" not in exported.text.lower()

    client.put(
        "/api/admin/settings/server",
        json={"name": "恢复前的名称", "public_url": None},
        headers=headers,
    )
    refused = client.post(f"/api/admin/backups/{backup['name']}/restore", headers=headers)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "restore_confirmation_required"

    restored = client.post(
        f"/api/admin/backups/{backup['name']}/restore?confirm=true", headers=headers
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["safety_backup"]["name"] != backup["name"]
    restore_task_id = restored.json()["task_id"]
    restore_task = client.get(f"/api/admin/tasks/{restore_task_id}", headers=fresh_admin(client))
    assert restore_task.status_code == 200
    assert restore_task.json()["kind"] == "backup_restore"
    assert restore_task.json()["status"] == "succeeded"
    settings = client.get("/api/admin/settings", headers=headers).json()
    assert settings["server"]["name"] != "恢复前的名称"
