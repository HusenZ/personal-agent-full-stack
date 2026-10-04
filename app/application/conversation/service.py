import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.types import StreamMode
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.conversation.dto import (
    ChatCommand,
    ChatResult,
    ConversationDetail,
    Page,
)
from app.application.conversation.events import (
    TurnCompleted,
    TurnEvent,
    TurnEventType,
    TurnProgress,
    describe_step,
)
from app.application.conversation.exceptions import ConversationNotFoundError
from app.application.memory.background import MemoryExtractionPayload, memory_extraction_job
from app.application.runs.recording import build_run_record
from app.core.context import get_request_id
from app.domain.conversation.context import (
    ContextBudget,
    ContextMessage,
    MessageRole,
    ProfileContext,
    derive_title,
)
from app.graph.conversation.graph import ConversationGraph
from app.graph.conversation.state import ConversationTurnState
from app.infrastructure.database.models import AgentRun, Conversation, Message, RunKind
from app.infrastructure.database.repositories.conversation import ConversationRepository
from app.infrastructure.database.repositories.job import JobRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.database.repositories.run import RunRepository
from app.infrastructure.observability.tracing import RunTrace, bind_trace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionSettings:
    # Off is a supported mode; extraction costs model calls per exchange.
    enabled: bool = True
    max_attempts: int = 3


@dataclass(frozen=True)
class PreparedTurn:
    command: ChatCommand
    conversation: Conversation
    history: list[ContextMessage]
    profile: ProfileContext
    timezone: str


# "updates" reports each step; the last "values" is the final state.
_STREAM_MODES: list[StreamMode] = ["updates", "values"]


def _initial_state(turn: PreparedTurn) -> ConversationTurnState:
    return {
        "user_id": str(turn.command.user_id),
        "conversation_id": str(turn.conversation.id),
        "profile": turn.profile,
        "timezone": turn.timezone,
        "history": turn.history,
        "message": turn.command.message,
        "errors": [],
    }


