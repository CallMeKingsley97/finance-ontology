"""质量规则执行器：规则 CRUD、批量运行、结果持久化与告警查询。"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings
from app.db.connection import BUSINESS_TABLES, connect, query_one
from app.schemas.common import AppError
from app.services import quality_seed
from app.services.ingest.store import (
    ensure_staging_available,
    staging_connection,
    staging_now,
    staging_readonly_connection,
)
from app.services.query import guard_sql

logger = logging.getLogger("finance_ontology.quality")

RULE_TYPES = ("reconciliation", "completeness", "uniqueness", "range", "custom_sql")
TARGET_DBS = ("business", "staging")
SEVERITIES = ("info", "warning", "blocker")
SCHEDULES = ("manual", "sync", "report", "sync_report")
OPERATORS = ("==", "!=", ">", ">=", "<", "<=")
RESULT_STATUSES = ("passed", "failed", "skipped", "error")

_STAGING_META_TABLES = frozenset(
    {
        "ingest_run",
        "ingest_run_table",
        "staging_table",
        "catalog_table",
        "catalog_column",
        "catalog_change_log",
    }
)
_STAGING_NAME = re.compile(r"^stg_[a-z0-9_]{1,64}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

_RULE_COLUMNS = """id, code, name, rule_type, target_db, target_ref, expression_json,
    tolerance, severity, enabled, schedule, owner, description,
    created_at, updated_at, updated_by"""

_RUN_COLUMNS = """run_id, trigger_type, trigger_role, period_code, source_id, ingest_run_id,
    status, rules_total, rules_passed, rules_failed, rules_skipped, blocker_failed,
    error_message, started_at, finished_at, created_at, updated_at"""

_RESULT_COLUMNS = """id, run_id, rule_code, rule_name, rule_type, target_db, severity,
    status, left_value, right_value, diff, tolerance, detail_json, error_message, created_at"""


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _load_json(text: str | None) -> Any:
    try:
        return json.loads(text or "")
    except (TypeError, ValueError):
        return None


def _rule(row: sqlite3.Row) -> dict[str, Any]:
    expression = _load_json(row["expression_json"])
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "rule_type": row["rule_type"],
        "target_db": row["target_db"],
        "target_ref": row["target_ref"],
        "expression": expression if isinstance(expression, dict) else {},
        "expression_json": row["expression_json"],
        "tolerance": row["tolerance"],
        "severity": row["severity"],
        "enabled": bool(row["enabled"]),
        "schedule": row["schedule"],
        "owner": row["owner"],
        "description": row["description"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def _result(row: sqlite3.Row) -> dict[str, Any]:
    detail = _load_json(row["detail_json"])
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "rule_code": row["rule_code"],
        "rule_name": row["rule_name"],
        "rule_type": row["rule_type"],
        "target_db": row["target_db"],
        "severity": row["severity"],
        "status": row["status"],
        "left_value": row["left_value"],
        "right_value": row["right_value"],
        "diff": row["diff"],
        "tolerance": row["tolerance"],
        "detail": detail if isinstance(detail, dict) else {},
        "error_message": row["error_message"],
        "created_at": row["created_at"],
    }


def _run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "trigger_type": row["trigger_type"],
        "trigger_role": row["trigger_role"],
        "period_code": row["period_code"],
        "source_id": row["source_id"],
        "ingest_run_id": row["ingest_run_id"],
        "status": row["status"],
        "rules_total": row["rules_total"],
        "rules_passed": row["rules_passed"],
        "rules_failed": row["rules_failed"],
        "rules_skipped": row["rules_skipped"],
        "blocker_failed": row["blocker_failed"],
        "error_message": row["error_message"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _cutoff_iso(days: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")


def ensure_rules() -> dict[str, Any]:
    return quality_seed.ensure_quality_rules()


def _latest_period() -> str | None:
    try:
        row = query_one(
            "SELECT period_code FROM dim_period ORDER BY period_code DESC LIMIT 1",
            enforce_authorizer=False,
        )
    except sqlite3.Error:
        return None
    return row["period_code"] if row else None


def _validate_period(period_code: str | None) -> str | None:
    if period_code is None or str(period_code).strip() == "":
        return None
    value = str(period_code).strip()
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise AppError(422, "VALIDATION_FAILED", "period 必须为 YYYY-MM") from exc
    return value


def _staging_allowed_tables() -> frozenset[str]:
    ensure_staging_available()
    with staging_connection() as conn:
        rows = conn.execute(
            "SELECT staging_name FROM staging_table WHERE staging_name IS NOT NULL"
        ).fetchall()
    dynamic = {
        str(row["staging_name"]).lower()
        for row in rows
        if _STAGING_NAME.match(str(row["staging_name"]).lower())
    }
    return frozenset(_STAGING_META_TABLES | dynamic)


def _business_connection() -> sqlite3.Connection:
    return connect(read_only=True, allowed_tables=frozenset(BUSINESS_TABLES))


def _execute_scalar(conn: sqlite3.Connection, sql: str, params: dict[str, Any]) -> float:
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        raise ValueError("规则 SQL 返回空结果")
    columns = [item[0] for item in (cursor.description or [])]
    if "value" in columns:
        raw = row["value"]
    elif columns:
        raw = row[0]
    else:
        raise ValueError("规则 SQL 缺少结果列")
    if raw is None:
        raise ValueError("规则 SQL 标量为空")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("规则 SQL 标量必须为数值") from exc


def _target_missing(rule: dict[str, Any], staging_tables: frozenset[str]) -> str | None:
    target_ref = str(rule.get("target_ref") or "").strip().lower()
    if rule["target_db"] != "staging" or not target_ref:
        return None
    if target_ref == "catalog_table":
        with staging_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM catalog_table").fetchone()
        if not row or int(row["n"]) == 0:
            return "staging 目录尚未登记任何表"
        return None
    if target_ref not in staging_tables:
        return f"staging 目标表不存在: {target_ref}"
    return None


def _params_for(period_code: str | None) -> dict[str, Any]:
    return {
        "start_period": period_code,
        "end_period": period_code,
        "period_id": period_code,
    }


def _needs_period(sql: str) -> bool:
    return bool(re.search(r":(?:period_id|start_period|end_period)\b", sql, re.I))


def _guard_rule_sql(
    sql: str, target_db: str, staging_tables: frozenset[str]
) -> Any:
    allowed = (
        frozenset(BUSINESS_TABLES)
        if target_db == "business"
        else staging_tables
    )
    allow_no_period = target_db == "staging" or not _needs_period(sql)
    return guard_sql(
        sql,
        allow_no_period=allow_no_period,
        allow_sensitive=False,
        allowed_tables=allowed,
    )


def _compare(operator: str, actual: float, expected: float) -> bool:
    if operator == "==":
        return actual == expected
    if operator == "!=":
        return actual != expected
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "<":
        return actual < expected
    if operator == "<=":
        return actual <= expected
    raise ValueError(f"不支持的操作符: {operator}")


def _evaluate_rule(
    rule: dict[str, Any],
    *,
    period_code: str | None,
    staging_tables: frozenset[str],
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rule_code": rule["code"],
        "rule_name": rule["name"],
        "rule_type": rule["rule_type"],
        "target_db": rule["target_db"],
        "severity": rule["severity"],
        "left_value": None,
        "right_value": None,
        "diff": None,
        "tolerance": rule["tolerance"],
        "detail": {"period_code": period_code, "target_ref": rule.get("target_ref") or ""},
        "error_message": None,
    }
    if not rule["enabled"]:
        return {**base, "status": "skipped", "detail": {**base["detail"], "reason": "规则已禁用"}}
    missing = _target_missing(rule, staging_tables)
    if missing:
        return {**base, "status": "skipped", "detail": {**base["detail"], "reason": missing}}

    expression = rule.get("expression") or {}
    params = _params_for(period_code)
    conn: sqlite3.Connection | None = None
    try:
        if rule["target_db"] == "staging":
            conn = staging_readonly_connection(staging_tables)
        else:
            conn = _business_connection()
        if rule["rule_type"] == "reconciliation":
            left_sql = str(expression.get("left_sql") or "")
            right_sql = str(expression.get("right_sql") or "")
            left_guard = _guard_rule_sql(left_sql, rule["target_db"], staging_tables)
            if not left_guard.ok:
                raise ValueError(f"左侧 SQL 未通过守卫：{left_guard.reason}")
            right_guard = _guard_rule_sql(right_sql, rule["target_db"], staging_tables)
            if not right_guard.ok:
                raise ValueError(f"右侧 SQL 未通过守卫：{right_guard.reason}")
            left = _execute_scalar(conn, left_guard.sql, params)
            right = _execute_scalar(conn, right_guard.sql, params)
            diff = round(left - right, 4)
            passed = abs(diff) <= float(rule["tolerance"])
            return {
                **base,
                "status": "passed" if passed else "failed",
                "left_value": left,
                "right_value": right,
                "diff": diff,
                "detail": {
                    **base["detail"],
                    "tolerance": rule["tolerance"],
                    "sql": {"left": left_guard.sql, "right": right_guard.sql},
                },
            }

        sql = str(expression.get("sql") or "")
        guarded = _guard_rule_sql(sql, rule["target_db"], staging_tables)
        if not guarded.ok:
            raise ValueError(f"规则 SQL 未通过守卫：{guarded.reason}")
        actual = _execute_scalar(conn, guarded.sql, params)
        if rule["rule_type"] == "range":
            minimum = expression.get("min")
            maximum = expression.get("max")
            low = float(minimum) if minimum is not None else None
            high = float(maximum) if maximum is not None else None
            lower_ok = low is None or actual >= low
            upper_ok = high is None or actual <= high
            expected = high if high is not None else low
            return {
                **base,
                "status": "passed" if (lower_ok and upper_ok) else "failed",
                "left_value": actual,
                "right_value": expected,
                "diff": None if expected is None else round(actual - expected, 4),
                "detail": {**base["detail"], "min": low, "max": high, "sql": guarded.sql},
            }
        operator = str(expression.get("operator") or "")
        expected = float(expression.get("expected"))
        passed = _compare(operator, actual, expected)
        return {
            **base,
            "status": "passed" if passed else "failed",
            "left_value": actual,
            "right_value": expected,
            "diff": round(actual - expected, 4),
            "detail": {
                **base["detail"],
                "operator": operator,
                "expected": expected,
                "sql": guarded.sql,
            },
        }
    except Exception as exc:  # noqa: BLE001 - 单条规则失败必须落 result
        return {
            **base,
            "status": "error",
            "error_message": str(exc)[:300],
        }
    finally:
        if conn is not None:
            conn.close()


def _select_rules(trigger_type: str, rule_codes: list[str] | None) -> list[dict[str, Any]]:
    with staging_connection() as conn:
        if rule_codes:
            placeholders = ", ".join(f":code_{index}" for index in range(len(rule_codes)))
            params = {f"code_{index}": code for index, code in enumerate(rule_codes)}
            rows = conn.execute(
                f"SELECT {_RULE_COLUMNS} FROM quality_rule WHERE code IN ({placeholders}) ORDER BY id",
                params,
            ).fetchall()
            return [_rule(row) for row in rows]
        if trigger_type == "sync":
            rows = conn.execute(
                f"""SELECT {_RULE_COLUMNS} FROM quality_rule
                    WHERE enabled = 1 AND schedule IN ('sync','sync_report')
                    ORDER BY id"""
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_RULE_COLUMNS} FROM quality_rule WHERE enabled = 1 ORDER BY id"
            ).fetchall()
    return [_rule(row) for row in rows]


def _prune_old_runs() -> None:
    cutoff = _cutoff_iso(settings.staging_run_retention_days)
    try:
        with staging_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM quality_run WHERE created_at < :cutoff", {"cutoff": cutoff})
                conn.execute("COMMIT")
            except Exception:
                _safe_rollback(conn)
                raise
    except Exception:  # noqa: BLE001 - 清理失败不影响主流程
        logger.exception("quality prune failed cutoff=%s", cutoff)


def _new_run_id() -> str:
    return f"qrun_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _persist_run_results(
    run_id: str,
    results: list[dict[str, Any]],
    *,
    status: str,
    error_message: str | None,
    finished_at: str,
    rules_total: int,
    passed: int,
    failed: int,
    skipped: int,
    blocker_failed: int,
) -> None:
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in results:
                conn.execute(
                    """INSERT INTO quality_result
                       (run_id, rule_code, rule_name, rule_type, target_db, severity,
                        status, left_value, right_value, diff, tolerance,
                        detail_json, error_message, created_at)
                       VALUES (:run_id, :rule_code, :rule_name, :rule_type, :target_db,
                               :severity, :status, :left_value, :right_value, :diff,
                               :tolerance, :detail_json, :error_message, :created_at)""",
                    {
                        "run_id": run_id,
                        "rule_code": item["rule_code"],
                        "rule_name": item["rule_name"],
                        "rule_type": item["rule_type"],
                        "target_db": item["target_db"],
                        "severity": item["severity"],
                        "status": item["status"],
                        "left_value": item.get("left_value"),
                        "right_value": item.get("right_value"),
                        "diff": item.get("diff"),
                        "tolerance": item.get("tolerance"),
                        "detail_json": json.dumps(item.get("detail") or {}, ensure_ascii=False),
                        "error_message": item.get("error_message"),
                        "created_at": finished_at,
                    },
                )
            conn.execute(
                """UPDATE quality_run
                   SET status = :status, rules_total = :rules_total, rules_passed = :passed,
                       rules_failed = :failed, rules_skipped = :skipped,
                       blocker_failed = :blocker_failed, error_message = :error_message,
                       finished_at = :finished_at, updated_at = :finished_at
                   WHERE run_id = :run_id""",
                {
                    "status": status,
                    "rules_total": rules_total,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "blocker_failed": blocker_failed,
                    "error_message": error_message,
                    "finished_at": finished_at,
                    "run_id": run_id,
                },
            )
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise


def run_quality(
    *,
    trigger_type: str,
    period_code: str | None = None,
    role: str = "system",
    rule_codes: list[str] | None = None,
    source_id: str | None = None,
    ingest_run_id: str | None = None,
) -> dict[str, Any]:
    """执行一次质量检查；每条参与规则都会写入一条 quality_result。"""
    if trigger_type not in ("manual", "sync", "report"):
        raise AppError(422, "VALIDATION_FAILED", "trigger_type 非法")
    normalized_period = _validate_period(period_code)
    ensure_rules()
    ensure_staging_available()
    _prune_old_runs()
    rules = _select_rules(trigger_type, rule_codes)
    if normalized_period is None and any(
        _needs_period(str((rule.get("expression") or {}).get("sql") or ""))
        or _needs_period(str((rule.get("expression") or {}).get("left_sql") or ""))
        or _needs_period(str((rule.get("expression") or {}).get("right_sql") or ""))
        for rule in rules
    ):
        normalized_period = _latest_period()
    try:
        staging_tables = _staging_allowed_tables()
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError(503, "STAGING_UNAVAILABLE", "staging 库不可用", {"error": str(exc)}) from exc

    run_id = _new_run_id()
    started_at = staging_now()
    try:
        with staging_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO quality_run
                       (run_id, trigger_type, trigger_role, period_code, source_id,
                        ingest_run_id, status, rules_total, rules_passed, rules_failed,
                        rules_skipped, blocker_failed, error_message, started_at,
                        finished_at, created_at, updated_at)
                       VALUES (:run_id, :trigger_type, :role, :period, :source_id,
                               :ingest_run_id, 'running', 0, 0, 0, 0, 0, NULL,
                               :started_at, NULL, :started_at, :started_at)""",
                    {
                        "run_id": run_id,
                        "trigger_type": trigger_type,
                        "role": role,
                        "period": normalized_period,
                        "source_id": source_id,
                        "ingest_run_id": ingest_run_id,
                        "started_at": started_at,
                    },
                )
                conn.execute("COMMIT")
            except Exception:
                _safe_rollback(conn)
                raise

        results = [
            _evaluate_rule(rule, period_code=normalized_period, staging_tables=staging_tables)
            for rule in rules
        ]
        passed = sum(1 for item in results if item["status"] == "passed")
        failed = sum(1 for item in results if item["status"] == "failed")
        skipped = sum(1 for item in results if item["status"] == "skipped")
        errors = sum(1 for item in results if item["status"] == "error")
        blocker_failed = sum(
            1
            for item in results
            if item["severity"] == "blocker" and item["status"] in ("failed", "error")
        )
        if failed == 0 and errors == 0:
            status = "succeeded"
        elif passed == 0 and skipped == 0:
            status = "failed"
        else:
            status = "partial"
        finished_at = staging_now()
        _persist_run_results(
            run_id,
            results,
            status=status,
            error_message=None,
            finished_at=finished_at,
            rules_total=len(results),
            passed=passed,
            failed=failed,
            skipped=skipped,
            blocker_failed=blocker_failed,
        )
        return {
            "run_id": run_id,
            "trigger_type": trigger_type,
            "trigger_role": role,
            "period_code": normalized_period,
            "source_id": source_id,
            "ingest_run_id": ingest_run_id,
            "status": status,
            "rules_total": len(results),
            "rules_passed": passed,
            "rules_failed": failed,
            "rules_skipped": skipped,
            "blocker_failed": blocker_failed,
            "error_message": None,
            "started_at": started_at,
            "finished_at": finished_at,
            "results": results,
        }
    except Exception as exc:  # noqa: BLE001 - 未预期异常也要留下 failed run
        logger.exception("quality run failed run_id=%s", run_id)
        finished_at = staging_now()
        message = str(exc)[:300]
        try:
            _persist_run_results(
                run_id,
                [],
                status="failed",
                error_message=message,
                finished_at=finished_at,
                rules_total=len(rules),
                passed=0,
                failed=0,
                skipped=0,
                blocker_failed=0,
            )
        except Exception:  # noqa: BLE001
            logger.exception("quality run failed to persist failure run_id=%s", run_id)
        return {
            "run_id": run_id,
            "trigger_type": trigger_type,
            "trigger_role": role,
            "period_code": normalized_period,
            "source_id": source_id,
            "ingest_run_id": ingest_run_id,
            "status": "failed",
            "rules_total": len(rules),
            "rules_passed": 0,
            "rules_failed": 0,
            "rules_skipped": 0,
            "blocker_failed": 0,
            "error_message": message,
            "started_at": started_at,
            "finished_at": finished_at,
            "results": [],
        }


