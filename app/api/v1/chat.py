import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    ConversationServiceDep,
    CurrentUserDep,
    SubscriptionServiceDep,
)
from app.api.rate_limiting import ChatRateLimit
from app.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, encode_event
from app.api.usage_limits import ChatUsage
from app.application.conversation.dto import ChatCommand, ChatResult
from app.application.conversation.events import TurnProgress
from app.core.context import get_request_id
from app.infrastructure.database.models import Message
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.conversation import ChatRequest, ChatResponse, MessageResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def to_message_response(message: Message) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )


def to_chat_response(result: ChatResult) -> ChatResponse:
    return ChatResponse(
        conversation_id=result.conversation.id,
        title=result.conversation.title,
        user_message=to_message_response(result.user_message),
        reply=to_message_response(result.reply),
        degraded=result.degraded,
        grounded=result.grounded,
        run_id=result.run_id,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[ChatResponse],
    summary="Send a message and receive a reply",
)
async def send_message(
    payload: ChatRequest,
    current_user: CurrentUserDep,
    service: ConversationServiceDep,
    _limit: ChatRateLimit,
    _usage: ChatUsage,
) -> DataResponse[ChatResponse]:
    result: ChatResult = await service.send_message(
        ChatCommand(
            user_id=current_user.id,
            message=payload.message,
            conversation_id=payload.conversation_id,
        )
    )

    return DataResponse(
        data=to_chat_response(result),
        meta=ResponseMeta(request_id=get_request_id()),
    )


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {SSE_MEDIA_TYPE: {}},
            "description": (
                "Server-sent events: progress events as each step finishes, then "
                "`reply` and `completed` once the reply has passed verification "
                "and been stored, or `error`."
            ),
        }
    },
    summary="Send a message and stream the turn's progress",
)
async def stream_message(
    payload: ChatRequest,
    current_user: CurrentUserDep,
    service: ConversationServiceDep,
    response: Response,
    subscriptions: SubscriptionServiceDep,
    _limit: ChatRateLimit,
    usage: ChatUsage,
) -> StreamingResponse:
    # Prepared before streaming so a bad conversation id is a plain 404.
    turn = await service.prepare_turn(
        ChatCommand(
            user_id=current_user.id,
            message=payload.message,
            conversation_id=payload.conversation_id,
        )
    )

    async def events() -> AsyncIterator[str]:
        try:
            async for event in service.stream_turn(turn):
                if isinstance(event, TurnProgress):
                    yield encode_event(event.type.value, event.data)
                    continue

                result = event.result
                # Send grounded with the text so the client can decide how to show it.
                yield encode_event(
                    "reply", {"content": result.reply.content, "grounded": result.grounded}
                )
                yield encode_event(
                    "completed",
                    {
                        "data": to_chat_response(result).model_dump(mode="json"),
                        "meta": {"request_id": get_request_id()},
                    },
                )
        except Exception:
            # Headers are already sent, so report a safe error event instead.
            logger.exception("chat_stream_failed")
            # The usage dependency can't see stream failures, so refund here.
            if usage is not None:
                await subscriptions.refund(usage)
            yield encode_event(
                "error",
                {
                    "code": "TURN_FAILED",
                    "message": "The reply could not be completed. Please try again.",
                    "request_id": get_request_id(),
                },
            )

    # Keep headers set by dependencies (e.g. rate limit counters).
    headers = {**dict(response.headers), **SSE_HEADERS}
    headers.pop("content-length", None)

    return StreamingResponse(events(), media_type=SSE_MEDIA_TYPE, headers=headers)