def _describe_for_record(node: str, update: dict[str, Any] | None) -> dict[str, Any] | None:
    # Reuse the stream's allow-list so traces and clients see the same data.
    event = describe_step(node, update)
    return event.data if event is not None else None


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        conversations: ConversationRepository,
        profiles: ProfileRepository,
        runs: RunRepository,
        jobs: JobRepository,
        graph: ConversationGraph,
        budget: ContextBudget,
        extraction: ExtractionSettings | None = None,
    ) -> None:
        self._session = session
        self._conversations = conversations
        self._profiles = profiles
        self._runs = runs
        self._jobs = jobs
        self._extraction = extraction or ExtractionSettings()
        self._graph = graph
        self._budget = budget

    async def send_message(self, command: ChatCommand) -> ChatResult:
        turn = await self.prepare_turn(command)
        trace = RunTrace(run_id=uuid.uuid4())

        with bind_trace(trace):
            try:
                result: dict[str, Any] = await self._graph.ainvoke(_initial_state(turn))
                return await self._record(turn, trace, result, streamed=False)
            except Exception as error:
                await self._record_failed_run(turn, trace, {}, error)
                raise

    async def prepare_turn(self, command: ChatCommand) -> PreparedTurn:
        """Resolve everything a turn needs before any of it is answered.

        Separate from running the turn so a streaming caller can surface a
        missing or foreign conversation as an ordinary error response, before
        it has committed to a stream whose status code is already sent.
        """
        conversation = await self._resolve_conversation(command)
        history = await self._recent_history(conversation, command.conversation_id is not None)
        profile, timezone = await self._profile_context(command.user_id)

        # Commit now to release the connection while the model calls run.
        await self._session.commit()

        return PreparedTurn(
            command=command,
            conversation=conversation,
            history=history,
            profile=profile,
            timezone=timezone,
        )

    async def stream_turn(self, turn: PreparedTurn) -> AsyncIterator[TurnEvent]:
        """Run a prepared turn, reporting each step as it finishes.

        The exchange is stored before the reply is released, so a client never
        receives a reply that was not recorded. If the caller stops consuming
        part way, nothing is committed: the request's session rolls back, and
        a question whose answer was never delivered is not left in the history.
        """
        trace = RunTrace(run_id=uuid.uuid4())
        yield TurnProgress(
            TurnEventType.QUERY_STARTED,
            {"conversation_id": str(turn.conversation.id), "run_id": str(trace.run_id)},
        )

        result: dict[str, Any] = {}
        with bind_trace(trace):
            try:
                async for part in self._graph.astream(
                    _initial_state(turn), stream_mode=_STREAM_MODES
                ):
                    # With several modes requested, each part is a (mode, payload) pair.
                    mode, payload = cast(tuple[str, dict[str, Any]], part)
                    if mode == "values":
                        result = payload
                        continue
                    for node, update in payload.items():
                        event = describe_step(node, update)
                        if event is not None:
                            yield event

                completed = await self._record(turn, trace, result, streamed=True)
            except Exception as error:
                await self._record_failed_run(turn, trace, result, error)
                raise

        yield TurnCompleted(completed)

    async def _record(
        self, turn: PreparedTurn, trace: RunTrace, result: dict[str, Any], *, streamed: bool
    ) -> ChatResult:
        conversation = turn.conversation
        now = datetime.now(tz=UTC)

        if turn.command.conversation_id is None:
            self._conversations.add(conversation)
        else:
            # Lock and re-read the count so concurrent turns don't reuse positions.
            await self._session.refresh(
                conversation, attribute_names=["message_count"], with_for_update=True
            )
        next_position = conversation.message_count

        # Assign ids now so the extraction job can reference these messages.
        user_message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            position=next_position,
            role=MessageRole.USER,
            content=turn.command.message,
        )
        reply = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            position=next_position + 1,
            role=MessageRole.ASSISTANT,
            content=str(result.get("reply") or ""),
        )
        self._conversations.add_message(user_message)
        self._conversations.add_message(reply)

        conversation.message_count = next_position + 2
        conversation.last_message_at = now

        self._runs.add(
            self._run_record(turn, trace, result, error=None, conversation_id=conversation.id)
        )

        if self._extraction.enabled:
            self._jobs.add(
                memory_extraction_job(
                    turn.command.user_id,
                    MemoryExtractionPayload(
                        conversation_id=conversation.id,
                        user_message_id=user_message.id,
                        reply_message_id=reply.id,
                        include_reply=result.get("grounded") is not False,
                        timezone=turn.timezone,
                    ),
                    max_attempts=self._extraction.max_attempts,
                )
            )

        # Commit exchange, run and extraction job together, or none of them.
        await self._session.commit()

        logger.info(
            "chat_message_answered",
            extra={
                "user_id": str(turn.command.user_id),
                "conversation_id": str(conversation.id),
                "history_messages": len(turn.history),
                "retrieval_attempts": int(result.get("retrieval_attempts") or 0),
                "generation_attempts": int(result.get("generation_attempts") or 0),
                "grounded": result.get("grounded"),
                "degraded": bool(result.get("degraded")),
                "streamed": streamed,
            },
        )

        return ChatResult(
            conversation=conversation,
            user_message=user_message,
            reply=reply,
            degraded=bool(result.get("degraded")),
            grounded=result.get("grounded"),
            timezone=turn.timezone,
            run_id=trace.run_id,
        )

    async def _record_failed_run(
        self,
        turn: PreparedTurn,
        trace: RunTrace,
        result: dict[str, Any],
        error: BaseException,
    ) -> None:
        """Store the record of a run that failed, without the exchange it was for.

        Failed runs are the ones a trace is most needed for, so the record is
        written even though the exchange is not. Best effort: when the database
        itself is what failed, this fails too, and the original error is the one
        that matters to the caller.
        """
        # Read before rollback; expired attributes raise on an async session.
        conversation_id = turn.conversation.id if turn.command.conversation_id is not None else None

        try:
            await self._session.rollback()
            self._runs.add(
                self._run_record(turn, trace, result, error=error, conversation_id=conversation_id)
            )
            await self._session.commit()
        except Exception:
            logger.exception("run_record_failed", extra={"run_id": str(trace.run_id)})
            await self._session.rollback()

        logger.warning(
            "chat_turn_failed",
            extra={
                "user_id": str(turn.command.user_id),
                "error_type": type(error).__name__,
                "failed_node": (step.node if (step := trace.failed_step()) else None),
            },
        )

    def _run_record(
        self,
        turn: PreparedTurn,
        trace: RunTrace,
        result: dict[str, Any],
        *,
        error: BaseException | None,
        conversation_id: uuid.UUID | None,
    ) -> AgentRun:
        return build_run_record(
            trace,
            kind=RunKind.CONVERSATION_TURN,
            user_id=turn.command.user_id,
            conversation_id=conversation_id,
            request_id=get_request_id(),
            result=result,
            error=error,
            describe=_describe_for_record,
        )

    async def list_conversations(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> Page[Conversation]:
        conversations, total = await self._conversations.list_for_user(
            user_id, limit=limit, offset=offset
        )
        return Page(items=conversations, total_items=total)

    async def get_conversation(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> ConversationDetail:
        conversation = await self._conversations.get_owned_by(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError()

        messages, total = await self._conversations.list_messages(
            conversation.id, limit=limit, offset=offset
        )

        return ConversationDetail(
            conversation=conversation, messages=messages, total_messages=total
        )

    async def delete_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Delete a conversation and its messages.

        What was learned from it stays: memories, events and runs keep their
        own rows with the link cleared, because the user manages those
        separately and deleting a chat is not the same as asking to forget.
        """
        if not await self._conversations.delete_owned_by(conversation_id, user_id):
            raise ConversationNotFoundError()
        await self._session.commit()

        logger.info("conversation_deleted", extra={"user_id": str(user_id)})

    async def _resolve_conversation(self, command: ChatCommand) -> Conversation:
        if command.conversation_id is None:
            conversation = Conversation(
                id=uuid.uuid4(),
                user_id=command.user_id,
                title=derive_title(command.message),
                last_message_at=datetime.now(tz=UTC),
                message_count=0,
            )
            # Not added yet: nothing is written until the turn is answered.
            return conversation

        existing = await self._conversations.get_owned_by(command.conversation_id, command.user_id)
        if existing is None:
            raise ConversationNotFoundError()

        return existing

    async def _recent_history(
        self, conversation: Conversation, is_existing: bool
    ) -> list[ContextMessage]:
        if not is_existing:
            return []

        messages = await self._conversations.recent_messages(
            conversation.id, limit=self._budget.max_history_messages
        )

        return [ContextMessage(role=message.role, content=message.content) for message in messages]

    async def _profile_context(self, user_id: uuid.UUID) -> tuple[ProfileContext, str]:
        profile = await self._profiles.get_by_user_id(user_id)
        if profile is None:
            return ProfileContext(), "UTC"

        return (
            ProfileContext(
                goals=tuple(goal.name for goal in profile.goals),
                constraints=tuple(item.description for item in profile.constraints),
                preferences=tuple((item.key, item.value) for item in profile.preferences),
            ),
            profile.timezone,
        )