def run_after_sync(source_id: str, ingest_run_id: str, status: str) -> dict[str, Any] | None:
    """同步成功后 best-effort 执行质量规则；任何异常只记日志。"""
    if status not in ("succeeded", "partial"):
        return None
    try:
        return run_quality(
            trigger_type="sync",
            period_code=_latest_period(),
            role="system",
            source_id=source_id,
            ingest_run_id=ingest_run_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "quality run after sync failed source_id=%s ingest_run_id=%s",
            source_id,
            ingest_run_id,
        )
        return None


def summary() -> dict[str, Any]:
    ensure_staging_available()
    cutoff = _cutoff_iso(7)
    with staging_connection() as conn:
        rule_row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled FROM quality_rule"
        ).fetchone()
        result_row = conn.execute(
            """SELECT
                   SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS passed,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
               FROM quality_result WHERE created_at >= :cutoff""",
            {"cutoff": cutoff},
        ).fetchone()
        run_row = conn.execute(
            """SELECT COUNT(*) AS n, SUM(blocker_failed) AS blockers
               FROM quality_run WHERE started_at >= :cutoff""",
            {"cutoff": cutoff},
        ).fetchone()
        last_row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM quality_run ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
    passed = int((result_row["passed"] if result_row else 0) or 0)
    failed = int((result_row["failed"] if result_row else 0) or 0)
    errors = int((result_row["errors"] if result_row else 0) or 0)
    denominator = passed + failed + errors
    return {
        "rules_total": int(rule_row["total"] or 0) if rule_row else 0,
        "rules_enabled": int(rule_row["enabled"] or 0) if rule_row else 0,
        "pass_rate_7d": round(passed / denominator, 4) if denominator else None,
        "runs_7d": int(run_row["n"] or 0) if run_row else 0,
        "failed_results_7d": failed,
        "blocker_failed_7d": int(run_row["blockers"] or 0) if run_row else 0,
        "last_run_at": last_row["started_at"] if last_row else None,
        "last_run_status": last_row["status"] if last_row else None,
    }


