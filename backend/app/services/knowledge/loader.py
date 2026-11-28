"""知识包加载器：扫描 JSON 包、逐条校验、单事务幂等 upsert。

任何校验失败都整批拒绝（旧库保持原状），并 best-effort 记录
``knowledge_meta.last_load_error``。校验通过的整批写入在单个
``BEGIN IMMEDIATE`` 事务内完成，失败回滚。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.common import AppError
from app.services.knowledge import store as knowledge_store

logger = logging.getLogger("finance_ontology.knowledge.loader")

KIND_VALUES: frozenset[str] = frozenset(
    {"term", "process", "metric_basis", "reconciliation_rule", "terminology", "correction"}
)
DOMAIN_VALUES: frozenset[str] = frozenset(
    {"accounting_reconciliation", "business_finance_integration"}
)
CONFIDENCE_VALUES: frozenset[str] = frozenset({"standard", "opensource", "practice"})
SOURCE_TYPE_VALUES: frozenset[str] = frozenset({"standard", "opensource", "practice"})

REQUIRED_ENTRY_FIELDS: tuple[str, ...] = (
    "id",
    "kind",
    "domain",
    "title",
    "statement",
    "basis",
    "source_id",
    "version",
    "effective_from",
    "confidence",
)
REQUIRED_SOURCE_FIELDS: tuple[str, ...] = ("source_id", "name", "type", "license", "version")

_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_ENTRY_ID_RE = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")
_WHITESPACE_RE = re.compile(r"\s+")

_MAX_ERROR_VIOLATIONS = 20
_MAX_ERROR_MESSAGE_CHARS = 200


class PackValidationError(AppError):
    """知识包校验失败（422），携带逐条 violations 供 API 返回。"""

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        super().__init__(
            422,
            "KNOWLEDGE_VALIDATION_FAILED",
            "知识包校验失败",
            {"violations": violations},
        )
        self.violations = violations


def _now() -> str:
    """知识库统一时间戳：带时区、秒级 ISO 8601。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _violation(
    file_name: str, entry_id: str | None, field: str, message: str
) -> dict[str, Any]:
    return {"file": file_name, "entry_id": entry_id, "field": field, "message": message}


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _pack_stamp(pack_dir: Path) -> str:
    """文件名:mtime_ns:size 逐文件串接后的 sha256，用于跳过未变化加载。"""
    digest = hashlib.sha256()
    for path in sorted(pack_dir.glob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}\n".encode("utf-8"))
    return digest.hexdigest()


def _validate_source(src: dict[str, Any]) -> list[dict[str, Any]]:
    """来源必填、枚举与 opensource notice 规则。"""
    violations: list[dict[str, Any]] = []
    for field in REQUIRED_SOURCE_FIELDS:
        if not isinstance(src.get(field), str) or not src[field].strip():
            violations.append(_violation("", None, field, f"来源缺少必填字段: {field}"))
    source_id = src.get("source_id")
    if isinstance(source_id, str) and source_id and not _SOURCE_ID_RE.match(source_id):
        violations.append(
            _violation(
                "",
                None,
                "source_id",
                "source_id 必须匹配 ^[a-z][a-z0-9-]{2,63}$",
            )
        )
    source_type = src.get("type")
    if isinstance(source_type, str) and source_type and source_type not in SOURCE_TYPE_VALUES:
        violations.append(
            _violation("", None, "type", "type 必须为 standard/opensource/practice 之一")
        )
    if source_type == "opensource" and not _as_text(src.get("notice")).strip():
        violations.append(
            _violation("", None, "notice", "opensource 来源必须提供非空 notice 版权声明")
        )
    return violations


def _validate_sql_field(
    payload: dict[str, Any], field: str, violations: list[dict[str, Any]], entry_id: str | None
) -> str:
    sql = payload.get(field)
    if not isinstance(sql, str) or not sql.strip():
        violations.append(_violation("", entry_id, f"payload.{field}", f"{field} 必填且为非空字符串"))
        return ""
    if ";" in sql:
        violations.append(_violation("", entry_id, f"payload.{field}", f"{field} 不允许包含分号（禁止多语句）"))
    condensed = _WHITESPACE_RE.sub(" ", sql).strip().upper()
    if "SELECT *" in condensed:
        violations.append(_violation("", entry_id, f"payload.{field}", f"{field} 禁止 SELECT *"))
    if ":start_period" not in sql:
        violations.append(_violation("", entry_id, f"payload.{field}", f"{field} 必须包含 :start_period 参数"))
    return sql


