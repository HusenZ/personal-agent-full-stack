from typing import Any

from pydantic import BaseModel, Field


def reject_nul_characters(value: str) -> str:
    """Refuse text PostgreSQL cannot store.

    NUL is valid JSON (``"\\u0000"``) and valid Python, so it passes every other
    check, but a text column rejects it at write time, which surfaced as a 500.
    Refused here it is a 422 naming the field, like any other invalid input.
    """
    if "\x00" in value:
        raise ValueError("must not contain NUL (\\u0000) characters")
    return value


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
