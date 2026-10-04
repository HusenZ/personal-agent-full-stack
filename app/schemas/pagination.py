from math import ceil
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

from app.schemas.common import ResponseMeta

# Max page size stops a client fetching everything in one query.
MAX_PAGE_SIZE = 100


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


class PageMeta(ResponseMeta):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool

    @classmethod
    def build(cls, params: PageParams, total_items: int, request_id: str | None) -> "PageMeta":
        total_pages = ceil(total_items / params.page_size) if total_items else 0

        return cls(
            request_id=request_id,
            page=params.page,
            page_size=params.page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=params.page < total_pages,
        )


class PaginatedResponse[ItemT](BaseModel):
    """A page of items."""

    data: list[ItemT]
    meta: PageMeta


class PaginatedItemResponse[ItemT](BaseModel):
    """One object whose nested collection is the thing being paginated.

    Distinct from PaginatedResponse so that reading a single conversation
    returns the conversation, not a one-element list containing it.
    """

    data: ItemT
    meta: PageMeta
