"""指标注册表服务：定义、版本、认证、测试用例、别名与公式执行。"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app.db.connection import BUSINESS_TABLES
from app.schemas.common import AppError
from app.services.ingest.store import (
    ensure_staging_available,
    staging_connection,
    staging_now,
)
from app.services import metric_seed

METRIC_STATUSES = ("draft", "pending", "certified", "deprecated")
VERSION_STATUSES = ("draft", "pending", "certified", "deprecated")
ALIAS_TYPES = ("synonym", "business", "exclusion")
SENSITIVITY_LEVELS = ("public", "internal", "confidential", "restricted")
EDITABLE_FIELDS = (
    "name",
    "definition",
    "formula_text",
    "formula_sql",
    "boundary",
    "owner",
    "business_domain",
    "unit",
    "sensitivity_level",
    "term_code",
)
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

_DEFINITION_COLUMNS = """code, name, definition, formula_text, formula_sql,
    boundary_json, owner, business_domain, unit, sensitivity_level, status,
    current_version, term_code, sort_order, created_at, updated_at, updated_by"""

_VERSION_COLUMNS = """id, metric_code, version, status, snapshot_json, change_note,
    effective_period, certified_by, certified_at, created_by, created_at, updated_at"""

_TEST_CASE_COLUMNS = """id, metric_code, version_id, name, period_code, expected_value,
    tolerance_abs, tolerance_rel, last_status, last_actual, last_diff, last_run_at,
    last_message, created_at, updated_at"""

_ALIAS_COLUMNS = """id, metric_code, alias, normalized_alias, alias_type, priority,
    status, created_by, created_at, updated_at"""


def _now() -> str:
    return staging_now()


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _load_json(text: str | None, default: Any) -> Any:
    try:
        value = json.loads(text or "")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _boundary(row: dict[str, Any] | sqlite3.Row) -> dict[str, str]:
    raw = _load_json(row["boundary_json"], {})
    return {str(key): str(value) for key, value in raw.items()}


def _knowledge_basis_fields(code: str) -> dict[str, Any]:
    """指标口径的知识依据字段；知识不可用时返回空串/None 兜底。"""
    try:
        from app.services.knowledge import service as knowledge_service

        info = knowledge_service.metric_basis(code)
        if not isinstance(info, dict):
            info = dict(info) if info else {}
        return {
            "basis": info.get("basis") or "",
            "source_ref": info.get("source_ref"),
            "source_name": info.get("source_name"),
            "source_url": info.get("source_url"),
            "knowledge_version": info.get("version") or None,
        }
    except Exception:  # noqa: BLE001 - 知识不可用不得影响指标读取
        return {
            "basis": "",
            "source_ref": None,
            "source_name": None,
            "source_url": None,
            "knowledge_version": None,
        }


def _definition(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "code": row["code"],
        "name": row["name"],
        "definition": row["definition"],
        "formula_text": row["formula_text"],
        "formula_sql": row["formula_sql"],
        "boundary": _boundary(row),
        "owner": row["owner"],
        "business_domain": row["business_domain"],
        "unit": row["unit"],
        "sensitivity_level": row["sensitivity_level"],
        "status": row["status"],
        "current_version": row["current_version"],
        "term_code": row["term_code"],
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
        **_knowledge_basis_fields(row["code"]),
    }


def _version(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "metric_code": row["metric_code"],
        "version": row["version"],
        "status": row["status"],
        "snapshot": _load_json(row["snapshot_json"], {}),
        "change_note": row["change_note"],
        "effective_period": row["effective_period"],
        "certified_by": row["certified_by"],
        "certified_at": row["certified_at"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _test_case(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "metric_code": row["metric_code"],
        "version_id": row["version_id"],
        "name": row["name"],
        "period_code": row["period_code"],
        "expected_value": row["expected_value"],
        "tolerance_abs": row["tolerance_abs"],
        "tolerance_rel": row["tolerance_rel"],
        "last_status": row["last_status"],
        "last_actual": row["last_actual"],
        "last_diff": row["last_diff"],
        "last_run_at": row["last_run_at"],
        "last_message": row["last_message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _alias(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "metric_code": row["metric_code"],
        "alias": row["alias"],
        "normalized_alias": row["normalized_alias"],
        "alias_type": row["alias_type"],
        "priority": row["priority"],
        "status": row["status"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _test_summary(test_cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(test_cases)
    passed = sum(1 for item in test_cases if item["last_status"] == "passed")
    failed = sum(1 for item in test_cases if item["last_status"] == "failed")
    errors = sum(1 for item in test_cases if item["last_status"] == "error")
    not_run = sum(1 for item in test_cases if item["last_status"] == "not_run")
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "error": errors,
        "not_run": not_run,
        "pass_rate": round(passed / total, 4) if total else None,
    }


def _snapshot_of(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item["name"],
        "definition": item["definition"],
        "formula_text": item["formula_text"],
        "formula_sql": item["formula_sql"],
        "boundary": item["boundary"],
        "owner": item["owner"],
        "business_domain": item["business_domain"],
        "unit": item["unit"],
        "sensitivity_level": item["sensitivity_level"],
        "term_code": item["term_code"],
    }


def _row_by_code(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_DEFINITION_COLUMNS} FROM metric_definition WHERE code = :code",
        {"code": code},
    ).fetchone()


def _require_definition(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    row = _row_by_code(conn, code)
    if row is None:
        raise AppError(404, "METRIC_NOT_FOUND", f"指标不存在: {code}")
    return row


def _require_version(conn: sqlite3.Connection, code: str, version: str) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT {_VERSION_COLUMNS} FROM metric_version "
        "WHERE metric_code = :code AND version = :version",
        {"code": code, "version": version},
    ).fetchone()
    if row is None:
        raise AppError(404, "METRIC_VERSION_NOT_FOUND", f"指标版本不存在: {code} {version}")
    return row


def _parse_version(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"v(\d+)\.(\d+)", value or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _next_version(conn: sqlite3.Connection, code: str) -> str:
    rows = conn.execute(
        "SELECT version FROM metric_version WHERE metric_code = :code ORDER BY id DESC",
        {"code": code},
    ).fetchall()
    parsed = [_parse_version(row["version"]) for row in rows]
    parsed = [item for item in parsed if item is not None]
    if not parsed:
        return "v0.1"
    major, minor = max(parsed)
    if major == 0:
        return f"v0.{minor + 1}"
    return f"v{major}.{minor + 1}"


def _latest_period() -> str | None:
    from app.db.connection import query_one

    try:
        row = query_one(
            "SELECT period_code FROM dim_period ORDER BY period_code DESC LIMIT 1",
            enforce_authorizer=False,
        )
    except sqlite3.Error:
        return None
    return row["period_code"] if row else None


def _validate_formula_sql(sql: str) -> None:
    if not sql or not str(sql).strip():
        raise AppError(422, "METRIC_FORMULA_INVALID", "公式 SQL 不能为空")
    if len(sql) > 4000:
        raise AppError(422, "METRIC_FORMULA_INVALID", "公式 SQL 长度不能超过 4000 字符")
    from app.services import query

    guarded = query.guard_sql(sql, allow_sensitive=False, allowed_tables=frozenset(BUSINESS_TABLES))
    if not guarded.ok:
        raise AppError(422, "METRIC_FORMULA_INVALID", f"公式未通过只读守卫：{guarded.reason}")
    period = _latest_period()
    if period is None:
        return
    params = {"start_period": period, "end_period": period, "period_id": period}
    try:
        result = query.execute_query(guarded.sql, params)
    except AppError as exc:
        if exc.code == "DB_UNAVAILABLE":
            return
        raise AppError(422, "METRIC_FORMULA_INVALID", f"公式执行失败：{exc.message}", exc.details) from exc
    rows = result.get("rows") or []
    if len(rows) != 1 or "value" not in rows[0]:
        raise AppError(422, "METRIC_FORMULA_INVALID", "公式必须返回单行单列 value")


def _validate_payload(payload: dict[str, Any], *, creating: bool = False) -> dict[str, Any]:
    allowed = set(EDITABLE_FIELDS)
    if creating:
        allowed.add("code")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AppError(422, "VALIDATION_FAILED", f"不支持的字段: {', '.join(unknown)}")

    cleaned: dict[str, Any] = {}
    if creating:
        code = str(payload.get("code") or "").strip()
        if not _CODE_PATTERN.match(code):
            raise AppError(422, "VALIDATION_FAILED", "code 必须为小写字母开头的字母/数字/下划线，长度 2-64")
        cleaned["code"] = code
        if not str(payload.get("name") or "").strip():
            raise AppError(422, "VALIDATION_FAILED", "name 不能为空")
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name or len(name) > 100:
            raise AppError(422, "VALIDATION_FAILED", "name 长度必须为 1-100")
        cleaned["name"] = name
    if "definition" in payload:
        definition = str(payload["definition"] or "")
        if len(definition) > 2000:
            raise AppError(422, "VALIDATION_FAILED", "definition 长度不能超过 2000")
        cleaned["definition"] = definition
    if "formula_text" in payload:
        formula_text = str(payload["formula_text"] or "")
        if len(formula_text) > 1000:
            raise AppError(422, "VALIDATION_FAILED", "formula_text 长度不能超过 1000")
        cleaned["formula_text"] = formula_text
    if "formula_sql" in payload:
        formula_sql = str(payload["formula_sql"] or "")
        if formula_sql.strip():
            _validate_formula_sql(formula_sql)
        cleaned["formula_sql"] = formula_sql
    if "boundary" in payload:
        boundary = payload["boundary"]
        if not isinstance(boundary, dict):
            raise AppError(422, "VALIDATION_FAILED", "boundary 必须为对象")
        if len(boundary) > 20:
            raise AppError(422, "VALIDATION_FAILED", "boundary 字段过多")
        cleaned["boundary"] = {str(key): str(value) for key, value in boundary.items()}
    for field, max_length in (("owner", 100), ("business_domain", 50), ("unit", 16)):
        if field in payload:
            value = str(payload[field] or "")
            if len(value) > max_length:
                raise AppError(422, "VALIDATION_FAILED", f"{field} 长度不能超过 {max_length}")
            cleaned[field] = value
    if "sensitivity_level" in payload:
        level = str(payload["sensitivity_level"] or "")
        if level not in SENSITIVITY_LEVELS:
            raise AppError(422, "VALIDATION_FAILED", "sensitivity_level 非法")
        cleaned["sensitivity_level"] = level
    if "term_code" in payload:
        term_code = payload["term_code"]
        if term_code is not None and len(str(term_code)) > 64:
            raise AppError(422, "VALIDATION_FAILED", "term_code 长度不能超过 64")
        cleaned["term_code"] = str(term_code) if term_code else None
    return cleaned


def ensure_seeded() -> dict[str, Any]:
    """确保指标目录已播种。"""
    return metric_seed.ensure_metric_catalog()


def summary() -> dict[str, Any]:
    ensure_staging_available()
    with staging_connection() as conn:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM metric_definition GROUP BY status"
        ).fetchall()
        counts = {row["status"]: int(row["n"]) for row in status_rows}
        test_row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN last_status = 'passed' THEN 1 ELSE 0 END) AS passed
               FROM metric_test_case"""
        ).fetchone()
    total = sum(counts.values())
    tests_total = int(test_row["total"] or 0) if test_row else 0
    tests_passed = int(test_row["passed"] or 0) if test_row else 0
    return {
        "total": total,
        "certified": counts.get("certified", 0),
        "draft": counts.get("draft", 0),
        "pending": counts.get("pending", 0),
        "deprecated": counts.get("deprecated", 0),
        "tests_total": tests_total,
        "tests_passed": tests_passed,
        "test_pass_rate": round(tests_passed / tests_total, 4) if tests_total else None,
    }