def _validate_payload(entry: dict[str, Any], violations: list[dict[str, Any]]) -> None:
    """按 kind 校验 payload；通用必填字段不在此检查。"""
    raw_payload = entry.get("payload")
    if raw_payload is not None and not isinstance(raw_payload, dict):
        violations.append(_violation("", _entry_id(entry), "payload", "payload 必须是 JSON 对象"))
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    kind = entry.get("kind")
    entry_id = _entry_id(entry)

    if kind == "reconciliation_rule":
        _validate_sql_field(payload, "left_sql", violations, entry_id)
        _validate_sql_field(payload, "right_sql", violations, entry_id)
    elif kind == "metric_basis":
        if not isinstance(payload.get("metric_code"), str) or not payload["metric_code"].strip():
            violations.append(
                _violation("", entry_id, "payload.metric_code", "metric_basis 必须提供 metric_code")
            )
    elif kind == "terminology":
        phrase = payload.get("phrase")
        if not isinstance(phrase, str) or not phrase.strip():
            violations.append(_violation("", entry_id, "payload.phrase", "terminology 必须提供 phrase"))
        elif not 2 <= len(phrase.strip()) <= 8:
            violations.append(_violation("", entry_id, "payload.phrase", "phrase 长度必须为 2~8 个字符"))
        target_kind = payload.get("target_kind")
        if target_kind not in ("concept", "metric"):
            violations.append(
                _violation("", entry_id, "payload.target_kind", "target_kind 必须为 concept 或 metric")
            )
        if not isinstance(payload.get("target_code"), str) or not payload["target_code"].strip():
            violations.append(
                _violation("", entry_id, "payload.target_code", "terminology 必须提供 target_code")
            )
    elif kind == "correction":
        if not isinstance(payload.get("account_code"), str) or not payload["account_code"].strip():
            violations.append(
                _violation("", entry_id, "payload.account_code", "correction 必须提供 account_code")
            )
    elif kind in ("term", "process"):
        if isinstance(payload, dict) and "term_code" in payload and not isinstance(
            payload.get("term_code"), str
        ):
            violations.append(
                _violation("", entry_id, "payload.term_code", "term_code 必须是字符串")
            )


def _entry_id(entry: dict[str, Any]) -> str | None:
    raw = entry.get("id")
    return raw if isinstance(raw, str) and raw else None


def _validate_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """通用 10 字段、id 正则、枚举与 kind payload 校验。

    来源存在性统一由 ``_validate_cross_references`` 检查，避免重复报错。
    """
    violations: list[dict[str, Any]] = []
    entry_id = _entry_id(entry)
    for field in REQUIRED_ENTRY_FIELDS:
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            violations.append(_violation("", entry_id, field, f"条目缺少必填字段: {field}"))
    if entry_id is not None and not _ENTRY_ID_RE.match(entry_id):
        violations.append(
            _violation("", entry_id, "id", "id 必须匹配 ^[a-z][a-z0-9_.]{2,63}$")
        )
    kind = entry.get("kind")
    if isinstance(kind, str) and kind and kind not in KIND_VALUES:
        violations.append(
            _violation("", entry_id, "kind", "kind 必须为 term/process/metric_basis/reconciliation_rule/terminology/correction 之一")
        )
    domain = entry.get("domain")
    if isinstance(domain, str) and domain and domain not in DOMAIN_VALUES:
        violations.append(
            _violation("", entry_id, "domain", "domain 必须为 accounting_reconciliation/business_finance_integration 之一")
        )
    confidence = entry.get("confidence")
    if isinstance(confidence, str) and confidence and confidence not in CONFIDENCE_VALUES:
        violations.append(
            _violation("", entry_id, "confidence", "confidence 必须为 standard/opensource/practice 之一")
        )
    _validate_payload(entry, violations)
    return violations


