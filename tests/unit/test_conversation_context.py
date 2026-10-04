import pytest

from app.domain.conversation.context import (
    ContextBudget,
    ContextMessage,
    MessageRole,
    ProfileContext,
    build_conversation_context,
    derive_title,
    render_history,
)


def history(count: int, content: str = "a message") -> list[ContextMessage]:
    return [
        ContextMessage(
            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            content=f"{content} {index}",
        )
        for index in range(count)
    ]


class TestBudgetValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_history_messages": 0},
            {"max_history_characters": 0},
            {"max_message_characters": 0},
        ],
    )
    def test_incoherent_budgets_are_rejected(self, kwargs: dict[str, int]):
        with pytest.raises(ValueError):
            ContextBudget(**kwargs)


class TestHistorySelection:
    def test_short_history_is_kept_whole(self):
        context = build_conversation_context(ProfileContext(), history(3), "now")

        assert len(context.history) == 3
        assert context.history_truncated is False

    def test_history_is_capped_by_message_count(self):
        budget = ContextBudget(max_history_messages=5, max_history_characters=100_000)

        context = build_conversation_context(ProfileContext(), history(50), "now", budget)

        assert len(context.history) == 5
        assert context.history_truncated is True

    def test_history_is_capped_by_total_length(self):
        """A message count alone says nothing about how much text it admits."""
        budget = ContextBudget(max_history_messages=100, max_history_characters=120)

        context = build_conversation_context(ProfileContext(), history(50, "x" * 40), "now", budget)

        assert sum(len(message.content) for message in context.history) <= 120
        assert context.history_truncated is True

    def test_the_most_recent_turns_are_the_ones_kept(self):
        budget = ContextBudget(max_history_messages=2, max_history_characters=100_000)

        context = build_conversation_context(ProfileContext(), history(10), "now", budget)

        assert [message.content for message in context.history] == ["a message 8", "a message 9"]

    def test_kept_history_reads_in_the_order_it_happened(self):
        budget = ContextBudget(max_history_messages=3, max_history_characters=100_000)

        context = build_conversation_context(ProfileContext(), history(10), "now", budget)

        assert [message.content for message in context.history] == [
            "a message 7",
            "a message 8",
            "a message 9",
        ]

    def test_roles_are_preserved(self):
        context = build_conversation_context(ProfileContext(), history(2), "now")

        assert context.history[0].role is MessageRole.USER
        assert context.history[1].role is MessageRole.ASSISTANT

    def test_an_empty_history_is_not_reported_as_truncated(self):
        context = build_conversation_context(ProfileContext(), [], "now")

        assert context.history == ()
        assert context.history_truncated is False


class TestCurrentMessage:
    def test_the_message_being_answered_is_never_dropped(self):
        """Replying to a question the model was not shown is worse than
        replying to a shortened version of it."""
        budget = ContextBudget(max_history_messages=1, max_history_characters=1)

        context = build_conversation_context(ProfileContext(), history(50), "the question", budget)

        assert context.current_message == "the question"

    def test_an_overlong_message_is_truncated_rather_than_refused(self):
        budget = ContextBudget(max_message_characters=50)

        context = build_conversation_context(ProfileContext(), [], "x" * 500, budget)

        assert len(context.current_message) == 50

    def test_an_overlong_history_entry_is_truncated(self):
        budget = ContextBudget(max_message_characters=10, max_history_characters=1000)

        context = build_conversation_context(
            ProfileContext(), [ContextMessage(MessageRole.USER, "y" * 500)], "now", budget
        )

        assert len(context.history[0].content) == 10


class TestProfileContext:
    def test_an_empty_profile_is_recognised(self):
        assert ProfileContext().is_empty()

    def test_a_populated_profile_renders_each_kind(self):
        rendered = ProfileContext(
            goals=("Move into AI engineering",),
            constraints=("Only evenings",),
            preferences=(("communication_style", "direct"),),
        ).render()

        assert "Goal: Move into AI engineering" in rendered
        assert "Constraint: Only evenings" in rendered
        assert "Prefers communication_style: direct" in rendered


class TestRenderHistory:
    def test_each_turn_is_attributed(self):
        rendered = render_history(
            (
                ContextMessage(MessageRole.USER, "hello"),
                ContextMessage(MessageRole.ASSISTANT, "hi"),
            )
        )

        assert rendered == "user: hello\nassistant: hi"


class TestDeriveTitle:
    def test_a_short_message_becomes_the_title(self):
        assert derive_title("What should I focus on?") == "What should I focus on?"

    def test_whitespace_is_collapsed(self):
        assert derive_title("  too   many\nspaces  ") == "too many spaces"

    def test_a_long_message_is_shortened(self):
        title = derive_title("word " * 100)

        assert len(title) <= 80
        assert title.endswith("…")

    def test_shortening_prefers_a_word_boundary(self):
        title = derive_title("alpha beta gamma delta " * 10)

        assert not title.removesuffix("…").endswith(" ")
        assert "alpha" in title

    def test_an_unbroken_run_is_still_shortened(self):
        """A single very long token has no boundary to cut at."""
        title = derive_title("x" * 500)

        assert len(title) <= 80

    def test_an_empty_message_still_yields_a_title(self):
        assert derive_title("   ") == "New conversation"