def list_metrics(filters: dict[str, Any]) -> dict[str, Any]:
    ensure_staging_available()
    where: list[str] = []
    params: dict[str, Any] = {}
    for field in ("status", "business_domain", "owner"):
        value = filters.get(field)
        if value:
            where.append(f"d.{field} = :{field}")
            params[field] = str(value)
    q = str(filters.get("q") or "").strip()
    if q:
        where.append(
            "instr(lower(d.code || ' ' || d.name || ' ' || d.definition), lower(:q)) > 0"
        )
        params["q"] = q
    page = max(1, int(filters.get("page") or 1))
    page_size = max(1, min(int(filters.get("page_size") or 20), 100))
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with staging_connection() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM metric_definition d {clause}", params
        ).fetchone()
        rows = conn.execute(
            f"""SELECT {', '.join(f'd.{item.strip()}' for item in _DEFINITION_COLUMNS.split(','))},
                       COALESCE(t.test_total, 0) AS test_total,
                       COALESCE(t.test_passed, 0) AS test_passed
                FROM metric_definition d
                LEFT JOIN (
                    SELECT metric_code, COUNT(*) AS test_total,
                           SUM(CASE WHEN last_status = 'passed' THEN 1 ELSE 0 END) AS test_passed
                    FROM metric_test_case GROUP BY metric_code
                ) t ON t.metric_code = d.code
                {clause}
                ORDER BY d.sort_order, d.code
                LIMIT :limit OFFSET :offset""",
            params,
        ).fetchall()
    items = []
    for row in rows:
        item = _definition(row)
        total = int(row["test_total"] or 0)
        passed = int(row["test_passed"] or 0)
        item["test_total"] = total
        item["test_passed"] = passed
        item["test_pass_rate"] = round(passed / total, 4) if total else None
        items.append(item)
    return {
        "items": items,
        "total": int(total_row["n"]) if total_row else 0,
        "page": page,
        "page_size": page_size,
    }