def _validate_cross_references(
    entries: list[tuple[str, dict[str, Any]]] | list[dict[str, Any]],
    source_ids: set[str],
    glossary_ids: set[str],
) -> list[dict[str, Any]]:
    """来源引用、条目 id 全局唯一与 terminology→concept 目标存在性。

    ``entries`` 支持 ``(文件, 条目)`` 二元组或裸条目两种形式，便于直接复用。
    """
    violations: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}
    for item in entries:
        if isinstance(item, tuple):
            file_name, entry = item[0], item[1]
        else:
            file_name, entry = "", item
        entry_id = _entry_id(entry)
        source_id = entry.get("source_id")
        if isinstance(source_id, str) and source_id and source_id not in source_ids:
            violations.append(
                _violation(file_name, entry_id, "source_id", f"引用的来源不存在: {source_id}")
            )
        if entry_id is not None:
            if entry_id in seen_ids:
                violations.append(
                    _violation(
                        file_name,
                        entry_id,
                        "id",
                        f"条目 id 重复（首次出现于 {seen_ids[entry_id] or '<pack>'}）",
                    )
                )
            else:
                seen_ids[entry_id] = file_name
        if entry.get("kind") == "terminology":
            payload = entry.get("payload")
            if isinstance(payload, dict) and payload.get("target_kind") == "concept":
                target_code = payload.get("target_code")
                if (
                    isinstance(target_code, str)
                    and target_code
                    and target_code not in glossary_ids
                ):
                    violations.append(
                        _violation(
                            file_name,
                            entry_id,
                            "payload.target_code",
                            f"terminology 指向的概念条目不存在: {target_code}",
                        )
                    )
    return violations


