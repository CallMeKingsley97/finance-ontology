"""三角色掩码矩阵、敏感列策略与审计（含 P1-4 供应商电话回归）。"""

from __future__ import annotations

import re

import pytest

from app.services.security import mask_value
from tests.support import ask, column_index, headers, ok_data

PHONE_ANALYST = re.compile(r"^\d{3}\*{4}\d{4}$")
BANK_ANALYST = re.compile(r"^\*{4} \*{4} \d{4}$")


def test_mask_value_roles() -> None:
    assert mask_value("phone", "13812345678", "admin") == "13812345678"
    assert mask_value("phone", "13812345678", "analyst") == "138****5678"
    assert mask_value("phone", "13812345678", "viewer") == "***"
    assert mask_value("bank_account", "6222021234567890", "analyst") == "**** **** 7890"
    assert mask_value("bank_account", "6222021234567890", "viewer") == "***"
    assert mask_value("phone", None, "viewer") is None
    assert mask_value("other", "abcdef", "analyst") == "**ef"


def test_viewer_customer_detail_fully_masked(client) -> None:
    data = ask(client, "客户明细", "viewer", limit=3)
    assert data["answerable"] is True
    assert set(data["masked_columns"]) == {"phone", "bank_account"}
    phone_idx = column_index(data, "phone")
    bank_idx = column_index(data, "bank_account")
    assert data["rows"]
    assert all(row[phone_idx] == "***" for row in data["rows"])
    assert all(row[bank_idx] == "***" for row in data["rows"])


def test_analyst_customer_detail_partially_masked(client) -> None:
    data = ask(client, "客户明细", "analyst", limit=3)
    phone_idx = column_index(data, "phone")
    bank_idx = column_index(data, "bank_account")
    assert data["rows"]
    phone_values = [str(row[phone_idx]) for row in data["rows"]]
    bank_values = [str(row[bank_idx]) for row in data["rows"]]
    assert all(PHONE_ANALYST.match(value) for value in phone_values), phone_values
    assert all(BANK_ANALYST.match(value) for value in bank_values), bank_values


def test_admin_customer_detail_returns_original_values(client) -> None:
    data = ask(client, "客户明细", "admin", limit=3)
    # 审计/响应仍记录识别出的敏感列，但 admin 角色返回原值
    assert set(data["masked_columns"]) == {"phone", "bank_account"}
    phone_idx = column_index(data, "phone")
    bank_idx = column_index(data, "bank_account")
    for row in data["rows"]:
        assert re.fullmatch(r"1\d{10}", str(row[phone_idx]))
        assert re.fullmatch(r"6222\d{15}", str(row[bank_idx]))


def test_three_roles_get_different_customer_masking(client) -> None:
    viewer = ask(client, "客户明细", "viewer", limit=1)
    analyst = ask(client, "客户明细", "analyst", limit=1)
    admin = ask(client, "客户明细", "admin", limit=1)
    phone_idx = column_index(viewer, "phone")
    viewer_value = str(viewer["rows"][0][phone_idx])
    analyst_value = str(analyst["rows"][0][phone_idx])
    admin_value = str(admin["rows"][0][phone_idx])
    assert viewer_value == "***"
    assert viewer_value != analyst_value != admin_value
    assert PHONE_ANALYST.match(analyst_value)


def test_viewer_vendor_detail_phone_and_bank_masked(client) -> None:
    """P1-4 回归：供应商明细的 phone 与 bank_account 都必须对 viewer 掩码。"""
    data = ask(client, "2026年10月供应商明细", "viewer", limit=3)
    assert data["answerable"] is True
    assert set(data["masked_columns"]) == {"phone", "bank_account"}
    phone_idx = column_index(data, "phone")
    bank_idx = column_index(data, "bank_account")
    assert data["rows"]
    assert all(row[phone_idx] == "***" for row in data["rows"])
    assert all(row[bank_idx] == "***" for row in data["rows"])


