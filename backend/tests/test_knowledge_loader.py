"""知识包加载与溯源（S1）+ 幂等 + 孤儿回收（CR-014）+ 空目录 422（CR-005）。

全部场景在 ``tmp_path`` 的真实知识包副本上执行，不修改仓库 ``backend/knowledge``。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.knowledge import loader as knowledge_loader
from app.services.knowledge import service as knowledge_service
from tests.knowledge_support import (
    EXPECTED_ENTRIES,
    EXPECTED_SOURCES,
    PACK_VERSION,
    insert_reconciliation_rule,
    knowledge_scalar,
    load_packs,
    remove_knowledge_db,
    update_pack,
)
from tests.support import error_data, headers, ok_data


def test_all_entries_and_sources_loaded(knowledge_env: Path) -> None:
    """S1：6 个知识包加载出 71 条条目与 8 个来源，全部带溯源字段。"""
    first = knowledge_loader.load_knowledge_packs(force=True)
    assert first["loaded"] is True
    assert first["entries"] == EXPECTED_ENTRIES
    assert first["sources"] == EXPECTED_SOURCES
    assert first["pack_version"] == PACK_VERSION

    snapshot = knowledge_service.get_snapshot()
    assert snapshot.available is True
    assert len(snapshot.entries) == EXPECTED_ENTRIES
    assert len(snapshot.sources) == EXPECTED_SOURCES
    for entry in snapshot.entries:
        assert entry["entry_id"]
        assert entry["source_id"] in snapshot.sources, entry["entry_id"]
        assert entry["version"]
        assert entry["basis"].strip()
        assert entry["effective_from"]
        assert entry["confidence"] in {"standard", "opensource", "practice"}

    counts = json.loads(json.dumps(knowledge_service.summary()))
    assert counts["available"] is True
    assert counts["entries"] == EXPECTED_ENTRIES
    assert counts["coverage"]["reconciliation_rules"] == 5
    assert counts["coverage"]["metrics_covered"] == 20


@pytest.mark.parametrize("field", ["source_id", "version", "basis"])
def test_entry_missing_required_field_is_rejected(
    knowledge_env: Path, field: str
) -> None:
    """S1：缺 source_id/version/basis 的条目导致整包拒绝，且库内无残留。"""
    update_pack(
        knowledge_env,
        "glossary.json",
        lambda data: data["entries"][0].pop(field),
    )
    with pytest.raises(knowledge_loader.PackValidationError) as excinfo:
        knowledge_loader.load_knowledge_packs(force=True)

    violations = excinfo.value.violations
    assert any(
        violation["file"] == "glossary.json"
        and violation["entry_id"] == "glossary.equity_equation"
        and violation["field"] == field
        for violation in violations
    ), violations
    # 整批拒绝：库内条目计数为 0，未写入任何半成品。
    assert knowledge_scalar("SELECT COUNT(*) FROM knowledge_entry") == 0


def test_entry_with_unregistered_source_is_rejected(knowledge_env: Path) -> None:
    """S1：引用未登记 source_id 的条目被拒绝，错误信息含来源 id。"""
    update_pack(
        knowledge_env,
        "glossary.json",
        lambda data: data["entries"][0].__setitem__("source_id", "not-registered"),
    )
    with pytest.raises(knowledge_loader.PackValidationError) as excinfo:
        knowledge_loader.load_knowledge_packs(force=True)

    violations = excinfo.value.violations
    assert any(
        violation["entry_id"] == "glossary.equity_equation"
        and violation["field"] == "source_id"
        and "not-registered" in violation["message"]
        for violation in violations
    ), violations
    assert knowledge_scalar("SELECT COUNT(*) FROM knowledge_entry") == 0


def test_load_twice_is_idempotent(knowledge_env: Path) -> None:
    """幂等：连续两次加载条目不增长，pack_stamp 未变时第二次直接跳过。"""
    first = knowledge_loader.load_knowledge_packs(force=True)
    assert first["loaded"] is True
    stamp = knowledge_scalar("SELECT value FROM knowledge_meta WHERE key = 'pack_stamp'")
    assert stamp

    second = load_packs(force=False)
    assert second["loaded"] is False
    assert second["skipped"] is True
    assert second["entries"] == EXPECTED_ENTRIES
    assert (
        knowledge_scalar("SELECT COUNT(*) FROM knowledge_entry") == EXPECTED_ENTRIES
    )
    assert (
        knowledge_scalar("SELECT value FROM knowledge_meta WHERE key = 'pack_stamp'")
        == stamp
    )


def test_orphan_entries_removed_on_reload(knowledge_env: Path) -> None:
    """CR-014 回归：从知识包撤下的条目在 reload 后被删除，removed 计数正确。"""
    knowledge_loader.load_knowledge_packs(force=True)
    assert (
        knowledge_scalar("SELECT COUNT(*) FROM knowledge_entry") == EXPECTED_ENTRIES
    )
    assert (
        knowledge_scalar(
            "SELECT COUNT(*) FROM knowledge_entry WHERE entry_id = 'glossary.budget_management'"
        )
        == 1
    )

    update_pack(
        knowledge_env,
        "glossary.json",
        lambda data: data.__setitem__(
            "entries",
            [
                entry
                for entry in data["entries"]
                if entry["id"] != "glossary.budget_management"
            ],
        ),
    )
    reloaded = knowledge_loader.load_knowledge_packs(force=True)
    assert reloaded["loaded"] is True
    assert reloaded["removed"] == 1
    assert reloaded["entries"] == EXPECTED_ENTRIES - 1
    assert (
        knowledge_scalar(
            "SELECT COUNT(*) FROM knowledge_entry WHERE entry_id = 'glossary.budget_management'"
        )
        == 0
    )
    assert (
        knowledge_scalar(
            "SELECT COUNT(*) FROM knowledge_link WHERE entry_id = 'glossary.budget_management'"
        )
        == 0
    )
    snapshot_ids = {entry["entry_id"] for entry in knowledge_service.get_snapshot().entries}
    assert "glossary.budget_management" not in snapshot_ids


def test_empty_pack_dir_reload_returns_422_and_keeps_state(
    pack_client: TestClient, knowledge_env: Path
) -> None:
    """CR-005 回归：目录置空 reload 返回 422，已有版本与条目不被清空。"""
    before = ok_data(pack_client.get("/api/knowledge/summary"))
    assert before["available"] is True
    assert before["entries"] == EXPECTED_ENTRIES
    assert before["pack_version"] == PACK_VERSION

    for pack_file in knowledge_env.glob("*.json"):
        pack_file.unlink()
    response = pack_client.post("/api/knowledge/reload", headers=headers("admin"))
    assert response.status_code == 422
    payload = error_data(response)
    assert payload["code"] == "KNOWLEDGE_VALIDATION_FAILED"
    assert any(
        violation["field"] == "entries"
        for violation in payload["details"]["violations"]
    )

    after = ok_data(pack_client.get("/api/knowledge/summary"))
    assert after["available"] is True
    assert after["entries"] == EXPECTED_ENTRIES
    assert after["pack_version"] == PACK_VERSION
    assert after["last_error"], "空目录失败原因应写入 last_load_error"


def test_unknown_entry_detail_returns_404(pack_client: TestClient) -> None:
    """未知 entry_id 返回 404 KNOWLEDGE_ENTRY_NOT_FOUND 而非 500。"""
    response = pack_client.get("/api/knowledge/entries/glossary.not_exist")
    assert response.status_code == 404
    assert error_data(response)["code"] == "KNOWLEDGE_ENTRY_NOT_FOUND"


def test_entry_detail_contains_payload_links_and_source(
    pack_client: TestClient,
) -> None:
    """条目详情包含 payload、links 与来源登记（版权信息可见）。"""
    detail = ok_data(pack_client.get("/api/knowledge/entries/glossary.account_system"))
    assert detail["entry_id"] == "glossary.account_system"
    assert detail["kind"] == "term"
    assert detail["payload"]["term_code"] == "term_account"
    assert {"target_kind": "term", "target_code": "term_account"} in detail["links"]
    assert detail["source"]["source_id"]
    assert detail["source"]["license"]

    types = {
        item["source_id"]: item["type"]
        for item in ok_data(pack_client.get("/api/knowledge/sources"))["items"]
    }
    assert types["fibo-loan"] == "opensource"
    assert types["cube"] == "opensource"
    assert types["datahub-glossary"] == "opensource"
    sources = ok_data(pack_client.get("/api/knowledge/sources"))["items"]
    for source in sources:
        if source["type"] == "opensource":
            assert source["notice"].strip(), source["source_id"]
    assert sum(item["entry_count"] for item in sources) == EXPECTED_ENTRIES


def test_store_readonly_connection_denies_non_whitelisted_tables(
    knowledge_env: Path,
) -> None:
    """store 只读连接只放行 5 张知识表，写操作被 authorizer 拒绝。"""
    from app.services.knowledge import store

    knowledge_loader.load_knowledge_packs(force=True)
    conn = store.knowledge_read_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_entry").fetchone()[0] == (
            EXPECTED_ENTRIES
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM knowledge_entry")
    finally:
        conn.close()


def test_injected_pii_reconciliation_rule_skipped_by_guard(
    pack_client: TestClient,
) -> None:
    """CR-001（P0 回归）：引用敏感列的勾稽规则被 guard_sql 拒绝，不计入结果。

    注入走知识包 reload 路径（真实 loader 校验 + 真实报告接口），
    刻意避开 REPLACE/CAST 等禁用关键字，仅用 SUM(LENGTH(bank_account))。
    """
    insert_reconciliation_rule(
        "recon.malicious_pii",
        "客户银行账号长度合计",
        "SELECT ROUND(COALESCE(SUM(LENGTH(c.bank_account)), 0), 2) AS value "
        "FROM dim_customer c WHERE :start_period = :start_period",
        "SELECT 0 AS value FROM dim_customer WHERE :start_period = :start_period",
    )
    summary = ok_data(pack_client.get("/api/knowledge/summary"))
    assert summary["entries"] == EXPECTED_ENTRIES + 1

    response = pack_client.post(
        "/api/reports/generate",
        json={
            "template_code": "income_statement",
            "period": "2026-10",
            "comparison": "none",
        },
        headers=headers("analyst"),
    )
    assert response.status_code == 200, response.text
    report = ok_data(response)
    reconciliation = report["reconciliation"]

    assert reconciliation["skipped"] == 1
    assert reconciliation["knowledge_checks"] == 5
    assert len(reconciliation["checks"]) == 10
    assert any(
        "禁止引用敏感列" in warning and "bank_account" in warning
        for warning in reconciliation["knowledge_warnings"]
    ), reconciliation["knowledge_warnings"]
    # 种子数据 SUM(LENGTH(bank_account)) == 570，任何 check 出现该值即泄漏。
    for check in reconciliation["checks"]:
        assert check["left"] != 570.0 and check["right"] != 570.0, check
    assert not any("银行账号" in check["name"] for check in reconciliation["checks"])
    assert any("知识勾稽规则" in warning for warning in report["warnings"])


def test_deleted_knowledge_db_degrades_consumers_without_500(
    pack_client: TestClient,
) -> None:
    """R3 降级：运行期删除 knowledge.db，业务接口与知识接口均不 500。"""
    ok_data(pack_client.get("/api/knowledge/summary"))
    remove_knowledge_db()

    summary = ok_data(pack_client.get("/api/knowledge/summary"))
    assert summary["available"] is False
    assert summary["entries"] == 0
    assert summary["pack_version"] == ""
    assert ok_data(pack_client.get("/api/knowledge/entries"))["total"] == 0
    assert ok_data(pack_client.get("/api/knowledge/sources"))["items"] == []

    report = ok_data(
        pack_client.post(
            "/api/reports/generate",
            json={
                "template_code": "income_statement",
                "period": "2026-10",
                "comparison": "none",
            },
            headers=headers("analyst"),
        )
    )
    assert len(report["reconciliation"]["checks"]) == 5
    assert report["reconciliation"]["knowledge_checks"] == 0
    assert report["reconciliation"]["passed"] is True
    assert report["appendix"]["knowledge_sources"] == []
    assert report["appendix"]["knowledge_version"] is None

    metric = ok_data(pack_client.get("/api/metrics/revenue_total"))
    assert metric["basis"] == ""
    assert metric["source_ref"] is None
    assert metric["knowledge_version"] is None

    answer = ok_data(
        pack_client.post(
            "/api/query/ask",
            json={"question": "2026年10月营业收入", "limit": 20},
            headers=headers("viewer"),
        )
    )
    assert answer["answerable"] is True
    assert answer["provenance"]["knowledge_entry_ids"] == []
    assert answer["provenance"]["knowledge_version"] is None
