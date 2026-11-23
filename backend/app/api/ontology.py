"""本体 API：生成、查询、编辑、认证、关系。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import require_admin, role_from_request
from app.schemas.common import AppError, ok
from app.schemas.ontology import CertifyRequest, GenerateRequest, TermUpdateRequest
from app.services.ontology import (
    certify_term,
    generate_ontology,
    get_term,
    list_relations,
    list_terms,
    update_term,
)

router = APIRouter(prefix="/api/ontology", tags=["ontology"])


def _parse_bool(value: str | None, field: str) -> bool | None:
    if value is None or value == "":
        return None
    lowered = value.lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise AppError(422, "VALIDATION_FAILED", f"{field} 仅支持 true/false")


@router.post("/generate")
def ontology_generate(request: Request, payload: GenerateRequest) -> dict:
    require_admin(request)
    return ok(generate_ontology(force=payload.force))


@router.get("/terms")
def ontology_terms(
    q: str | None = None,
    certified: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> dict:
    return ok(list_terms(q=q, certified=_parse_bool(certified, "certified"), category=category, limit=limit))


@router.get("/terms/{term_id}")
def ontology_term_detail(term_id: int, request: Request) -> dict:
    role = role_from_request(request)
    return ok(get_term(term_id, role))


@router.patch("/terms/{term_id}")
def ontology_term_update(term_id: int, request: Request, payload: TermUpdateRequest) -> dict:
    role = require_admin(request)
    data = payload.model_dump(exclude_unset=True)
    return ok(update_term(term_id, data, role))


@router.post("/terms/{term_id}/certify")
def ontology_term_certify(term_id: int, request: Request, payload: CertifyRequest) -> dict:
    role = require_admin(request)
    return ok(certify_term(term_id, role, payload.certified))


@router.get("/relations")
def ontology_relations() -> dict:
    return ok(list_relations())
