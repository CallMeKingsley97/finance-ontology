"""契约回归：新增同步/目录能力不得改变既有接口响应形状。"""

from __future__ import annotations

from tests.ingest_support import CUSTOMER_ROWS, run_sync_now, upload_csv
from tests.support import ask, headers, ok_data


def test_sources_list_keeps_builtin_first_after_external_sync(client) -> None:
    source = upload_csv(client, CUSTOMER_ROWS)
    run_sync_now(client, source["id"])

    data = ok_data(client.get("/api/sources"))
    first = data["items"][0]
    assert first["id"] == "sqlite-local"
    assert first["kind"] == "sqlite"
    assert first["status"] == "ready"
    assert first["table_count"] == 16
    assert first["size_bytes"] > 0
    assert data["items"][1]["id"] == source["id"]

    legacy_metadata = ok_data(client.get("/api/sources/sqlite-local/metadata"))
    assert len(legacy_metadata["tables"]) == 10
    external_metadata = ok_data(
        client.get(f"/api/sources/{source['id']}/metadata")
    )
    assert external_metadata["source_id"] == source["id"]
    assert len(external_metadata["tables"]) == 1


def test_existing_read_endpoints_keep_ok_envelope(ontology_client) -> None:
    client = ontology_client
    assert ok_data(client.get("/api/health"))["status"] == "ok"
    assert ok_data(
        client.get("/api/dashboard/summary", headers=headers("viewer"))
    )["latest_period"] == "2026-10"
    assert len(ok_data(client.get("/api/metadata/tables"))["items"]) == 10
    assert ok_data(
        client.get("/api/ontology/terms", params={"limit": 10})
    )["items"]
    assert ok_data(client.get("/api/reports/templates"))["periods"]
    assert ok_data(client.get("/api/security/roles"))["items"]
    assert ok_data(client.get("/api/security/policies"))["sensitive_columns"]
    answer = ask(client, "2026年10月营业收入", "viewer")
    assert answer["answerable"] is True
    assert answer["row_count"] == 1