def test_analyst_vendor_detail_partially_masked(client) -> None:
    data = ask(client, "2026年10月供应商明细", "analyst", limit=3)
    phone_idx = column_index(data, "phone")
    bank_idx = column_index(data, "bank_account")
    assert all(PHONE_ANALYST.match(str(row[phone_idx])) for row in data["rows"])
    assert all(BANK_ANALYST.match(str(row[bank_idx])) for row in data["rows"])


def test_admin_vendor_detail_returns_original_values(client) -> None:
    data = ask(client, "2026年10月供应商明细", "admin", limit=3)
    assert set(data["masked_columns"]) == {"phone", "bank_account"}
    phone_idx = column_index(data, "phone")
    bank_idx = column_index(data, "bank_account")
    for row in data["rows"]:
        assert re.fullmatch(r"1\d{10}", str(row[phone_idx]))
        assert re.fullmatch(r"6222\d{15}", str(row[bank_idx]))


def test_vendor_metadata_samples_masked(client) -> None:
    viewer = ok_data(
        client.get("/api/metadata/tables/dim_vendor", headers=headers("viewer"))
    )
    assert set(viewer["masked_columns"]) == {"phone", "bank_account"}
    sensitive_columns = {col["name"]: col for col in viewer["columns"] if col["is_sensitive"]}
    assert sensitive_columns["phone"]["sample_masked"] == "***"
    assert sensitive_columns["bank_account"]["sample_masked"] == "***"
    assert viewer["sample_rows"]
    for row in viewer["sample_rows"]:
        assert row["phone"] == "***"
        assert row["bank_account"] == "***"

    admin = ok_data(
        client.get("/api/metadata/tables/dim_vendor", headers=headers("admin"))
    )
    assert set(admin["masked_columns"]) == {"phone", "bank_account"}
    admin_columns = {col["name"]: col for col in admin["columns"] if col["is_sensitive"]}
    assert re.fullmatch(r"1\d{10}", str(admin_columns["phone"]["sample_masked"]))
    assert re.fullmatch(r"6222\d{15}", str(admin_columns["bank_account"]["sample_masked"]))


def test_customer_metadata_samples_masked(client) -> None:
    viewer = ok_data(
        client.get("/api/metadata/tables/dim_customer", headers=headers("viewer"))
    )
    assert set(viewer["masked_columns"]) == {"phone", "bank_account"}
    for row in viewer["sample_rows"]:
        assert row["phone"] == "***"
        assert row["bank_account"] == "***"


def test_security_policies_cover_four_sensitive_columns(client) -> None:
    data = ok_data(client.get("/api/security/policies"))
    entries = {
        (entry["table"], entry["column"]) for entry in data["sensitive_columns"]
    }
    assert entries == {
        ("dim_customer", "phone"),
        ("dim_customer", "bank_account"),
        ("dim_vendor", "bank_account"),
        ("dim_vendor", "phone"),
    }
    assert "question" in data["audit_fields"]
    assert "masked_columns" in data["audit_fields"]


def test_security_roles_return_three_roles(client) -> None:
    data = ok_data(client.get("/api/security/roles"))
    assert [role["code"] for role in data["items"]] == ["admin", "analyst", "viewer"]
    for role in data["items"]:
        assert role["permissions"]
        assert role["masking"]


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_audit_records_queries_and_masks(client, role: str) -> None:
    ask(client, "客户明细", role, limit=2)
    data = ok_data(
        client.get("/api/security/audit", params={"role": role, "limit": 10})
    )
    assert data["total"] >= 1
    item = data["items"][0]
    assert item["role"] == role
    assert item["status"] == "succeeded"
    assert set(item["masked_columns"]) == {"phone", "bank_account"}
    assert item["sql_preview"]
    assert "T" in item["created_at"] or " " in item["created_at"]


def test_audit_records_unanswered_question(client) -> None:
    data = ask(client, "今天天气怎么样", "viewer")
    assert data["answerable"] is False
    audit = ok_data(client.get("/api/security/audit", params={"limit": 10}))
    assert audit["items"][0]["status"] == "unanswered"
    assert audit["items"][0]["intent"] == "unsupported"