def get_metric(code: str) -> dict[str, Any]:
    ensure_staging_available()
    with staging_connection() as conn:
        definition = _definition(_require_definition(conn, code))
        versions = [
            _version(row)
            for row in conn.execute(
                f"SELECT {_VERSION_COLUMNS} FROM metric_version "
                "WHERE metric_code = :code ORDER BY id DESC",
                {"code": code},
            ).fetchall()
        ]
        test_cases = [
            _test_case(row)
            for row in conn.execute(
                f"SELECT {_TEST_CASE_COLUMNS} FROM metric_test_case "
                "WHERE metric_code = :code ORDER BY id",
                {"code": code},
            ).fetchall()
        ]
        aliases = [
            _alias(row)
            for row in conn.execute(
                f"SELECT {_ALIAS_COLUMNS} FROM metric_alias "
                "WHERE metric_code = :code ORDER BY alias_type, priority DESC, id",
                {"code": code},
            ).fetchall()
        ]
    current_certified = next(
        (item for item in versions if item["status"] == "certified"), None
    )
    draft_version = next(
        (item for item in versions if item["status"] in ("draft", "pending")), None
    )
    detail = dict(definition)
    detail.update(
        {
            "versions": versions,
            "test_cases": test_cases,
            "aliases": aliases,
            "test_summary": _test_summary(test_cases),
            "current_certified": current_certified,
            "draft_version": draft_version,
        }
    )
    return detail


