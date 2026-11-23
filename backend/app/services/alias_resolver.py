"""指标别名解析：精确、最长、排除项与歧义消解。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()《》<>【】\[\]{}|/\\_\-—…~`·]+")
_NOISE_WORDS = ("请帮我", "帮我", "请", "查一下", "查询", "看看", "是多少", "怎么样", "如何", "多少", "的")
_PERIOD_PATTERNS = (
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月"),
    re.compile(r"\d{4}\s*[-/]\s*\d{1,2}"),
    re.compile(r"\d{4}\s*年"),
    re.compile(r"\d{4}\s*Q[1-4]", re.IGNORECASE),
    re.compile(r"第?[一二三四1-4]\s*季度"),
    re.compile(r"(本|上|下|当|去|今)\s*(月|季度|年)"),
    re.compile(r"同比|环比"),
)


@dataclass
class AliasResolution:
    kind: str  # matched | ambiguous | exclusion | none
    metric_code: str | None = None
    metric_name: str | None = None
    metric_version: str | None = None
    alias: str | None = None
    alias_type: str | None = None
    priority: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)


def normalize(text: str) -> str:
    """小写、去空白、去标点，用于别名比较。"""
    lowered = (text or "").strip().lower()
    return _PUNCT.sub("", lowered)


def strip_question_noise(question: str) -> str:
    """移除期间表达式、同比环比、标点与常见问句噪声。"""
    text = question or ""
    for pattern in _PERIOD_PATTERNS:
        text = pattern.sub(" ", text)
    for word in _NOISE_WORDS:
        text = text.replace(word, " ")
    return normalize(text)


def _negated(text: str, start: int) -> bool:
    if start <= 0:
        return False
    return text[start - 1] in ("不", "非", "未", "无")


def _candidate(entry: dict[str, Any], length: int) -> dict[str, Any]:
    return {
        "metric_code": entry["metric_code"],
        "metric_name": entry["metric_name"],
        "metric_version": entry.get("metric_version"),
        "alias": entry["alias"],
        "alias_type": entry["alias_type"],
        "priority": int(entry.get("priority") or 0),
        "match_length": length,
    }


def resolve(question: str) -> AliasResolution:
    """按 精确 → 最长 → 排除项 → 歧义 顺序解析问题中的指标别名。"""
    from app.services import metrics

    aliases = metrics.active_aliases()
    if not aliases:
        return AliasResolution(kind="none")
    normalized = strip_question_noise(question)
    if not normalized:
        return AliasResolution(kind="none")

    # 1. 精确命中（整句归一化后与别名完全一致）
    exact = [entry for entry in aliases if normalized == entry["normalized_alias"]]
    if exact:
        exact.sort(
            key=lambda item: (
                -int(item.get("priority") or 0),
                item["metric_code"],
            )
        )
        exact_metrics = {entry["metric_code"] for entry in exact}
        if len(exact_metrics) > 1:
            candidates = [
                _candidate(entry, len(entry["normalized_alias"]))
                for entry in exact
            ]
            candidates.sort(
                key=lambda item: (
                    -item["match_length"],
                    -item["priority"],
                    item["metric_code"],
                )
            )
            return AliasResolution(kind="ambiguous", candidates=candidates)
        entry = exact[0]
        resolution = _matched(entry, len(entry["normalized_alias"]))
        if entry["alias_type"] == "exclusion":
            resolution.kind = "exclusion"
        return resolution

    # 2. 收集包含命中（排除被否定的排除项）
    hits: list[tuple[dict[str, Any], int]] = []
    for entry in aliases:
        alias_norm = entry["normalized_alias"]
        if len(alias_norm) < 2:
            continue
        start = normalized.find(alias_norm)
        if start < 0:
            continue
        if entry["alias_type"] == "exclusion" and _negated(normalized, start):
            continue
        hits.append((entry, len(alias_norm)))
    if not hits:
        return AliasResolution(kind="none")

    longest = max(length for _, length in hits)
    top = [item for item in hits if item[1] == longest]
    top.sort(key=lambda item: (-int(item[0].get("priority") or 0), item[0]["metric_code"]))

    # 3. 最长命中为排除项：要求确认，不直接给数
    if any(entry["alias_type"] == "exclusion" for entry, _ in top):
        entry = next(entry for entry, _ in top if entry["alias_type"] == "exclusion")
        resolution = _matched(entry, longest)
        resolution.kind = "exclusion"
        return resolution

    # 4. 同指标多别名去重；不同指标同时命中视为歧义
    by_metric: dict[str, tuple[dict[str, Any], int]] = {}
    for entry, length in top:
        existing = by_metric.get(entry["metric_code"])
        if existing is None or entry["priority"] > existing[0]["priority"]:
            by_metric[entry["metric_code"]] = (entry, length)
    candidates = [_candidate(entry, length) for entry, length in by_metric.values()]
    candidates.sort(key=lambda item: (-item["match_length"], -item["priority"], item["metric_code"]))
    if len(by_metric) > 1:
        return AliasResolution(kind="ambiguous", candidates=candidates)
    entry, length = next(iter(by_metric.values()))
    return _matched(entry, length)


def _matched(entry: dict[str, Any], length: int) -> AliasResolution:
    return AliasResolution(
        kind="matched",
        metric_code=entry["metric_code"],
        metric_name=entry["metric_name"],
        metric_version=entry.get("metric_version"),
        alias=entry["alias"],
        alias_type=entry["alias_type"],
        priority=int(entry.get("priority") or 0),
        candidates=[_candidate(entry, length)],
    )