def list_rules(filters: dict[str, Any]) -> dict[str, Any]:
    ensure_staging_available()
    where: list[str] = []
    params: dict[str, Any] = {}
    enabled = filters.get("enabled")
    if enabled is not None and str(enabled) != "":
        where.append("enabled = :enabled")
        params["enabled"] = 1 if str(enabled).lower() in ("1", "true", "yes") else 0
    for field in ("rule_type", "target_db"):
        value = filters.get(field)
        if value:
            where.append(f"{field} = :{field}")
            params[field] = str(value)
    q = str(filters.get("q") or "").strip()
    if q:
        where.append("instr(lower(code || ' ' || name || ' ' || description), lower(:q)) > 0")
        params["q"] = q
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with staging_connection() as conn:
        rows = conn.execute(
            f"SELECT {_RULE_COLUMNS} FROM quality_rule {clause} ORDER BY id", params
        ).fetchall()
    return {"items": [_rule(row) for row in rows]}


def _validate_expression(
    rule_type: str,
    target_db: str,
    expression: dict[str, Any],
    tolerance: float,
) -> None:
    if rule_type not in RULE_TYPES:
        raise AppError(422, "QUALITY_RULE_INVALID", "rule_type 非法")
    if target_db not in TARGET_DBS:
        raise AppError(422, "QUALITY_RULE_INVALID", "target_db 非法")
    if tolerance < 0 or tolerance > 1_000_000_000:
        raise AppError(422, "QUALITY_RULE_INVALID", "tolerance 非法")
    if not isinstance(expression, dict) or not expression:
        raise AppError(422, "QUALITY_RULE_INVALID", "expression 必须为非空对象")
    if len(json.dumps(expression, ensure_ascii=False)) > 4000:
        raise AppError(422, "QUALITY_RULE_INVALID", "expression 超过 4000 字符")
    allowed: set[str] = set()
    sql_list: list[str] = []
    if rule_type == "reconciliation":
        allowed = {"left_sql", "right_sql"}
        for key in ("left_sql", "right_sql"):
            sql = str(expression.get(key) or "").strip()
            if not sql:
                raise AppError(422, "QUALITY_RULE_INVALID", f"{key} 不能为空")
            sql_list.append(sql)
    elif rule_type == "range":
        allowed = {"sql", "min", "max"}
        sql = str(expression.get("sql") or "").strip()
        if not sql:
            raise AppError(422, "QUALITY_RULE_INVALID", "sql 不能为空")
        sql_list.append(sql)
        minimum = expression.get("min")
        maximum = expression.get("max")
        if minimum is None and maximum is None:
            raise AppError(422, "QUALITY_RULE_INVALID", "range 规则必须提供 min 或 max")
        for key, value in (("min", minimum), ("max", maximum)):
            if value is not None and not isinstance(value, (int, float)):
                raise AppError(422, "QUALITY_RULE_INVALID", f"{key} 必须为数值")
    else:
        allowed = {"sql", "operator", "expected"}
        sql = str(expression.get("sql") or "").strip()
        if not sql:
            raise AppError(422, "QUALITY_RULE_INVALID", "sql 不能为空")
        sql_list.append(sql)
        operator = str(expression.get("operator") or "")
        if operator not in OPERATORS:
            raise AppError(422, "QUALITY_RULE_INVALID", "operator 非法")
        expected = expression.get("expected")
        if not isinstance(expected, (int, float)):
            raise AppError(422, "QUALITY_RULE_INVALID", "expected 必须为数值")
    unknown = sorted(set(expression) - allowed)
    if unknown:
        raise AppError(422, "QUALITY_RULE_INVALID", f"expression 不支持字段: {', '.join(unknown)}")
    if len(sql_list) == 1 and len(sql_list[0]) > 4000:
        raise AppError(422, "QUALITY_RULE_INVALID", "SQL 超过 4000 字符")

    try:
        staging_tables = _staging_allowed_tables() if target_db == "staging" else frozenset()
    except AppError:
        if target_db == "staging":
            raise
        staging_tables = frozenset()
    for sql in sql_list:
        guarded = _guard_rule_sql(sql, target_db, staging_tables)
        if not guarded.ok:
            raise AppError(422, "QUALITY_RULE_INVALID", f"SQL 未通过守卫：{guarded.reason}")
        try:
            if target_db == "staging":
                conn = staging_readonly_connection(staging_tables)
            else:
                conn = _business_connection()
            try:
                _execute_scalar(conn, guarded.sql, _params_for(_latest_period()))
            finally:
                conn.close()
        except AppError as exc:
            if exc.code == "DB_UNAVAILABLE":
                continue
            raise AppError(422, "QUALITY_RULE_INVALID", f"SQL 试执行失败：{exc.message}") from exc
        except sqlite3.Error as exc:
            message = str(exc)
            if "no such table" in message and target_db == "staging":
                continue
            raise AppError(422, "QUALITY_RULE_INVALID", f"SQL 试执行失败：{message}") from exc
        except ValueError as exc:
            raise AppError(422, "QUALITY_RULE_INVALID", f"SQL 试执行失败：{exc}") from exc


