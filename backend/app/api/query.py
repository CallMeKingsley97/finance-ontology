"""自然语言问数 API。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import role_from_request
from app.schemas.common import ok
from app.schemas.query import AskRequest
from app.services.query import answer_question

router = APIRouter(prefix="/api/query", tags=["query"])


@router.post("/ask")
def ask(request: Request, payload: AskRequest) -> dict:
    role = role_from_request(request)
    return ok(answer_question(payload.question, role, payload.limit))
