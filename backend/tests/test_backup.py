"""Database backups: a manual backup is a readable SQLite copy, old ones are pruned."""

import os
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from tests.conftest import AUTH


def test_backup_create_list_and_prune(client: TestClient) -> None:
    service = client.app.state.backup_service
    service._keep = 2
    first = client.post("/api/system/backups", headers=AUTH)
    assert first.status_code == 200
    path = service.folder / first.json()["name"]
    with closing(sqlite3.connect(path)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"devices", "scenes", "automations", "settings"} <= tables

    for i in range(3):
        old = service.folder / f"lychnos-2000-01-0{i + 1}_000000.db"
        old.write_bytes(b"")
        os.utime(old, (946_684_800 + i, 946_684_800 + i))  # year 2000
    client.post("/api/system/backups", headers=AUTH)
    listed = client.get("/api/system/backups", headers=AUTH).json()["backups"]
    assert len(listed) == 2 and all(not b["name"].startswith("lychnos-2000") for b in listed)