def _record_error_best_effort(violations: list[dict[str, Any]]) -> None:
    """独立连接写 last_load_error（最多 20 条脱敏摘要），失败忽略。"""
    summary = []
    for violation in violations[:_MAX_ERROR_VIOLATIONS]:
        text = "{}:{}:{}:{}".format(
            violation.get("file") or "<pack>",
            violation.get("entry_id") or "-",
            violation.get("field") or "-",
            violation.get("message") or "-",
        )
        summary.append(_WHITESPACE_RE.sub(" ", text)[:_MAX_ERROR_MESSAGE_CHARS])
    payload = "\n".join(summary)
    try:
        with knowledge_store.knowledge_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO knowledge_meta (key, value, updated_at)
                       VALUES (:key, :value, :updated_at)
                       ON CONFLICT(key) DO UPDATE SET
                           value = excluded.value,
                           updated_at = excluded.updated_at""",
                    {"key": "last_load_error", "value": payload, "updated_at": _now()},
                )
                conn.execute("COMMIT")
            except sqlite3.Error:
                knowledge_store._safe_rollback(conn)
                raise
    except (sqlite3.Error, OSError):
        logger.debug("failed to persist knowledge last_load_error", exc_info=True)


def _db_counts() -> dict[str, Any]:
    """从只读连接读取当前知识库计数与版本；失败返回零值。"""
    counts: dict[str, Any] = {
        "entries": 0,
        "sources": 0,
        "domains": 0,
        "pack_version": "",
    }
    try:
        conn = knowledge_store.knowledge_read_connection()
    except (sqlite3.Error, OSError):
        return counts
    try:
        counts["entries"] = int(
            conn.execute("SELECT COUNT(*) AS n FROM knowledge_entry").fetchone()["n"]
        )
        counts["sources"] = int(
            conn.execute("SELECT COUNT(*) AS n FROM knowledge_source").fetchone()["n"]
        )
        counts["domains"] = int(
            conn.execute(
                "SELECT COUNT(DISTINCT domain) AS n FROM knowledge_entry"
            ).fetchone()["n"]
        )
        row = conn.execute(
            "SELECT value FROM knowledge_meta WHERE key = :key",
            {"key": "knowledge_pack_version"},
        ).fetchone()
        counts["pack_version"] = str(row["value"]) if row else ""
    except sqlite3.Error:
        logger.debug("failed to read knowledge counts", exc_info=True)
    finally:
        conn.close()
    return counts


def _stored_stamp() -> str | None:
    """读取已加载包摘要；失败返回 None（视为需要加载）。"""
    try:
        with knowledge_store.knowledge_connection() as conn:
            row = conn.execute(
                "SELECT value FROM knowledge_meta WHERE key = :key",
                {"key": "pack_stamp"},
            ).fetchone()
            return str(row["value"]) if row else None
    except sqlite3.Error:
        return None


def _upsert_sources(conn: sqlite3.Connection, sources: list[dict[str, Any]], now: str) -> None:
    sql = """INSERT INTO knowledge_source
                 (source_id, name, type, license, url, notice, version, updated_at)
             VALUES
                 (:source_id, :name, :type, :license, :url, :notice, :version, :updated_at)
             ON CONFLICT(source_id) DO UPDATE SET
                 name = excluded.name,
                 type = excluded.type,
                 license = excluded.license,
                 url = excluded.url,
                 notice = excluded.notice,
                 version = excluded.version,
                 updated_at = excluded.updated_at"""
    for source in sources:
        conn.execute(
            sql,
            {
                "source_id": source["source_id"],
                "name": source["name"],
                "type": source["type"],
                "license": source["license"],
                "url": _as_text(source.get("url")),
                "notice": _as_text(source.get("notice")),
                "version": source["version"],
                "updated_at": now,
            },
        )


def _upsert_packs(
    conn: sqlite3.Connection,
    packs: list[dict[str, Any]],
    now: str,
) -> None:
    sql = """INSERT INTO knowledge_pack
                 (pack_id, domain, pack_version, file_name, entry_count, status, loaded_at)
             VALUES
                 (:pack_id, :domain, :pack_version, :file_name, :entry_count, 'loaded', :loaded_at)
             ON CONFLICT(pack_id) DO UPDATE SET
                 domain = excluded.domain,
                 pack_version = excluded.pack_version,
                 file_name = excluded.file_name,
                 entry_count = excluded.entry_count,
                 status = excluded.status,
                 loaded_at = excluded.loaded_at"""
    for pack in packs:
        # 同一文件改名 pack_id 时先清掉旧主键，避免 file_name UNIQUE 冲突。
        conn.execute(
            "DELETE FROM knowledge_pack WHERE file_name = :file_name AND pack_id <> :pack_id",
            {"file_name": pack["file_name"], "pack_id": pack["pack_id"]},
        )
        conn.execute(
            sql,
            {
                "pack_id": pack["pack_id"],
                "domain": pack["domain"],
                "pack_version": pack["pack_version"],
                "file_name": pack["file_name"],
                "entry_count": pack["entry_count"],
                "loaded_at": now,
            },
        )


def _upsert_entries(
    conn: sqlite3.Connection,
    entries: list[tuple[str, dict[str, Any]]],
    now: str,
) -> None:
    sql = """INSERT INTO knowledge_entry
                 (entry_id, kind, domain, title, statement, basis, source_id,
                  version, effective_from, confidence, payload_json, pack_version, loaded_at)
             VALUES
                 (:entry_id, :kind, :domain, :title, :statement, :basis, :source_id,
                  :version, :effective_from, :confidence, :payload_json, :pack_version, :loaded_at)
             ON CONFLICT(entry_id) DO UPDATE SET
                 kind = excluded.kind,
                 domain = excluded.domain,
                 title = excluded.title,
                 statement = excluded.statement,
                 basis = excluded.basis,
                 source_id = excluded.source_id,
                 version = excluded.version,
                 effective_from = excluded.effective_from,
                 confidence = excluded.confidence,
                 payload_json = excluded.payload_json,
                 pack_version = excluded.pack_version,
                 loaded_at = excluded.loaded_at"""
    for _file_name, entry in entries:
        payload = entry.get("payload")
        payload_json = json.dumps(
            payload if isinstance(payload, dict) else {},
            ensure_ascii=False,
            sort_keys=True,
        )
        conn.execute(
            sql,
            {
                "entry_id": entry["id"],
                "kind": entry["kind"],
                "domain": entry["domain"],
                "title": entry["title"],
                "statement": entry["statement"],
                "basis": entry["basis"],
                "source_id": entry["source_id"],
                "version": entry["version"],
                "effective_from": entry["effective_from"],
                "confidence": entry["confidence"],
                "payload_json": payload_json,
                "pack_version": _as_text(entry.get("_pack_version")),
                "loaded_at": now,
            },
        )
        _replace_links(conn, entry)


def _delete_orphan_entries(
    conn: sqlite3.Connection, entries: list[tuple[str, dict[str, Any]]]
) -> int:
    """删除本次包中已不存在的条目（含其 links），返回删除条数。

    知识包采用全量声明语义：从 JSON 撤下的条目必须同步从库中移除，
    否则被撤下的错误规则仍会参与勾稽与指标依据。
    """
    incoming = {entry["id"] for _file_name, entry in entries}
    existing = {
        str(row["entry_id"])
        for row in conn.execute("SELECT entry_id FROM knowledge_entry").fetchall()
    }
    orphans = sorted(existing - incoming)
    for entry_id in orphans:
        conn.execute("DELETE FROM knowledge_link WHERE entry_id = :entry_id", {"entry_id": entry_id})
        conn.execute("DELETE FROM knowledge_entry WHERE entry_id = :entry_id", {"entry_id": entry_id})
    if orphans:
        logger.info("knowledge orphan entries removed: count=%s ids=%s", len(orphans), orphans[:10])
    return len(orphans)


def _replace_links(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    entry_id = entry["id"]
    conn.execute(
        "DELETE FROM knowledge_link WHERE entry_id = :entry_id", {"entry_id": entry_id}
    )
    links = entry.get("links")
    if not isinstance(links, list):
        return
    for link in links:
        if not isinstance(link, dict):
            continue
        target_kind = link.get("target_kind")
        target_code = link.get("target_code")
        if not isinstance(target_kind, str) or not target_kind:
            continue
        if not isinstance(target_code, str) or not target_code:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO knowledge_link (entry_id, target_kind, target_code)
               VALUES (:entry_id, :target_kind, :target_code)""",
            {
                "entry_id": entry_id,
                "target_kind": target_kind,
                "target_code": target_code,
            },
        )