def create_metric(payload: dict[str, Any], role: str) -> dict[str, Any]:
    cleaned = _validate_payload(payload, creating=True)
    for field, default in (
        ("definition", ""),
        ("formula_text", ""),
        ("formula_sql", ""),
        ("owner", ""),
        ("business_domain", ""),
        ("boundary", {}),
        ("unit", "CNY"),
        ("sensitivity_level", "internal"),
        ("term_code", None),
    ):
        cleaned.setdefault(field, default)
    ensure_staging_available()
    now = _now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _row_by_code(conn, cleaned["code"]) is not None:
                raise AppError(409, "VALIDATION_FAILED", f"指标编码已存在: {cleaned['code']}")
            snapshot = _snapshot_of(cleaned)
            conn.execute(
                """INSERT INTO metric_definition
                   (code, name, definition, formula_text, formula_sql, boundary_json,
                    owner, business_domain, unit, sensitivity_level, status,
                    current_version, term_code, sort_order, created_at, updated_at, updated_by)
                   VALUES (:code, :name, :definition, :formula_text, :formula_sql, :boundary_json,
                           :owner, :business_domain, :unit, :sensitivity_level, 'draft',
                           NULL, :term_code, :sort_order, :now, :now, :role)""",
                {
                    **cleaned,
                    "boundary_json": json.dumps(cleaned["boundary"], ensure_ascii=False),
                    "sort_order": 999,
                    "now": now,
                    "role": role,
                },
            )
            conn.execute(
                """INSERT INTO metric_version
                   (metric_code, version, status, snapshot_json, change_note,
                    effective_period, certified_by, certified_at, created_by,
                    created_at, updated_at)
                   VALUES (:code, 'v0.1', 'draft', :snapshot, '新建指标草稿',
                           NULL, NULL, NULL, :role, :now, :now)""",
                {
                    "code": cleaned["code"],
                    "snapshot": json.dumps(snapshot, ensure_ascii=False),
                    "role": role,
                    "now": now,
                },
            )
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return get_metric(cleaned["code"])


