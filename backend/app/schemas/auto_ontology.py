"""外部 auto-ontology 绑定的请求模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AutoOntologyConfigRequest(BaseModel):
    """PUT /api/ontology/service 的全量配置请求。"""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    auth_header: str = "Authorization"
    token: str | None = None
    clear_token: bool = False
    enabled: bool = True
    verify_tls: bool = True
    probe_path: str = "/api/terms?limit=1"
    timeout_seconds: float | None = Field(default=None, gt=0, le=10)


class AutoOntologyTestRequest(BaseModel):
    """POST /api/ontology/service/test 的可选覆盖字段。

    任一字段被显式传入即视为“表单测试”，不写入配置与状态。
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    auth_header: str | None = None
    token: str | None = None
    probe_path: str | None = None
    verify_tls: bool | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=10)