def _validate_rule_payload(
    payload: dict[str, Any], *, creating: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    allowed = {
        "code",
        "name",
        "rule_type",
        "target_db",
        "target_ref",
        "expression",
        "tolerance",
        "severity",
        "enabled",
        "schedule",
        "owner",
        "description",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AppError(422, "VALIDATION_FAILED", f"不支持的字段: {', '.join(unknown)}")
    if creating:
        code = str(payload.get("code") or "").strip()
        if not _CODE_PATTERN.match(code):
            raise AppError(422, "VALIDATION_FAILED", "code 必须为小写字母开头，长度 2-64")
        if not str(payload.get("name") or "").strip():
            raise AppError(422, "VALIDATION_FAILED", "name 不能为空")
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 100:
            raise AppError(422, "VALIDATION_FAILED", "name 长度必须为 1-100")
    rule_type = str(payload.get("rule_type") or "")
    target_db = str(payload.get("target_db") or "")
    if creating and (not rule_type or not target_db):
        raise AppError(422, "VALIDATION_FAILED", "rule_type 与 target_db 不能为空")
    expression = payload.get("expression")
    if creating and expression is None:
        raise AppError(422, "VALIDATION_FAILED", "expression 不能为空")
    severity = str(payload.get("severity") or "warning")
    schedule = str(payload.get("schedule") or "report")
    if severity not in SEVERITIES:
        raise AppError(422, "VALIDATION_FAILED", "severity 非法")
    if schedule not in SCHEDULES:
        raise AppError(422, "VALIDATION_FAILED", "schedule 非法")
    tolerance = float(payload.get("tolerance", 0.01))
    target_ref = str(payload.get("target_ref") or "")
    if len(target_ref) > 128:
        raise AppError(422, "VALIDATION_FAILED", "target_ref 长度不能超过 128")
    for field, max_length in (("owner", 100), ("description", 500)):
        if len(str(payload.get(field) or "")) > max_length:
            raise AppError(422, "VALIDATION_FAILED", f"{field} 长度超限")
    cleaned = {
        **payload,
        "rule_type": rule_type,
        "target_db": target_db,
        "target_ref": target_ref,
        "severity": severity,
        "schedule": schedule,
        "tolerance": tolerance,
        "owner": str(payload.get("owner") or ""),
        "description": str(payload.get("description") or ""),
        "enabled": 1 if payload.get("enabled", True) else 0,
    }
    return cleaned, {"expression": expression or {}}


def _get_rule(code: str) -> dict[str, Any]:
    with staging_connection() as conn:
        row = conn.execute(
            f"SELECT {_RULE_COLUMNS} FROM quality_rule WHERE code = :code", {"code": code}
        ).fetchone()
    if row is None:
        raise AppError(404, "QUALITY_RULE_NOT_FOUND", f"质量规则不存在: {code}")
    return _rule(row)


def get_rule(code: str) -> dict[str, Any]:
    """按 code 获取质量规则；不存在时抛 404 QUALITY_RULE_NOT_FOUND。"""
    ensure_staging_available()
    return _get_rule(code)


def create_rule(payload: dict[str, Any], role: str) -> dict[str, Any]:
    cleaned, extra = _validate_rule_payload(payload, creating=True)
    expression = extra["expression"]
    _validate_expression(cleaned["rule_type"], cleaned["target_db"], expression, cleaned["tolerance"])
    ensure_staging_available()
    now = staging_now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT id FROM quality_rule WHERE code = :code", {"code": cleaned["code"]}
            ).fetchone()
            if existing:
                raise AppError(409, "VALIDATION_FAILED", f"规则编码已存在: {cleaned['code']}")
            conn.execute(
                """INSERT INTO quality_rule
                   (code, name, rule_type, target_db, target_ref, expression_json,
                    tolerance, severity, enabled, schedule, owner, description,
                    created_at, updated_at, updated_by)
                   VALUES (:code, :name, :rule_type, :target_db, :target_ref, :expression_json,
                           :tolerance, :severity, :enabled, :schedule, :owner, :description,
                           :now, :now, :role)""",
                {
                    **cleaned,
                    "expression_json": json.dumps(expression, ensure_ascii=False),
                    "now": now,
                    "role": role,
                },
            )
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return _get_rule(cleaned["code"])


def update_rule(code: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
    current = _get_rule(code)
    merged_payload = {
        "name": current["name"],
        "rule_type": current["rule_type"],
        "target_db": current["target_db"],
        "target_ref": current["target_ref"],
        "expression": current["expression"],
        "tolerance": current["tolerance"],
        "severity": current["severity"],
        "enabled": current["enabled"],
        "schedule": current["schedule"],
        "owner": current["owner"],
        "description": current["description"],
        **payload,
    }
    cleaned, extra = _validate_rule_payload(merged_payload, creating=False)
    _validate_expression(cleaned["rule_type"], cleaned["target_db"], extra["expression"], cleaned["tolerance"])
    ensure_staging_available()
    now = staging_now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """UPDATE quality_rule
                   SET name = :name, rule_type = :rule_type, target_db = :target_db,
                       target_ref = :target_ref, expression_json = :expression_json,
                       tolerance = :tolerance, severity = :severity, enabled = :enabled,
                       schedule = :schedule, owner = :owner, description = :description,
                       updated_at = :now, updated_by = :role
                   WHERE code = :code""",
                {
                    **cleaned,
                    "code": code,
                    "expression_json": json.dumps(extra["expression"], ensure_ascii=False),
                    "now": now,
                    "role": role,
                },
            )
            if cursor.rowcount == 0:
                raise AppError(404, "QUALITY_RULE_NOT_FOUND", f"质量规则不存在: {code}")
            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise
    return _get_rule(code)


def list_runs(filters: dict[str, Any]) -> dict[str, Any]:
    ensure_staging_available()
    where: list[str] = []
    params: dict[str, Any] = {}
    for field in ("status", "trigger_type"):
        value = filters.get(field)
        if value:
            where.append(f"{field} = :{field}")
            params[field] = str(value)
    limit = max(1, min(int(filters.get("limit") or 20), 100))
    offset = max(0, int(filters.get("offset") or 0))
    params["limit"] = limit
    params["offset"] = offset
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with staging_connection() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM quality_run {clause}", params
        ).fetchone()
        rows = conn.execute(
            f"""SELECT {_RUN_COLUMNS} FROM quality_run {clause}
                ORDER BY started_at DESC, run_id DESC LIMIT :limit OFFSET :offset""",
            params,
        ).fetchall()
    return {
        "items": [_run(row) for row in rows],
        "total": int(total_row["n"]) if total_row else 0,
    }