def update_metric(code: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
    cleaned = _validate_payload(payload)
    if not cleaned:
        return get_metric(code)
    ensure_staging_available()
    now = _now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _definition(_require_definition(conn, code))
            merged = {**current, **cleaned}
            draft = conn.execute(
                f"SELECT {_VERSION_COLUMNS} FROM metric_version "
                "WHERE metric_code = :code AND status = 'draft' ORDER BY id DESC LIMIT 1",
                {"code": code},
            ).fetchone()
            if draft is None:
                target_version = _next_version(conn, code)
                snapshot = _snapshot_of(merged)
                cursor = conn.execute(
                    """INSERT INTO metric_version
                       (metric_code, version, status, snapshot_json, change_note,
                        effective_period, certified_by, certified_at, created_by,
                        created_at, updated_at)
                       VALUES (:code, :version, 'draft', :snapshot, :note,
                               NULL, NULL, NULL, :role, :now, :now)""",
                    {
                        "code": code,
                        "version": target_version,
                        "snapshot": json.dumps(snapshot, ensure_ascii=False),
                        "note": f"{role} 编辑生成草稿",
                        "role": role,
                        "now": now,
                    },
                )
                version_id = int(cursor.lastrowid)
            else:
                target_version = draft["version"]
                version_id = int(draft["id"])
                snapshot = _snapshot_of(merged)
                conn.execute(
                    """UPDATE metric_version
                       SET snapshot_json = :snapshot, updated_at = :now
                       WHERE id = :id""",
                    {
                        "snapshot": json.dumps(snapshot, ensure_ascii=False),
                        "now": now,
                        "id": version_id,
                    },
                )
            conn.execute(
                """UPDATE metric_test_case
                   SET version_id = :version_id, updated_at = :now
                   WHERE metric_code = :code""",
                {"version_id": version_id, "now": now, "code": code},
            )
            conn.execute(
                """UPDATE metric_definition
                   SET name = :name, definition = :definition, formula_text = :formula_text,
                       formula_sql = :formula_sql, boundary_json = :boundary_json,
                       owner = :owner, business_domain = :business_domain, unit = :unit,
                       sensitivity_level = :sensitivity_level, term_code = :term_code,
                       updated_at = :now, updated_by = :role
                   WHERE code = :code""",
                {
                    "code": code,
                    "name": merged["name"],
                    "definition": merged["definition"],
                    "formula_text": merged["formula_text"],
                    "formula_sql": merged["formula_sql"],
                    "boundary_json": json.dumps(merged["boundary"], ensure_ascii=False),
                    "owner": merged["owner"],
                    "business_domain": merged["business_domain"],
                    "unit": merged["unit"],
                    "sensitivity_level": merged["sensitivity_level"],
                    "term_code": merged["term_code"],
                    "now": now,
                    "role": role,
                },
            )
            if current["status"] == "draft" or current["status"] == "pending":
                conn.execute(
                    "UPDATE metric_definition SET status = 'draft' WHERE code = :code AND current_version IS NULL",
                    {"code": code},
                )
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return get_metric(code)


def list_versions(code: str) -> dict[str, Any]:
    ensure_staging_available()
    with staging_connection() as conn:
        _require_definition(conn, code)
        rows = conn.execute(
            f"SELECT {_VERSION_COLUMNS} FROM metric_version "
            "WHERE metric_code = :code ORDER BY id DESC",
            {"code": code},
        ).fetchall()
    return {"items": [_version(row) for row in rows]}


def submit_version(code: str, version: str, role: str, change_note: str | None = None) -> dict[str, Any]:
    ensure_staging_available()
    now = _now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _require_version(conn, code, version)
            if row["status"] == "certified":
                conn.execute("COMMIT")
                return _version(row)
            if row["status"] == "deprecated":
                raise AppError(422, "VALIDATION_FAILED", "已废弃版本不能提交认证")
            conn.execute(
                """UPDATE metric_version
                   SET status = 'pending', change_note = :note, updated_at = :now
                   WHERE id = :id""",
                {
                    "note": (change_note or row["change_note"] or ""),
                    "now": now,
                    "id": row["id"],
                },
            )
            definition = _require_definition(conn, code)
            if definition["current_version"] is None:
                conn.execute(
                    "UPDATE metric_definition SET status = 'pending', updated_at = :now WHERE code = :code",
                    {"now": now, "code": code},
                )
            refreshed = _require_version(conn, code, version)
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return _version(refreshed)


def run_tests(code: str, role: str, version: str | None = None) -> dict[str, Any]:
    ensure_staging_available()
    now = _now()
    with staging_connection() as conn:
        definition = _definition(_require_definition(conn, code))
        if version is None:
            draft = conn.execute(
                "SELECT version FROM metric_version WHERE metric_code = :code "
                "AND status IN ('draft','pending') ORDER BY id DESC LIMIT 1",
                {"code": code},
            ).fetchone()
            target_version = (
                draft["version"] if draft else definition["current_version"]
            )
        else:
            target_version = version
        if target_version is None:
            raise AppError(422, "VALIDATION_FAILED", "指标没有可执行的版本")
        _require_version(conn, code, target_version)
        test_rows = conn.execute(
            f"SELECT {_TEST_CASE_COLUMNS} FROM metric_test_case "
            "WHERE metric_code = :code ORDER BY id",
            {"code": code},
        ).fetchall()

    results: list[dict[str, Any]] = []
    passed = failed = 0
    for row in test_rows:
        case = _test_case(row)
        expected = float(case["expected_value"])
        try:
            execution = execute_metric(code, case["period_code"], version=target_version)
            actual = float(execution["value"])
            diff = round(actual - expected, 4)
            tolerance_abs = float(case["tolerance_abs"])
            tolerance_rel = float(case["tolerance_rel"])
            abs_ok = abs(diff) <= tolerance_abs
            rel_ok = expected != 0 and abs(diff) / abs(expected) <= tolerance_rel
            status = "passed" if (abs_ok or rel_ok) else "failed"
            message = None if status == "passed" else f"期望 {expected}，实际 {actual}，差异 {diff}"
        except AppError as exc:
            actual = None
            diff = None
            status = "error"
            message = exc.message[:300]
        except Exception as exc:  # noqa: BLE001 - 单条用例失败不阻断其余用例
            actual = None
            diff = None
            status = "error"
            message = str(exc)[:300]
        if status == "passed":
            passed += 1
        elif status == "failed":
            failed += 1
        results.append(
            {
                **case,
                "last_status": status,
                "last_actual": actual,
                "last_diff": diff,
                "last_run_at": now,
                "last_message": message,
            }
        )

    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in results:
                conn.execute(
                    """UPDATE metric_test_case
                       SET last_status = :status, last_actual = :actual, last_diff = :diff,
                           last_run_at = :now, last_message = :message, updated_at = :now
                       WHERE id = :id""",
                    {
                        "status": item["last_status"],
                        "actual": item["last_actual"],
                        "diff": item["last_diff"],
                        "now": now,
                        "message": item["last_message"],
                        "id": item["id"],
                    },
                )
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return {
        "code": code,
        "version": target_version,
        "items": results,
        "passed": passed,
        "failed": failed,
        "errors": sum(1 for item in results if item["last_status"] == "error"),
        "total": len(results),
    }


def certify_version(
    code: str, version: str, role: str, change_note: str | None = None
) -> dict[str, Any]:
    if role != "admin":
        raise AppError(403, "FORBIDDEN", "指标认证仅 admin 可执行")
    ensure_staging_available()
    with staging_connection() as conn:
        row = _require_version(conn, code, version)
        if row["status"] == "deprecated":
            raise AppError(422, "VALIDATION_FAILED", "已废弃版本不能认证")
    test_result = run_tests(code, role, version=version)
    if test_result["total"] == 0:
        with staging_connection() as conn:
            definition = _definition(_require_definition(conn, code))
        if not str(definition.get("formula_sql") or "").strip():
            raise AppError(
                422,
                "VALIDATION_FAILED",
                "指标缺少公式且没有测试用例，不能认证",
                {"code": code, "version": version},
            )
    if test_result["failed"] > 0 or test_result["errors"] > 0:
        failed_items = [
            {
                "name": item["name"],
                "expected": item["expected_value"],
                "actual": item["last_actual"],
                "diff": item["last_diff"],
                "message": item["last_message"],
            }
            for item in test_result["items"]
            if item["last_status"] != "passed"
        ]
        raise AppError(
            422,
            "METRIC_TEST_FAILED",
            "认证前测试用例未全部通过",
            {"failed": failed_items, "version": version},
        )
    now = _now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """UPDATE metric_version
                   SET status = 'deprecated', updated_at = :now
                   WHERE metric_code = :code AND status = 'certified' AND version <> :version""",
                {"code": code, "version": version, "now": now},
            )
            conn.execute(
                """UPDATE metric_version
                   SET status = 'certified', certified_by = :role, certified_at = :now,
                       change_note = COALESCE(:note, change_note), updated_at = :now
                   WHERE metric_code = :code AND version = :version""",
                {
                    "code": code,
                    "version": version,
                    "role": role,
                    "now": now,
                    "note": change_note,
                },
            )
            conn.execute(
                """UPDATE metric_definition
                   SET status = 'certified', current_version = :version,
                       updated_at = :now, updated_by = :role
                   WHERE code = :code""",
                {"code": code, "version": version, "now": now, "role": role},
            )
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return get_metric(code)


