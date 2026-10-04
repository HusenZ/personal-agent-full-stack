import uuid
from dataclasses import dataclass
from enum import StrEnum


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ContextMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ProfileContext:
    goals: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    preferences: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        return not (self.goals or self.constraints or self.preferences)

    def render(self) -> str:
        lines: list[str] = []
        lines += [f"Goal: {goal}" for goal in self.goals]
        lines += [f"Constraint: {constraint}" for constraint in self.constraints]
        lines += [f"Prefers {key}: {value}" for key, value in self.preferences]
        return "\n".join(lines)


@dataclass(frozen=True)
class RecalledMemory:
    id: uuid.UUID
    content: str
    memory_type: str

    # Reranked score, so keyword-only matches can still be ordered.
    relevance: float


@dataclass(frozen=True)
class ComputedTotals:
    """Exact figures from the database, and the period they cover.

    Held separately from everything else in the context because it is the one
    part the model must not reinterpret: it is arithmetic that has already been
    done correctly.
    """

    period: str
    rendered: str


@dataclass(frozen=True)
class ConversationContext:
    profile: ProfileContext
    history: tuple[ContextMessage, ...]
    current_message: str
    memories: tuple[RecalledMemory, ...] = ()
    timeline: str = ""
    timeline_period: str = ""
    totals: ComputedTotals | None = None

    # True when older turns were dropped to fit the budget.
    history_truncated: bool = False

    # True when retrieval couldn't find enough, so the reply admits the gap.
    evidence_incomplete: bool = False


@dataclass(frozen=True)
class ContextBudget:
    """How much conversation the model is allowed to see.

    Bounded on two axes because either alone is escapable: a message count says
    nothing about length, and a character budget alone would admit a hundred
    one-word turns whose interleaving costs more than it is worth.
    """

    max_history_messages: int = 20
    max_history_characters: int = 8000
    max_message_characters: int = 4000

    # Memories share the budget with history; fewer relevant ones beat many weak ones.
    max_memories: int = 6
    max_memory_characters: int = 2000

    # Cap timeline lines so long ranges don't flood the prompt.
    max_timeline_entries: int = 25
    max_timeline_characters: int = 3000

    def __post_init__(self) -> None:
        if self.max_history_messages < 1:
            raise ValueError("max_history_messages must be at least 1")
        if self.max_history_characters < 1:
            raise ValueError("max_history_characters must be at least 1")
        if self.max_message_characters < 1:
            raise ValueError("max_message_characters must be at least 1")
        if self.max_memories < 1:
            raise ValueError("max_memories must be at least 1")
        if self.max_memory_characters < 1:
            raise ValueError("max_memory_characters must be at least 1")
        if self.max_timeline_entries < 1:
            raise ValueError("max_timeline_entries must be at least 1")
        if self.max_timeline_characters < 1:
            raise ValueError("max_timeline_characters must be at least 1")


DEFAULT_CONTEXT_BUDGET = ContextBudget()


def build_conversation_context(
    profile: ProfileContext,
    history: list[ContextMessage],
    current_message: str,
    budget: ContextBudget = DEFAULT_CONTEXT_BUDGET,
    memories: list[RecalledMemory] | None = None,
    timeline: str = "",
    timeline_period: str = "",
    totals: ComputedTotals | None = None,
    evidence_incomplete: bool = False,
) -> ConversationContext:
    """Assemble the bounded view of a conversation the model will be given.

    History is selected newest first, because the most recent turns are the ones
    a reply has to be coherent with, and then restored to chronological order so
    the model reads the exchange the way it happened.

    The message being answered is never dropped, only truncated: replying to a
    question the model was not shown would be worse than replying to a shortened
    version of it.
    """
    selected: list[ContextMessage] = []
    used_characters = 0
    truncated = False

    for message in reversed(history):
        if len(selected) >= budget.max_history_messages:
            truncated = True
            break

        content = message.content[: budget.max_message_characters]
        if used_characters + len(content) > budget.max_history_characters:
            truncated = True
            break

        selected.append(ContextMessage(role=message.role, content=content))
        used_characters += len(content)

    return ConversationContext(
        profile=profile,
        history=tuple(reversed(selected)),
        current_message=current_message[: budget.max_message_characters],
        memories=select_memories(memories or [], budget),
        timeline=timeline[: budget.max_timeline_characters],
        timeline_period=timeline_period,
        totals=totals,
        history_truncated=truncated,
        evidence_incomplete=evidence_incomplete,
    )


def select_memories(
    memories: list[RecalledMemory], budget: ContextBudget = DEFAULT_CONTEXT_BUDGET
) -> tuple[RecalledMemory, ...]:
    """Take the strongest matches that fit the budget.

    Assumes the caller ordered by relevance, which retrieval already does, and
    stops at the first memory that would not fit rather than skipping past it:
    a later, weaker memory is not worth displacing the ordering.
    """
    selected: list[RecalledMemory] = []
    used = 0

    for memory in memories[: budget.max_memories]:
        if used + len(memory.content) > budget.max_memory_characters:
            break
        selected.append(memory)
        used += len(memory.content)

    return tuple(selected)


def render_memories(memories: tuple[RecalledMemory, ...]) -> str:
    return "\n".join(f"({memory.memory_type}) {memory.content}" for memory in memories)


def render_history(history: tuple[ContextMessage, ...]) -> str:
    return "\n".join(f"{message.role.value}: {message.content}" for message in history)


def derive_title(first_message: str, max_length: int = 80) -> str:
    """Name a conversation after the message that started it.

    Deterministic rather than model-generated: a title is not worth a model call,
    and a generated one would be one more thing that can fail or drift.
    """
    collapsed = " ".join(first_message.split())
    if not collapsed:
        return "New conversation"
    if len(collapsed) <= max_length:
        return collapsed

    # Prefer cutting at a word boundary, but only if that keeps most of the title.
    clipped = collapsed[: max_length - 1]
    boundary = clipped.rfind(" ")
    if boundary > max_length // 2:
        clipped = clipped[:boundary]

    return clipped.rstrip() + "…"
