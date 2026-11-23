"""质量规则 API：seed、运行、0 漏报、404、全失败状态、告警与权限。"""

from __future__ import annotations

from tests.quality_support import create_rule, run_quality
from tests.support import error_data, headers, ok_data


def test_quality_summary_and_rules_seed(client) -> None:
    summary = ok_data(client.get("/api/quality/summary", headers=headers("viewer")))
    assert summary["rules_total"] == 10
    assert summary["rules_enabled"] == 10

    rules = ok_data(client.get("/api/quality/rules", headers=headers("viewer")))
    assert len(rules["items"]) == 10
    assert {item["rule_type"] for item in rules["items"]} == {
        "reconciliation",
        "completeness",
        "uniqueness",
        "range",
        "custom_sql",
    }
    assert {item["target_db"] for item in rules["items"]} == {"business", "staging"}
    assert all(item["enabled"] is True for item in rules["items"])
    assert any(item["rule_type"] == "reconciliation" for item in rules["items"])


def test_quality_run_records_every_rule_and_passes(client) -> None:
    run = run_quality(client, period="2026-10")
    assert run["status"] == "succeeded"
    assert run["rules_total"] == 10
    assert run["rules_passed"] == 10
    assert run["rules_failed"] == 0
    assert run["rules_skipped"] == 0
    assert run["blocker_failed"] == 0
    assert len(run["results"]) == 10
    assert all(item["status"] == "passed" for item in run["results"])

    detail = ok_data(
        client.get(
            f"/api/quality/runs/{run['run_id']}", headers=headers("viewer")
        )
    )
    assert detail["run_id"] == run["run_id"]
    assert detail["status"] == "succeeded"
    assert len(detail["results"]) == 10

    listing = ok_data(
        client.get("/api/quality/runs", headers=headers("viewer"))
    )
    assert any(item["run_id"] == run["run_id"] for item in listing["items"])

    summary = ok_data(client.get("/api/quality/summary", headers=headers("viewer")))
    assert summary["last_run_status"] == "succeeded"
    assert summary["pass_rate_7d"] == 1.0
    assert summary["failed_results_7d"] == 0


def test_quality_single_rule_not_found(client) -> None:
    response = client.post(
        "/api/quality/rules/not_exists/run",
        json={},
        headers=headers("admin"),
    )
    assert response.status_code == 404
    assert error_data(response)["code"] == "QUALITY_RULE_NOT_FOUND"


def test_quality_disabled_single_rule_not_found(client) -> None:
    rule = create_rule(
        client,
        "t04_disabled_rule",
        expression={"sql": "SELECT 0 AS value", "operator": "==", "expected": 0},
        enabled=False,
    )
    assert rule["enabled"] is False
    response = client.post(
        "/api/quality/rules/t04_disabled_rule/run",
        json={},
        headers=headers("admin"),
    )
    assert response.status_code == 404
    assert error_data(response)["code"] == "QUALITY_RULE_NOT_FOUND"


def test_quality_all_failed_marks_run_failed_and_alert(client) -> None:
    create_rule(
        client,
        "t04_always_fail",
        expression={"sql": "SELECT 0 AS value", "operator": "==", "expected": 1},
    )
    run = run_quality(client, rule_codes=["t04_always_fail"], period="2026-10")
    assert run["status"] == "failed"
    assert run["rules_total"] == 1
    assert run["rules_failed"] == 1
    assert run["rules_passed"] == 0
    assert run["results"][0]["rule_code"] == "t04_always_fail"
    assert run["results"][0]["status"] == "failed"

    alerts = ok_data(client.get("/api/quality/alerts", headers=headers("viewer")))
    assert any(item["rule_code"] == "t04_always_fail" for item in alerts["items"])


def test_quality_viewer_cannot_write_or_run(client) -> None:
    run_response = client.post(
        "/api/quality/run", json={}, headers=headers("viewer")
    )
    assert run_response.status_code == 403
    assert error_data(run_response)["code"] == "FORBIDDEN"

    create_payload = {
        "code": "t04_viewer_rule",
        "name": "viewer 规则",
        "rule_type": "custom_sql",
        "target_db": "business",
        "expression": {"sql": "SELECT 0 AS value", "operator": "==", "expected": 0},
        "severity": "warning",
        "schedule": "manual",
        "enabled": True,
    }
    create_response = client.post(
        "/api/quality/rules", json=create_payload, headers=headers("viewer")
    )
    assert create_response.status_code == 403
    assert error_data(create_response)["code"] == "FORBIDDEN"