def create_alias(code: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
    alias = str(payload.get("alias") or "").strip()
    alias_type = str(payload.get("alias_type") or "")
    priority = int(payload.get("priority") or 0)
    if not alias or len(alias) > 64:
        raise AppError(422, "VALIDATION_FAILED", "alias 长度必须为 1-64")
    if alias_type not in ALIAS_TYPES:
        raise AppError(422, "VALIDATION_FAILED", "alias_type 仅支持 synonym/business/exclusion")
    if priority < 0 or priority > 1000:
        raise AppError(422, "VALIDATION_FAILED", "priority 必须在 0-1000")
    normalized = metric_seed.normalize_alias(alias)
    ensure_staging_available()
    now = _now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_definition(conn, code)
            existing = conn.execute(
                "SELECT id FROM metric_alias WHERE metric_code = :code AND normalized_alias = :norm",
                {"code": code, "norm": normalized},
            ).fetchone()
            if existing:
                raise AppError(409, "METRIC_ALIAS_CONFLICT", "该指标已存在同名别名")
            cursor = conn.execute(
                """INSERT INTO metric_alias
                   (metric_code, alias, normalized_alias, alias_type, priority,
                    status, created_by, created_at, updated_at)
                   VALUES (:code, :alias, :norm, :alias_type, :priority,
                           'active', :role, :now, :now)""",
                {
                    "code": code,
                    "alias": alias,
                    "norm": normalized,
                    "alias_type": alias_type,
                    "priority": priority,
                    "role": role,
                    "now": now,
                },
            )
            alias_id = int(cursor.lastrowid)
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return _get_alias(alias_id)


def update_alias(code: str, alias_id: int, payload: dict[str, Any], role: str) -> dict[str, Any]:
    ensure_staging_available()
    now = _now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                f"SELECT {_ALIAS_COLUMNS} FROM metric_alias WHERE id = :id AND metric_code = :code",
                {"id": alias_id, "code": code},
            ).fetchone()
            if row is None:
                raise AppError(404, "METRIC_ALIAS_NOT_FOUND", f"别名不存在: {alias_id}")
            alias = str(payload.get("alias") or row["alias"]).strip()
            alias_type = str(payload.get("alias_type") or row["alias_type"])
            priority = int(payload.get("priority", row["priority"]))
            status = str(payload.get("status") or row["status"])
            if not alias or len(alias) > 64:
                raise AppError(422, "VALIDATION_FAILED", "alias 长度必须为 1-64")
            if alias_type not in ALIAS_TYPES:
                raise AppError(422, "VALIDATION_FAILED", "alias_type 非法")
            if status not in ("active", "disabled"):
                raise AppError(422, "VALIDATION_FAILED", "status 非法")
            normalized = metric_seed.normalize_alias(alias)
            duplicate = conn.execute(
                """SELECT id FROM metric_alias
                   WHERE metric_code = :code AND normalized_alias = :norm AND id <> :id""",
                {"code": code, "norm": normalized, "id": alias_id},
            ).fetchone()
            if duplicate:
                raise AppError(409, "METRIC_ALIAS_CONFLICT", "该指标已存在同名别名")
            conn.execute(
                """UPDATE metric_alias
                   SET alias = :alias, normalized_alias = :norm, alias_type = :alias_type,
                       priority = :priority, status = :status, updated_at = :now
                   WHERE id = :id""",
                {
                    "alias": alias,
                    "norm": normalized,
                    "alias_type": alias_type,
                    "priority": priority,
                    "status": status,
                    "now": now,
                    "id": alias_id,
                },
            )
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return _get_alias(alias_id)


