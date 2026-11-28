"""知识只读门面：快照缓存、依据查询、术语检测与浏览查询。

所有对外函数都遵循降级原则：知识库缺失、损坏或任何 SQL 失败时返回空结构
（或 ``None`` / ``{}``），绝不把异常抛给业务调用方。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.schemas.common import AppError
from app.services.knowledge import store as knowledge_store

logger = logging.getLogger("finance_ontology.knowledge.service")

_PUNCT_RE = re.compile(
    r"[\s，。！？、；：,.!?;:'\"“”‘’（）()《》<>【】\[\]{}|/\\_\-—…~`·]+"
)
_DEFAULT_TOLERANCE = 0.01
_MAX_LAST_ERROR_CHARS = 1000
# 定义类问法的头尾噪声词，用于把「什么是X」「X是什么」归一为术语名 X。
_DEFINITION_STEM_RE = re.compile(
    r"^(?:请问|请解释|解释下)?(?:什么是|什么叫|何为|啥是|解释|定义|口径|含义)"
    r"|(?:是什么|是啥|什么意思|怎么理解|的定义|的含义|定义|含义|解释|口径)$"
)

_ENTRY_COLUMNS = (
    "entry_id",
    "kind",
    "domain",
    "title",
    "statement",
    "basis",
    "source_id",
    "version",
    "effective_from",
    "confidence",
    "payload_json",
    "pack_version",
    "loaded_at",
)
_SOURCE_COLUMNS = ("source_id", "name", "type", "license", "url", "notice", "version")


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """一次加载得到的内存只读快照。"""

    available: bool
    pack_version: str
    sources: dict[str, dict[str, Any]]
    entries: list[dict[str, Any]]
    by_kind: dict[str, list[dict[str, Any]]]
    by_metric: dict[str, dict[str, Any]]
    by_term: dict[str, dict[str, Any]]
    terminology: list[dict[str, Any]]
    loaded_at: str | None

    @classmethod
    def empty(cls) -> "KnowledgeSnapshot":
        """不可用时的全空快照。"""
        return cls(
            available=False,
            pack_version="",
            sources={},
            entries=[],
            by_kind={},
            by_metric={},
            by_term={},
            terminology=[],
            loaded_at=None,
        )


@dataclass(frozen=True)
class KnowledgeHit:
    """术语检测命中结果。"""

    entry_id: str
    title: str
    statement: str
    basis: str
    source_name: str
    source_url: str
    target_kind: str
    metric_code: str | None
    matched_phrase: str


_cache: KnowledgeSnapshot | None = None
_cache_stamp: tuple[str, int] | None = None


def _path_stamp() -> tuple[str, int]:
    path = settings.knowledge_db_path
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return (str(path), mtime)


def invalidate_snapshot() -> None:
    """reload 后失效内存快照。"""
    global _cache, _cache_stamp
    _cache = None
    _cache_stamp = None


def _load_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_snapshot(
    entry_rows: list[sqlite3.Row],
    source_rows: list[sqlite3.Row],
    meta: dict[str, str],
) -> KnowledgeSnapshot:
    sources: dict[str, dict[str, Any]] = {
        str(row["source_id"]): {column: row[column] for column in _SOURCE_COLUMNS}
        for row in source_rows
    }
    entries: list[dict[str, Any]] = []
    by_kind: dict[str, list[dict[str, Any]]] = {}
    by_metric: dict[str, dict[str, Any]] = {}
    by_term: dict[str, dict[str, Any]] = {}
    terminology: list[dict[str, Any]] = []

    for row in entry_rows:
        entry = {column: row[column] for column in _ENTRY_COLUMNS}
        entry["payload"] = _load_payload(row["payload_json"])
        source = sources.get(str(row["source_id"]), {})
        entry["source_name"] = str(source.get("name", ""))
        entry["source_url"] = str(source.get("url", ""))
        entry["source_type"] = str(source.get("type", ""))
        entries.append(entry)
        by_kind.setdefault(str(row["kind"]), []).append(entry)
        if row["kind"] == "metric_basis":
            metric_code = entry["payload"].get("metric_code")
            if isinstance(metric_code, str) and metric_code:
                by_metric.setdefault(metric_code, entry)
        if row["kind"] in ("term", "process"):
            term_code = entry["payload"].get("term_code")
            if isinstance(term_code, str) and term_code:
                by_term.setdefault(term_code, entry)
        if row["kind"] == "terminology":
            terminology.append(entry)

    return KnowledgeSnapshot(
        available=bool(entries),
        pack_version=meta.get("knowledge_pack_version", ""),
        sources=sources,
        entries=entries,
        by_kind=by_kind,
        by_metric=by_metric,
        by_term=by_term,
        terminology=terminology,
        loaded_at=meta.get("last_loaded_at") or None,
    )


def get_snapshot() -> KnowledgeSnapshot:
    """获取快照；库缺失或读取失败时返回空快照且绝不抛异常。"""
    global _cache, _cache_stamp
    stamp = _path_stamp()
    if _cache is not None and _cache_stamp == stamp:
        return _cache
    if not knowledge_store.knowledge_available():
        return KnowledgeSnapshot.empty()
    conn: sqlite3.Connection | None = None
    try:
        conn = knowledge_store.knowledge_read_connection()
        entry_rows = conn.execute(
            f"SELECT {', '.join(_ENTRY_COLUMNS)} FROM knowledge_entry ORDER BY kind, entry_id"
        ).fetchall()
        source_rows = conn.execute(
            f"SELECT {', '.join(_SOURCE_COLUMNS)} FROM knowledge_source ORDER BY source_id"
        ).fetchall()
        meta: dict[str, str] = {}
        for row in conn.execute(
            "SELECT key, value FROM knowledge_meta WHERE key IN "
            "('knowledge_pack_version','last_loaded_at','last_load_error')"
        ).fetchall():
            meta[str(row["key"])] = str(row["value"])
    except (sqlite3.Error, OSError, AppError):
        logger.warning("knowledge snapshot unavailable; falling back to empty", exc_info=True)
        return KnowledgeSnapshot.empty()
    finally:
        if conn is not None:
            conn.close()

    snapshot = _build_snapshot(entry_rows, source_rows, meta)
    if not snapshot.available:
        return KnowledgeSnapshot.empty()
    _cache = snapshot
    _cache_stamp = stamp
    return snapshot


def metric_basis(code: str) -> dict[str, Any]:
    """指标口径依据；无知识条目时返回空 dict。"""
    entry = get_snapshot().by_metric.get(code)
    if entry is None:
        return {}
    return {
        "basis": entry["basis"],
        "source_ref": entry["entry_id"],
        "source_name": entry["source_name"],
        "source_url": entry["source_url"],
        "entry_id": entry["entry_id"],
        "version": entry["version"],
    }


def term_enrichment(term_code: str) -> dict[str, Any] | None:
    """本体术语增强（同义词/描述/来源）；无增强时返回 None。"""
    entry = get_snapshot().by_term.get(term_code)
    if entry is None:
        return None
    payload = entry["payload"]
    raw_synonyms = payload.get("synonyms")
    synonyms = [item for item in raw_synonyms if isinstance(item, str)] if isinstance(
        raw_synonyms, list
    ) else []
    description = payload.get("description")
    return {
        "synonyms": synonyms,
        "description": description if isinstance(description, str) else "",
        "entry_id": entry["entry_id"],
        "basis": entry["basis"],
        "source_name": entry["source_name"],
        "source_url": entry["source_url"],
    }


def relation_enrichments() -> dict[str, dict[str, Any]]:
    """关系描述增强映射 ``{relation_code: {description, entry_id}}``；不可用时返回 {}。"""
    result: dict[str, dict[str, Any]] = {}
    for entry in get_snapshot().entries:
        if entry["kind"] not in ("term", "process"):
            continue
        relation_code = entry["payload"].get("relation_code")
        description = entry["payload"].get("description")
        if not isinstance(relation_code, str) or not relation_code:
            continue
        if not isinstance(description, str) or not description:
            continue
        result.setdefault(
            relation_code,
            {"description": description, "entry_id": entry["entry_id"]},
        )
    return result


def relation_enrichment(relation_code: str) -> dict[str, Any] | None:
    """关系描述增强；无增强时返回 None。"""
    return relation_enrichments().get(relation_code)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def reconciliation_rules() -> list[dict[str, Any]]:
    """归一化的勾稽规则列表；缺少 SQL 的条目跳过。"""
    rules: list[dict[str, Any]] = []
    for entry in get_snapshot().by_kind.get("reconciliation_rule", []):
        payload = entry["payload"]
        left_sql = payload.get("left_sql")
        right_sql = payload.get("right_sql")
        if not isinstance(left_sql, str) or not left_sql.strip():
            continue
        if not isinstance(right_sql, str) or not right_sql.strip():
            continue
        rules.append(
            {
                "entry_id": entry["entry_id"],
                "title": entry["title"],
                "statement": entry["statement"],
                "basis": entry["basis"],
                "source_id": entry["source_id"],
                "source_name": entry["source_name"],
                "left_sql": left_sql,
                "right_sql": right_sql,
                "tolerance": _to_float(payload.get("tolerance"), _DEFAULT_TOLERANCE),
            }
        )
    return rules


def _normalize(text: str) -> str:
    """小写、去空白与常见中英文标点，用于短语匹配。"""
    return _PUNCT_RE.sub("", (text or "").strip().lower())


# term/terminology 条目中可作为「术语名」参与定义匹配的短语上限，
# 过长的描述性文本不参与子串匹配，避免把整段陈述误当术语。
_MAX_TERM_PHRASE_CHARS = 16

_TERM_KINDS = ("term", "terminology")


def _definition_phrases(entry: dict[str, Any]) -> list[str]:
    """条目的可匹配术语名：title + payload 中的 phrase/synonyms。"""
    phrases: list[str] = []
    title = entry["title"]
    if isinstance(title, str) and title.strip():
        phrases.append(title.strip())
    payload = entry["payload"]
    phrase = payload.get("phrase")
    if isinstance(phrase, str) and phrase.strip():
        phrases.append(phrase.strip())
    synonyms = payload.get("synonyms")
    if isinstance(synonyms, list):
        phrases.extend(
            item.strip() for item in synonyms if isinstance(item, str) and item.strip()
        )
    return [
        item for item in dict.fromkeys(phrases) if len(item) <= _MAX_TERM_PHRASE_CHARS
    ]


def find_definition_entry(text: str, normalized: str | None = None) -> dict[str, Any] | None:
    """按问题文本匹配 kind 为 term/terminology 的知识条目，供定义意图兜底。

    匹配规则：术语名（title / payload.phrase / payload.synonyms）须完整出现在
    归一化问句中；先精确命中（问句去掉「什么是/口径/定义/含义/解释」等问法词后
    与术语名全等），再按术语名长度最长优先。命中 terminology 时若其 target 指向
    term/process 概念条目，则返回该概念条目（定义更完整），否则返回 terminology 本身。

    知识不可用、无命中或任何异常时返回 ``None``，绝不抛异常。
    """
    try:
        snapshot = get_snapshot()
    except Exception:  # noqa: BLE001 - 定义兜底不得影响问数主链路
        return None
    if not snapshot.available:
        return None
    haystack = _normalize(normalized if normalized is not None else text)
    if not haystack:
        return None
    by_id = {entry["entry_id"]: entry for entry in snapshot.entries}
    core = _DEFINITION_STEM_RE.sub("", haystack)

    best: tuple[int, int, dict[str, Any]] | None = None
    for kind in _TERM_KINDS:
        for entry in snapshot.by_kind.get(kind, []):
            for phrase in _definition_phrases(entry):
                needle = _normalize(phrase)
                if not needle or needle not in haystack:
                    continue
                exact = needle == core
                score = (1 if exact else 0, len(needle))
                if best is None or score > best[:2]:
                    best = (score[0], score[1], entry)
    if best is None:
        return None
    entry = best[2]
    if entry["kind"] == "terminology":
        target_code = entry["payload"].get("target_code")
        if entry["payload"].get("target_kind") == "concept" and isinstance(
            target_code, str
        ):
            concept = by_id.get(target_code)
            if concept is not None and concept["kind"] in ("term", "process"):
                entry = concept
    return {
        "entry_id": entry["entry_id"],
        "title": entry["title"],
        "statement": entry["statement"],
        "basis": entry["basis"],
        "source_name": entry["source_name"],
        "source_url": entry["source_url"],
        "version": entry["version"],
        "kind": entry["kind"],
    }


def _hit_from_entry(
    entry: dict[str, Any],
    matched_phrase: str,
    target_kind: str,
    metric_code: str | None,
) -> KnowledgeHit:
    return KnowledgeHit(
        entry_id=entry["entry_id"],
        title=entry["title"],
        statement=entry["statement"],
        basis=entry["basis"],
        source_name=entry["source_name"],
        source_url=entry["source_url"],
        target_kind=target_kind,
        metric_code=metric_code,
        matched_phrase=matched_phrase,
    )


def detect_terminology(normalized: str) -> KnowledgeHit | None:
    """最长短语匹配行业说法；``normalized`` 可传入任意问句文本。"""
    snapshot = get_snapshot()
    if not snapshot.available:
        return None
    haystack = _normalize(normalized)
    if not haystack:
        return None
    best_phrase = ""
    best_original = ""
    best_entry: dict[str, Any] | None = None
    for entry in snapshot.terminology:
        phrase = entry["payload"].get("phrase")
        if not isinstance(phrase, str):
            continue
        needle = _normalize(phrase)
        if not needle or needle not in haystack:
            continue
        if len(needle) > len(best_phrase):
            best_phrase = needle
            best_original = phrase.strip()
            best_entry = entry
    if best_entry is None:
        return None
    payload = best_entry["payload"]
    target_kind = payload.get("target_kind")
    target_code = payload.get("target_code")
    if target_kind == "concept":
        concept = next(
            (
                entry
                for entry in snapshot.entries
                if entry["entry_id"] == target_code and entry["kind"] in ("term", "process")
            ),
            None,
        )
        if concept is None:
            return None
        return _hit_from_entry(concept, best_original, "concept", None)
    if target_kind == "metric":
        return _hit_from_entry(best_entry, best_original, "metric", str(target_code or ""))
    return None


def _clamp_page(page: Any) -> int:
    try:
        return max(1, int(page))
    except (TypeError, ValueError):
        return 1


def _clamp_page_size(page_size: Any) -> int:
    try:
        return min(max(int(page_size), 1), 100)
    except (TypeError, ValueError):
        return 20


def _empty_page(page_size: int) -> dict[str, Any]:
    return {"items": [], "total": 0, "page": 1, "page_size": page_size}


def list_entries(
    q: str | None = None,
    domain: str | None = None,
    kind: str | None = None,
    source_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """分页浏览知识条目；失败或不可用时返回空页。"""
    page = _clamp_page(page)
    page_size = _clamp_page_size(page_size)
    if not settings.knowledge_db_path.exists():
        return _empty_page(page_size)

    where: list[str] = []
    params: dict[str, Any] = {}
    q_text = (q or "").strip()
    if q_text:
        where.append(
            "(e.title LIKE :q OR e.statement LIKE :q OR e.basis LIKE :q OR e.entry_id LIKE :q)"
        )
        params["q"] = f"%{q_text}%"
    for key, value in (("domain", domain), ("kind", kind), ("source_id", source_id)):
        if isinstance(value, str) and value.strip():
            where.append(f"e.{key} = :{key}")
            params[key] = value.strip()
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size

    conn: sqlite3.Connection | None = None
    try:
        conn = knowledge_store.knowledge_read_connection()
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM knowledge_entry e {clause}", params
            ).fetchone()["n"]
        )
        rows = conn.execute(
            f"""SELECT e.entry_id, e.kind, e.domain, e.title, e.statement, e.basis,
                       e.source_id, e.version, e.effective_from, e.confidence,
                       COALESCE(s.name, '') AS source_name,
                       COALESCE(s.type, '') AS source_type
                FROM knowledge_entry e
                LEFT JOIN knowledge_source s ON s.source_id = e.source_id
                {clause}
                ORDER BY e.kind, e.entry_id
                LIMIT :limit OFFSET :offset""",
            params,
        ).fetchall()
    except (sqlite3.Error, OSError):
        logger.warning("knowledge list_entries failed; returning empty page", exc_info=True)
        return _empty_page(page_size)
    finally:
        if conn is not None:
            conn.close()

    items = [
        {
            "entry_id": row["entry_id"],
            "kind": row["kind"],
            "domain": row["domain"],
            "title": row["title"],
            "statement": row["statement"],
            "basis": row["basis"],
            "source_id": row["source_id"],
            "source_name": row["source_name"],
            "source_type": row["source_type"],
            "version": row["version"],
            "effective_from": row["effective_from"],
            "confidence": row["confidence"],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_entry(entry_id: str) -> dict[str, Any] | None:
    """条目详情（含 payload/links/source）；不存在或不可用时返回 None。"""
    if not settings.knowledge_db_path.exists():
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = knowledge_store.knowledge_read_connection()
        row = conn.execute(
            f"SELECT {', '.join(_ENTRY_COLUMNS)} FROM knowledge_entry WHERE entry_id = :entry_id",
            {"entry_id": entry_id},
        ).fetchone()
        if row is None:
            return None
        links = [
            {"target_kind": link["target_kind"], "target_code": link["target_code"]}
            for link in conn.execute(
                """SELECT target_kind, target_code FROM knowledge_link
                   WHERE entry_id = :entry_id ORDER BY target_kind, target_code""",
                {"entry_id": entry_id},
            ).fetchall()
        ]
        source_row = conn.execute(
            f"SELECT {', '.join(_SOURCE_COLUMNS)} FROM knowledge_source "
            "WHERE source_id = :source_id",
            {"source_id": row["source_id"]},
        ).fetchone()
    except (sqlite3.Error, OSError):
        logger.warning("knowledge get_entry failed", exc_info=True)
        return None
    finally:
        if conn is not None:
            conn.close()

    detail: dict[str, Any] = {column: row[column] for column in _ENTRY_COLUMNS}
    detail["payload"] = _load_payload(row["payload_json"])
    detail["links"] = links
    detail["source"] = (
        {column: source_row[column] for column in _SOURCE_COLUMNS}
        if source_row is not None
        else {}
    )
    return detail


def list_sources() -> dict[str, Any]:
    """来源清单（含条目计数）；失败或不可用时返回空列表。"""
    if not settings.knowledge_db_path.exists():
        return {"items": []}
    conn: sqlite3.Connection | None = None
    try:
        conn = knowledge_store.knowledge_read_connection()
        rows = conn.execute(
            """SELECT s.source_id, s.name, s.type, s.license, s.url, s.notice, s.version,
                      COALESCE(c.entry_count, 0) AS entry_count
               FROM knowledge_source s
               LEFT JOIN (
                   SELECT source_id, COUNT(*) AS entry_count
                   FROM knowledge_entry GROUP BY source_id
               ) c ON c.source_id = s.source_id
               ORDER BY s.source_id"""
        ).fetchall()
    except (sqlite3.Error, OSError):
        logger.warning("knowledge list_sources failed; returning empty list", exc_info=True)
        return {"items": []}
    finally:
        if conn is not None:
            conn.close()

    items = [
        {
            "source_id": row["source_id"],
            "name": row["name"],
            "type": row["type"],
            "license": row["license"],
            "url": row["url"],
            "notice": row["notice"],
            "version": row["version"],
            "entry_count": int(row["entry_count"] or 0),
        }
        for row in rows
    ]
    return {"items": items}


def coverage() -> dict[str, int]:
    """知识覆盖度：术语/指标/勾稽规则/行业说法计数。"""
    snapshot = get_snapshot()
    if not snapshot.available:
        return {
            "ontology_terms_covered": 0,
            "metrics_covered": 0,
            "reconciliation_rules": 0,
            "terminology_phrases": 0,
        }
    return {
        "ontology_terms_covered": len(snapshot.by_term),
        "metrics_covered": len(snapshot.by_metric),
        "reconciliation_rules": len(snapshot.by_kind.get("reconciliation_rule", [])),
        "terminology_phrases": len(snapshot.terminology),
    }


def _read_last_error() -> str | None:
    """读取最近一次加载失败摘要（脱敏文本）；读取失败返回 None。"""
    if not settings.knowledge_db_path.exists():
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = knowledge_store.knowledge_read_connection()
        row = conn.execute(
            "SELECT value FROM knowledge_meta WHERE key = :key",
            {"key": "last_load_error"},
        ).fetchone()
    except (sqlite3.Error, OSError):
        return None
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        return None
    value = str(row["value"] or "").strip()
    if not value:
        return None
    return value[:_MAX_LAST_ERROR_CHARS]


def summary() -> dict[str, Any]:
    """知识库摘要；任何异常都降级为 available=false 且计数为 0。"""
    try:
        snapshot = get_snapshot()
    except Exception:  # noqa: BLE001 - 摘要接口绝不返回 500
        logger.warning("knowledge summary failed; returning degraded summary", exc_info=True)
        snapshot = KnowledgeSnapshot.empty()

    if not snapshot.available:
        return {
            "available": False,
            "pack_version": "",
            "entries": 0,
            "sources": 0,
            "loaded_at": None,
            "last_error": _read_last_error(),
            "domains": [],
            "kinds": [],
            "coverage": {
                "ontology_terms_covered": 0,
                "metrics_covered": 0,
                "reconciliation_rules": 0,
                "terminology_phrases": 0,
            },
        }

    domain_counts: dict[str, int] = {}
    for entry in snapshot.entries:
        key = str(entry["domain"])
        domain_counts[key] = domain_counts.get(key, 0) + 1
    domains = [
        {"domain": domain, "entries": count}
        for domain, count in sorted(domain_counts.items())
    ]
    kinds = [
        {"kind": kind, "entries": len(entries)}
        for kind, entries in sorted(snapshot.by_kind.items())
    ]
    return {
        "available": True,
        "pack_version": snapshot.pack_version,
        "entries": len(snapshot.entries),
        "sources": len(snapshot.sources),
        "loaded_at": snapshot.loaded_at,
        "last_error": _read_last_error(),
        "domains": domains,
        "kinds": kinds,
        "coverage": coverage(),
    }
