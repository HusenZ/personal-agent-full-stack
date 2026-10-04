import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import CurrentUserDep, MemoryServiceDep
from app.application.memory.dto import MemoryFilters, UpdateMemoryCommand
from app.core.context import get_request_id
from app.domain.memory.types import MemoryStatus, MemoryType
from app.infrastructure.database.models import Memory
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.memory import MemoryResponse, UpdateMemoryRequest
from app.schemas.pagination import PageMeta, PageParams, PaginatedResponse, page_params

router = APIRouter(prefix="/memories", tags=["memories"])

PageParamsDep = Annotated[PageParams, Depends(page_params)]

# Hide superseded and archived memories unless asked for.
_DEFAULT_STATUSES = (MemoryStatus.ACTIVE,)


def _to_response(memory: Memory) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type,
        status=memory.status,
        durability=memory.durability,
        source=memory.source,
        importance=round(memory.importance, 4),
        confidence=round(memory.confidence, 4),
        is_sensitive=memory.is_sensitive,
        occurred_at=memory.occurred_at,
        expires_at=memory.expires_at,
        superseded_by_id=memory.superseded_by_id,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


@router.get(
    "",
    response_model=PaginatedResponse[MemoryResponse],
    summary="List what is remembered about you",
)
async def list_memories(
    current_user: CurrentUserDep,
    service: MemoryServiceDep,
    params: PageParamsDep,
    memory_type: Annotated[MemoryType | None, Query()] = None,
    include_superseded: Annotated[bool, Query()] = False,
    include_archived: Annotated[bool, Query()] = False,
) -> PaginatedResponse[MemoryResponse]:
    statuses = list(_DEFAULT_STATUSES)
    if include_superseded:
        statuses.append(MemoryStatus.SUPERSEDED)
    if include_archived:
        statuses.append(MemoryStatus.ARCHIVED)

    page = await service.list_for_user(
        current_user.id,
        MemoryFilters(statuses=tuple(statuses), memory_type=memory_type),
        limit=params.page_size,
        offset=params.offset,
    )

    return PaginatedResponse(
        data=[_to_response(memory) for memory in page.items],
        meta=PageMeta.build(params, page.total_items, get_request_id()),
    )


@router.patch(
    "/{memory_id}",
    response_model=DataResponse[MemoryResponse],
    summary="Correct or archive a memory",
)
async def update_memory(
    memory_id: uuid.UUID,
    payload: UpdateMemoryRequest,
    current_user: CurrentUserDep,
    service: MemoryServiceDep,
) -> DataResponse[MemoryResponse]:
    memory = await service.update(
        memory_id,
        current_user.id,
        UpdateMemoryCommand(
            content=payload.content, importance=payload.importance, status=payload.status
        ),
    )

    return DataResponse(data=_to_response(memory), meta=ResponseMeta(request_id=get_request_id()))


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory permanently",
)
async def delete_memory(
    memory_id: uuid.UUID, current_user: CurrentUserDep, service: MemoryServiceDep
) -> None:
    await service.delete(memory_id, current_user.id)