def _get_alias(alias_id: int) -> dict[str, Any]:
    with staging_connection() as conn:
        row = conn.execute(
            f"SELECT {_ALIAS_COLUMNS} FROM metric_alias WHERE id = :id",
            {"id": alias_id},
        ).fetchone()
    if row is None:
        raise AppError(404, "METRIC_ALIAS_NOT_FOUND", f"别名不存在: {alias_id}")
    return _alias(row)


def delete_alias(code: str, alias_id: int, role: str) -> dict[str, Any]:
    ensure_staging_available()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT id FROM metric_alias WHERE id = :id AND metric_code = :code",
                {"id": alias_id, "code": code},
            ).fetchone()
            if row is None:
                raise AppError(404, "METRIC_ALIAS_NOT_FOUND", f"别名不存在: {alias_id}")
            conn.execute("DELETE FROM metric_alias WHERE id = :id", {"id": alias_id})
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return {"deleted": True, "id": alias_id}


def active_aliases() -> list[dict[str, Any]]:
    """认证指标 + active 别名；staging 不可用时返回空列表（问数降级）。"""
    try:
        ensure_staging_available()
        with staging_connection() as conn:
            rows = conn.execute(
                """SELECT a.id, a.metric_code, a.alias, a.normalized_alias, a.alias_type,
                          a.priority, d.name AS metric_name, d.term_code,
                          d.current_version
                   FROM metric_alias a
                   JOIN metric_definition d ON d.code = a.metric_code
                   WHERE a.status = 'active' AND d.status = 'certified'
                   ORDER BY a.id"""
            ).fetchall()
    except Exception:  # noqa: BLE001 - 问数必须可降级到旧路径
        return []
    return [
        {
            "id": row["id"],
            "metric_code": row["metric_code"],
            "metric_name": row["metric_name"],
            "metric_version": row["current_version"],
            "term_code": row["term_code"],
            "alias": row["alias"],
            "normalized_alias": row["normalized_alias"],
            "alias_type": row["alias_type"],
            "priority": row["priority"],
        }
        for row in rows
    ]


def find_certified_metric_by_alias(text: str) -> dict[str, Any] | None:
    from app.services import alias_resolver

    try:
        resolution = alias_resolver.resolve(text)
    except Exception:  # noqa: BLE001
        return None
    if resolution.kind != "matched" or not resolution.metric_code:
        return None
    return get_certified_metric(resolution.metric_code)


def get_certified_metric(code: str) -> dict[str, Any] | None:
    try:
        ensure_staging_available()
        with staging_connection() as conn:
            row = conn.execute(
                f"SELECT {_DEFINITION_COLUMNS} FROM metric_definition "
                "WHERE code = :code AND status = 'certified'",
                {"code": code},
            ).fetchone()
            if row is None:
                return None
            definition = _definition(row)
            version = None
            if row["current_version"]:
                version_row = conn.execute(
                    f"SELECT {_VERSION_COLUMNS} FROM metric_version "
                    "WHERE metric_code = :code AND version = :version",
                    {"code": code, "version": row["current_version"]},
                ).fetchone()
                version = _version(version_row) if version_row else None
            aliases = [
                _alias(alias_row)
                for alias_row in conn.execute(
                    f"SELECT {_ALIAS_COLUMNS} FROM metric_alias "
                    "WHERE metric_code = :code AND status = 'active' ORDER BY priority DESC, id",
                    {"code": code},
                ).fetchall()
            ]
    except AppError:
        return None
    return {
        **definition,
        "version": version["version"] if version else None,
        "certified_by": version["certified_by"] if version else None,
        "certified_at": version["certified_at"] if version else None,
        "effective_period": version["effective_period"] if version else None,
        "aliases": aliases,
    }


