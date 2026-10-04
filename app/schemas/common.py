from typing import Any

from pydantic import BaseModel, Field


class ResponseMeta(BaseModel):
    request_id: str | None = None


class DataResponse[DataT](BaseModel):
    data: DataT
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    meta: ResponseMeta = Field(default_factory=ResponseMeta)