def _write_meta(conn: sqlite3.Connection, values: dict[str, str], now: str) -> None:
    sql = """INSERT INTO knowledge_meta (key, value, updated_at)
             VALUES (:key, :value, :updated_at)
             ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = excluded.updated_at"""
    for key, value in values.items():
        conn.execute(sql, {"key": key, "value": value, "updated_at": now})


def _parse_pack_files(
    files: list[Path],
) -> tuple[
    list[tuple[str, dict[str, Any]]],
    list[tuple[str, str, dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str,
]:
    """解析所有 JSON 包，返回 (sources, entries, packs, violations, pack_version)。"""
    sources: list[tuple[str, dict[str, Any]]] = []
    entries: list[tuple[str, str, dict[str, Any]]] = []
    packs: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    version_candidates: list[str] = []

    for path in files:
        file_name = path.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            violations.append(
                _violation(file_name, None, "<json>", f"JSON 解析失败: {exc.msg} (line {exc.lineno})")
            )
            continue
        except OSError:
            violations.append(_violation(file_name, None, "<file>", "知识包文件不可读"))
            continue
        if not isinstance(data, dict):
            violations.append(_violation(file_name, None, "<json>", "知识包根节点必须是 JSON 对象"))
            continue

        pack_id = _as_text(data.get("pack")) or path.stem
        pack_version = _as_text(data.get("pack_version"))
        domain = _as_text(data.get("domain"))
        if pack_version:
            version_candidates.append(pack_version)

        if path.stem == "sources":
            sources.extend(_collect_sources(file_name, data.get("sources"), violations))
            file_entry_count = 0
        else:
            raw_entries = data.get("entries")
            file_entry_count = 0
            if not isinstance(raw_entries, list):
                violations.append(
                    _violation(file_name, None, "entries", "知识包必须包含 entries 数组")
                )
            else:
                for entry in raw_entries:
                    if isinstance(entry, dict):
                        entries.append((file_name, pack_version, entry))
                        file_entry_count += 1
                    else:
                        violations.append(
                            _violation(file_name, None, "entries", "条目项必须是 JSON 对象")
                        )

        packs.append(
            {
                "pack_id": pack_id,
                "domain": domain,
                "pack_version": pack_version,
                "file_name": file_name,
                "entry_count": file_entry_count,
            }
        )

    pack_version = version_candidates[0] if version_candidates else ""
    return sources, entries, packs, violations, pack_version


def _collect_sources(
    file_name: str,
    raw_sources: Any,
    violations: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """收集来源项；非数组或非对象元素记为 violation。"""
    if not isinstance(raw_sources, list):
        violations.append(_violation(file_name, None, "sources", "sources 必须是数组"))
        return []
    collected: list[tuple[str, dict[str, Any]]] = []
    for source in raw_sources:
        if isinstance(source, dict):
            collected.append((file_name, source))
        else:
            violations.append(_violation(file_name, None, "sources", "来源项必须是 JSON 对象"))
    return collected


def load_knowledge_packs(force: bool = False) -> dict[str, Any]:
    """加载 ``settings.knowledge_pack_dir`` 下全部知识包。

    校验失败抛 :class:`PackValidationError`（整批拒绝）；成功返回
    ``{loaded, skipped, pack_version, entries, sources, domains, duration_ms}``。
    """
    started = time.perf_counter()
    knowledge_store.init_knowledge()
    pack_dir = settings.knowledge_pack_dir
    stamp = _pack_stamp(pack_dir)

    if not force and _stored_stamp() == stamp:
        counts = _db_counts()
        return {
            "loaded": False,
            "skipped": True,
            "pack_version": counts["pack_version"],
            "entries": counts["entries"],
            "sources": counts["sources"],
            "domains": counts["domains"],
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    files = sorted(pack_dir.glob("*.json"))
    sources, raw_entries, packs, violations, pack_version = _parse_pack_files(files)

    valid_sources: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for file_name, source in sources:
        source_violations = _validate_source(source)
        if source_violations:
            for violation in source_violations:
                violation["file"] = file_name
            violations.extend(source_violations)
            continue
        valid_sources.append(source)
        source_ids.add(source["source_id"])

    entry_pairs: list[tuple[str, dict[str, Any]]] = []
    for file_name, entry_pack_version, entry in raw_entries:
        entry["_pack_version"] = entry_pack_version
        entry_pairs.append((file_name, entry))
        entry_violations = _validate_entry(entry)
        for violation in entry_violations:
            violation["file"] = file_name
        violations.extend(entry_violations)

    glossary_ids = {
        entry["id"]
        for _file_name, entry in entry_pairs
        if entry.get("kind") in ("term", "process") and _entry_id(entry) is not None
    }
    violations.extend(_validate_cross_references(entry_pairs, source_ids, glossary_ids))

    # 目录误配/为空时若照常"成功"加载，会写清零条目并清空版本，旧数据继续对外可见；
    # 这里显式整批拒绝，保证库内保持上一次成功状态。
    if not files:
        _record_error_best_effort(
            [_violation("<pack>", None, "entries", f"知识包目录为空或不存在: {pack_dir}")]
        )
        raise PackValidationError(
            [
                _violation(
                    "<pack>", None, "entries", f"知识包目录为空或不存在: {pack_dir}"
                )
            ]
        )
    if not entry_pairs:
        _record_error_best_effort(
            [_violation("<pack>", None, "entries", "知识包未解析出任何条目")]
        )
        raise PackValidationError(
            [_violation("<pack>", None, "entries", "知识包未解析出任何条目")]
        )

    if violations:
        _record_error_best_effort(violations)
        raise PackValidationError(violations)

    now = _now()
    removed = 0
    prior_version = _db_counts()["pack_version"]
    with knowledge_store.knowledge_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _upsert_sources(conn, valid_sources, now)
            _upsert_packs(conn, packs, now)
            _upsert_entries(conn, entry_pairs, now)
            removed = _delete_orphan_entries(conn, entry_pairs)
            _write_meta(
                conn,
                {
                    # 包未声明版本时保留既有版本，避免成功加载把版本清空。
                    "knowledge_pack_version": pack_version or prior_version,
                    "pack_stamp": stamp,
                    "last_loaded_at": now,
                    "last_load_error": "",
                },
                now,
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            knowledge_store._safe_rollback(conn)
            raise

    from app.services.knowledge import service as knowledge_service

    knowledge_service.invalidate_snapshot()

    counts = _db_counts()
    logger.info(
        "knowledge packs loaded: files=%s entries=%s sources=%s domains=%s removed=%s pack_version=%s",
        len(files),
        counts["entries"],
        counts["sources"],
        counts["domains"],
        removed,
        counts["pack_version"] or pack_version,
    )
    return {
        "loaded": True,
        "skipped": False,
        "pack_version": counts["pack_version"] or pack_version,
        "entries": counts["entries"],
        "sources": counts["sources"],
        "domains": counts["domains"],
        "removed": removed,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