def execute_metric(
    code: str,
    period_code: str,
    version: str | None = None,
    *,
    start_period: str | None = None,
) -> dict[str, Any]:
    ensure_staging_available()
    with staging_connection() as conn:
        definition_row = _require_definition(conn, code)
        definition = _definition(definition_row)
        version_row = None
        if version is not None:
            version_row = _require_version(conn, code, version)
        elif definition_row["current_version"]:
            version_row = conn.execute(
                f"SELECT {_VERSION_COLUMNS} FROM metric_version "
                "WHERE metric_code = :code AND version = :version",
                {"code": code, "version": definition_row["current_version"]},
            ).fetchone()
        if version_row is None:
            version_row = conn.execute(
                f"SELECT {_VERSION_COLUMNS} FROM metric_version "
                "WHERE metric_code = :code ORDER BY id DESC LIMIT 1",
                {"code": code},
            ).fetchone()
    snapshot = _version(version_row)["snapshot"] if version_row else {}
    formula_sql = str(snapshot.get("formula_sql") or definition["formula_sql"] or "")
    formula_text = str(snapshot.get("formula_text") or definition["formula_text"] or "")
    boundary_raw = snapshot.get("boundary") if isinstance(snapshot.get("boundary"), dict) else definition["boundary"]
    boundary = {str(key): str(value) for key, value in (boundary_raw or {}).items()}
    version_label = version_row["version"] if version_row else definition["current_version"]

    from app.services import query

    guarded = query.guard_sql(
        formula_sql, allow_sensitive=False, allowed_tables=frozenset(BUSINESS_TABLES)
    )
    if not guarded.ok:
        raise AppError(422, "METRIC_FORMULA_INVALID", f"公式未通过只读守卫：{guarded.reason}")
    params = {
        "start_period": start_period or period_code,
        "end_period": period_code,
        "period_id": period_code,
    }
    try:
        result = query.execute_query(guarded.sql, params)
    except AppError as exc:
        if exc.code in ("DB_UNAVAILABLE",):
            raise
        raise AppError(422, "METRIC_FORMULA_INVALID", f"公式执行失败：{exc.message}", exc.details) from exc
    rows = result.get("rows") or []
    if len(rows) != 1 or "value" not in rows[0]:
        raise AppError(422, "METRIC_FORMULA_INVALID", "公式必须返回单行单列 value")
    value = rows[0]["value"]
    if value is None:
        raise AppError(422, "METRIC_FORMULA_INVALID", "公式返回 value 为空")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(422, "METRIC_FORMULA_INVALID", "公式 value 必须为数值") from exc
    return {
        "code": code,
        "value": numeric,
        "unit": snapshot.get("unit") or definition["unit"],
        "name": snapshot.get("name") or definition["name"],
        "definition": snapshot.get("definition") or definition["definition"],
        "formula_text": formula_text,
        "formula_sql": formula_sql,
        "sql": guarded.sql,
        "params": params,
        "version": version_label,
        "boundary": boundary,
        "certified_by": version_row["certified_by"] if version_row else None,
        "certified_at": version_row["certified_at"] if version_row else None,
    }


def appendix_metrics(codes: list[str]) -> list[dict[str, Any]]:
    if not codes:
        return []
    unique_codes = list(dict.fromkeys(codes))
    placeholders = ", ".join(f":code_{index}" for index in range(len(unique_codes)))
    params = {f"code_{index}": code for index, code in enumerate(unique_codes)}
    try:
        ensure_staging_available()
        with staging_connection() as conn:
            rows = conn.execute(
                f"""SELECT d.code, d.name, d.definition, d.formula_text, d.boundary_json,
                           d.owner, d.unit, d.current_version,
                           v.certified_by, v.certified_at
                    FROM metric_definition d
                    LEFT JOIN metric_version v
                      ON v.metric_code = d.code AND v.version = d.current_version
                    WHERE d.code IN ({placeholders})""",
                params,
            ).fetchall()
    except Exception:  # noqa: BLE001 - 附录失败由调用方回退
        return []
    by_code = {
        row["code"]: {
            "code": row["code"],
            "name": row["name"],
            "definition": row["definition"],
            "formula_text": row["formula_text"],
            "boundary": _boundary(row),
            "owner": row["owner"],
            "unit": row["unit"],
            "version": row["current_version"],
            "certified_by": row["certified_by"],
            "certified_at": row["certified_at"],
            **_knowledge_basis_fields(row["code"]),
        }
        for row in rows
    }
    return [by_code[code] for code in unique_codes if code in by_code]
