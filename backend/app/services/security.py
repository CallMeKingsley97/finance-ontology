"""角色、脱敏、审计与安全策略。"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.db.connection import write_connection, query_all, query_one
from app.schemas.common import ROLES

logger = logging.getLogger(__name__)

SENSITIVE_BY_TABLE: dict[tuple[str, str], tuple[str, str]] = {
    ("dim_customer", "phone"): ("PII_PHONE", "客户联系电话"),
    ("dim_customer", "bank_account"): ("PII_BANK_ACCOUNT", "客户银行账号"),
    ("dim_vendor", "bank_account"): ("PII_BANK_ACCOUNT", "供应商银行账号"),
    # 供应商联系人同样可能是个人，phone 与客户 phone 采用同一 PII 策略
    ("dim_vendor", "phone"): ("PII_PHONE", "供应商联系电话"),
}

SENSITIVE_COLUMN_NAMES = {"phone", "bank_account"}

# 外部数据源（非内置业务表）额外识别的敏感列；内置表策略保持不变。
EXTERNAL_SENSITIVE_COLUMNS = {"phone", "mobile", "bank_account", "id_card", "email"}

ROLE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "code": "admin",
        "name": "管理员",
        "description": "演示环境最高权限，可播种、生成本体、编辑与认证术语；敏感列显示原值。",
        "permissions": [
            "仪表盘/元数据/本体只读",
            "数据源测试/播种",
            "术语编辑/认证",
            "问数",
            "报告生成",
            "安全页/审计读取",
            "敏感值查看（原值）",
        ],
        "masking": "敏感列显示原值，仅用于本地演示。",
    },
    {
        "code": "analyst",
        "name": "分析师",
        "description": "可问数、生成报告；敏感列部分掩码。",
        "permissions": [
            "仪表盘/元数据/本体只读",
            "问数",
            "报告生成",
            "安全页/审计读取",
            "敏感值查看（部分掩码）",
        ],
        "masking": "手机号保留前 3 后 4 位，银行账号保留末 4 位。",
    },
    {
        "code": "viewer",
        "name": "访客",
        "description": "只读浏览与问数，不能生成报告、播种或编辑本体；敏感列完全掩码。",
        "permissions": [
            "仪表盘/元数据/本体只读",
            "问数",
            "报告模板查看",
            "安全页/审计读取",
            "敏感值查看（完全掩码）",
        ],
        "masking": "敏感列统一显示 ***。",
    },
]


def resolve_role(header: str | None) -> str:
    """缺失或非法角色头按 viewer 处理。"""
    value = (header or "").strip().lower()
    return value if value in ROLES else "viewer"


def mask_value(column: str, value: Any, role: str) -> Any:
    """按角色脱敏单个敏感列值。非敏感列应在上层跳过。"""
    if value is None:
        return None
    col = column.lower()
    if role == "admin":
        return value
    if role == "viewer":
        return "***"
    text = str(value)
    digits = re.sub(r"\D", "", text)
    if col in ("phone", "mobile"):
        if len(digits) >= 7:
            return f"{digits[:3]}****{digits[-4:]}"
        return "***"
    if col == "bank_account":
        if len(digits) >= 4:
            return f"**** **** {digits[-4:]}"
        return "***"
    if col == "id_card":
        if len(text) >= 8:
            return f"{text[:3]}********{text[-4:]}"
        return "***"
    if col == "email":
        if "@" in text:
            local, domain = text.split("@", 1)
            return f"{local[0] if local else ''}***@{domain}"
        if len(text) >= 2:
            return "**" + text[-2:]
        return "***"
    if len(text) <= 2:
        return "**"
    return "**" + text[-2:]


def mask_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    role: str,
    sensitive_columns: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """对结果行中的敏感列统一脱敏，返回 (行, 已掩码列)。

    sensitive_columns 为空时按列名兜底识别 phone/bank_account；
    元数据与客户/供应商明细可传入表级敏感列，避免误伤非敏感字段。
    """
    if sensitive_columns is None:
        sensitive = [col for col in columns if col.lower() in SENSITIVE_COLUMN_NAMES]
    else:
        wanted = {col.lower() for col in sensitive_columns}
        sensitive = [col for col in columns if col.lower() in wanted]
    if not sensitive:
        return rows, []
    sensitive_set = {col.lower() for col in sensitive}
    masked: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for key, value in row.items():
            if key.lower() in sensitive_set:
                new_row[key] = mask_value(key, value, role)
            else:
                new_row[key] = value
        masked.append(new_row)
    return masked, sensitive


def record_audit(
    *,
    question: str,
    intent: str | None,
    sql_text: str | None,
    row_count: int,
    status: str,
    role: str,
    masked_columns: list[str],
    duration_ms: int,
    error_message: str | None = None,
    metric_code: str | None = None,
    metric_version: str | None = None,
    alias_matched: str | None = None,
) -> None:
    """写查询审计；审计失败不阻断主流程。不记录敏感原值。

    旧库缺少指标审计列时回退为旧列 INSERT，保证升级期的审计可用性。
    """
    preview = (sql_text or "")[:500] if sql_text else None
    params = {
        "question": question[:500],
        "intent": intent,
        "sql_text": preview,
        "row_count": row_count,
        "status": status,
        "role": role,
        "masked_columns": json.dumps(masked_columns, ensure_ascii=False),
        "duration_ms": duration_ms,
        "error_message": error_message[:500] if error_message else None,
        "metric_code": metric_code,
        "metric_version": metric_version,
        "alias_matched": alias_matched,
    }
    try:
        with write_connection() as conn:
            try:
                conn.execute(
                    """INSERT INTO query_audit
                       (question, intent, sql_text, row_count, status, role,
                        masked_columns, duration_ms, error_message,
                        metric_code, metric_version, alias_matched)
                       VALUES (:question, :intent, :sql_text, :row_count, :status, :role,
                               :masked_columns, :duration_ms, :error_message,
                               :metric_code, :metric_version, :alias_matched)""",
                    params,
                )
            except sqlite3.OperationalError:
                conn.execute(
                    """INSERT INTO query_audit
                       (question, intent, sql_text, row_count, status, role,
                        masked_columns, duration_ms, error_message)
                       VALUES (:question, :intent, :sql_text, :row_count, :status, :role,
                               :masked_columns, :duration_ms, :error_message)""",
                    params,
                )
    except Exception:  # noqa: BLE001 - 审计失败不阻断主流程，但必须留痕
        logger.warning("query audit write failed; audit record dropped", exc_info=True)
        return


def get_roles() -> dict[str, Any]:
    return {"items": ROLE_DEFINITIONS}


def get_policies() -> dict[str, Any]:
    sensitive: list[dict[str, Any]] = []
    for (table, column), (classification, description) in SENSITIVE_BY_TABLE.items():
        sensitive.append(
            {
                "table": table,
                "column": column,
                "classification": classification,
                "description": description,
                "masking": {
                    "admin": "原值",
                    "analyst": "138****5678" if column == "phone" else "**** **** 7890",
                    "viewer": "***",
                },
            }
        )
    return {
        "sensitive_columns": sensitive,
        "audit_fields": [
            "question",
            "intent",
            "sql_preview",
            "row_count",
            "status",
            "role",
            "masked_columns",
            "duration_ms",
            "created_at",
        ],
    }


def _iso_created_at(value: str) -> str:
    """把 SQLite CURRENT_TIMESTAMP（UTC）转为带时区的 ISO 8601。"""
    try:
        parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        return parsed.astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return value


def list_audit(role: str | None, limit: int = 50) -> dict[str, Any]:
    role_filter = role.strip().lower() if role else None
    if role_filter not in ROLES:
        role_filter = None
    safe_limit = max(1, min(int(limit or 50), 200))
    rows = query_all(
        """SELECT audit_id, question, intent, role, sql_text, row_count,
                  status, masked_columns, duration_ms, created_at
           FROM query_audit
           WHERE (:role IS NULL OR role = :role)
           ORDER BY audit_id DESC
           LIMIT :limit""",
        {"role": role_filter, "limit": safe_limit},
    )
    total_row = query_one(
        "SELECT COUNT(*) AS n FROM query_audit WHERE (:role IS NULL OR role = :role)",
        {"role": role_filter},
    )
    items = []
    for row in rows:
        try:
            masked = json.loads(row["masked_columns"] or "[]")
        except json.JSONDecodeError:
            masked = []
        items.append(
            {
                "audit_id": row["audit_id"],
                "question": row["question"],
                "intent": row["intent"],
                "role": row["role"],
                "sql_preview": (row["sql_text"] or "")[:500] or None,
                "row_count": row["row_count"],
                "status": row["status"],
                "masked_columns": masked,
                "duration_ms": row["duration_ms"],
                "created_at": _iso_created_at(row["created_at"]),
            }
        )
    return {"items": items, "total": int(total_row["n"]) if total_row else 0}