def get_run(run_id: str) -> dict[str, Any]:
    ensure_staging_available()
    with staging_connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM quality_run WHERE run_id = :run_id",
            {"run_id": run_id},
        ).fetchone()
        if row is None:
            raise AppError(404, "QUALITY_RUN_NOT_FOUND", f"质量运行不存在: {run_id}")
        results = conn.execute(
            f"SELECT {_RESULT_COLUMNS} FROM quality_result WHERE run_id = :run_id ORDER BY id",
            {"run_id": run_id},
        ).fetchall()
    return {**_run(row), "results": [_result(item) for item in results]}


def list_alerts(days: int, limit: int) -> dict[str, Any]:
    ensure_staging_available()
    safe_days = max(1, min(int(days or 7), 365))
    safe_limit = max(1, min(int(limit or 20), 200))
    cutoff = _cutoff_iso(safe_days)
    with staging_connection() as conn:
        rows = conn.execute(
            f"""SELECT {', '.join('r.' + item.strip() for item in _RESULT_COLUMNS.split(','))},
                       q.trigger_type AS trigger_type, q.period_code AS period_code,
                       q.started_at AS run_started_at
                FROM quality_result r
                JOIN quality_run q ON q.run_id = r.run_id
                WHERE r.status IN ('failed','error') AND r.created_at >= :cutoff
                ORDER BY r.id DESC LIMIT :limit""",
            {"cutoff": cutoff, "limit": safe_limit},
        ).fetchall()
    items = []
    for row in rows:
        item = _result(row)
        item["run"] = {
            "run_id": row["run_id"],
            "trigger_type": row["trigger_type"],
            "period_code": row["period_code"],
            "started_at": row["run_started_at"],
        }
        items.append(item)
    return {"items": items}
