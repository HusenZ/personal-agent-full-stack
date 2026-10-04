import uuid
from typing import Any

# The OpenAI SDK builds its errors from httpx2, so the stub requests must match.
import httpx2
import pytest
from langchain_core.messages import AIMessage
from openai import APIConnectionError, AuthenticationError, RateLimitError
from pydantic import BaseModel

from app.core.config import Settings
from app.infrastructure.llm.exceptions import LLMConfigurationError, LLMUnavailableError
from app.infrastructure.llm.openai_provider import OpenAIStructuredProvider
from app.infrastructure.llm.prompt import Prompt
from app.infrastructure.observability.tracing import (
    ModelCallKind,
    RunTrace,
    bind_trace,
    model_call,
)

PROMPT = Prompt(name="test.prompt", version="1", system="system", user="user")


class Answer(BaseModel):
    value: str


def build_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://user:secret@localhost:5433/pia",
        "jwt_secret": "x" * 48,
        "llm_timeout_seconds": 1.0,
        "llm_max_attempts": 2,
    }
    return Settings(**(defaults | overrides))


class StubRunnable:
    """Stands in for the structured-output runnable LangChain returns."""

    def __init__(self, *, results: list[Any] | None = None, error: Exception | None = None) -> None:
        self._results = list(results or [])
        self._error = error
        self.calls = 0

    async def ainvoke(self, _messages: Any) -> Any:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._results.pop(0)


class StubChatModel:
    def __init__(self, runnable: StubRunnable) -> None:
        self._runnable = runnable

    def with_structured_output(self, _schema: Any, **_kwargs: Any) -> StubRunnable:
        return self._runnable


def structured(parsed: Any, *, input_tokens: int = 10, output_tokens: int = 5) -> dict[str, Any]:
    """What LangChain returns for a structured call made with include_raw."""
    raw = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    return {"raw": raw, "parsed": parsed, "parsing_error": None}


def provider_with(runnable: StubRunnable, **overrides: Any) -> OpenAIStructuredProvider:
    provider = OpenAIStructuredProvider(build_settings(**overrides))
    # Bypasses client construction so no credentials or network are involved.
    provider._model = StubChatModel(runnable)  # type: ignore[assignment]
    return provider


def connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com/v1"))


def rate_limit_error() -> RateLimitError:
    response = httpx2.Response(
        429, request=httpx2.Request("POST", "https://api.openai.com/v1"), json={}
    )
    return RateLimitError("slow down", response=response, body=None)


def authentication_error() -> AuthenticationError:
    response = httpx2.Response(
        401, request=httpx2.Request("POST", "https://api.openai.com/v1"), json={}
    )
    return AuthenticationError("bad key", response=response, body=None)


class TestConfiguration:
    async def test_a_missing_api_key_is_reported_as_configuration(self):
        """The application starts without credentials; the failure surfaces here."""
        provider = OpenAIStructuredProvider(build_settings(openai_api_key=None))

        with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
            await provider.generate_structured(PROMPT, Answer)

    async def test_rejected_credentials_are_reported_as_configuration(self):
        provider = provider_with(StubRunnable(error=authentication_error()))

        with pytest.raises(LLMConfigurationError):
            await provider.generate_structured(PROMPT, Answer)

    async def test_rejected_credentials_are_not_retried(self):
        """Retrying a bad key only burns quota; it will be equally bad next time."""
        runnable = StubRunnable(error=authentication_error())
        provider = provider_with(runnable)

        with pytest.raises(LLMConfigurationError):
            await provider.generate_structured(PROMPT, Answer)

        assert runnable.calls == 1


class TestSuccess:
    async def test_a_valid_response_is_returned(self):
        provider = provider_with(StubRunnable(results=[structured(Answer(value="ok"))]))

        result = await provider.generate_structured(PROMPT, Answer)

        assert result.value == "ok"

    async def test_a_transient_failure_is_retried_then_succeeds(self):
        class FlakyRunnable(StubRunnable):
            async def ainvoke(self, _messages: Any) -> Any:
                self.calls += 1
                if self.calls == 1:
                    raise connection_error()
                return structured(Answer(value="second try"))

        runnable = FlakyRunnable()
        provider = provider_with(runnable)

        result = await provider.generate_structured(PROMPT, Answer)

        assert result.value == "second try"
        assert runnable.calls == 2


class TestBoundedRetry:
    @pytest.mark.parametrize("error_factory", [connection_error, rate_limit_error])
    async def test_retries_are_bounded(self, error_factory: Any):
        """An unbounded retry loop is unbounded cost and an unbounded request."""
        runnable = StubRunnable(error=error_factory())
        provider = provider_with(runnable, llm_max_attempts=3)

        with pytest.raises(LLMUnavailableError):
            await provider.generate_structured(PROMPT, Answer)

        assert runnable.calls == 3

    async def test_a_single_attempt_is_honoured(self):
        runnable = StubRunnable(error=connection_error())
        provider = provider_with(runnable, llm_max_attempts=1)

        with pytest.raises(LLMUnavailableError):
            await provider.generate_structured(PROMPT, Answer)

        assert runnable.calls == 1

    async def test_the_underlying_failure_is_preserved_for_diagnosis(self):
        provider = provider_with(StubRunnable(error=connection_error()))

        with pytest.raises(LLMUnavailableError) as exc_info:
            await provider.generate_structured(PROMPT, Answer)

        assert exc_info.value.__cause__ is not None

    async def test_a_hanging_provider_is_cut_off(self):
        """No external call may hang the request that is waiting on it."""
        import asyncio

        class HangingRunnable(StubRunnable):
            async def ainvoke(self, _messages: Any) -> Any:
                self.calls += 1
                await asyncio.sleep(10)

        runnable = HangingRunnable()
        provider = provider_with(runnable, llm_timeout_seconds=0.05, llm_max_attempts=2)

        with pytest.raises(LLMUnavailableError):
            await provider.generate_structured(PROMPT, Answer)

        assert runnable.calls == 2

    async def test_a_response_of_the_wrong_type_is_rejected(self):
        """Structured output is only trustworthy if the shape is actually checked."""
        runnable = StubRunnable(
            results=[{"value": "a bare dict"}, structured(None) | {"parsing_error": ValueError()}]
        )
        provider = provider_with(runnable)

        with pytest.raises(LLMUnavailableError):
            await provider.generate_structured(PROMPT, Answer)

        assert runnable.calls == 2


class TestTokenUsage:
    async def test_usage_is_recorded_into_the_open_model_call(self):
        provider = provider_with(StubRunnable(results=[structured(Answer(value="ok"))]))

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            async with model_call(ModelCallKind.STRUCTURED, PROMPT.name):
                await provider.generate_structured(PROMPT, Answer)

        call = trace.model_calls()[0]
        assert (call.input_tokens, call.output_tokens) == (10, 5)

    async def test_every_attempt_is_counted_because_every_attempt_is_billed(self):
        runnable = StubRunnable(
            results=[
                structured(None, input_tokens=7, output_tokens=3),
                structured(Answer(value="ok"), input_tokens=11, output_tokens=4),
            ]
        )
        provider = provider_with(runnable)

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            async with model_call(ModelCallKind.STRUCTURED, PROMPT.name):
                await provider.generate_structured(PROMPT, Answer)

        call = trace.model_calls()[0]
        assert (call.input_tokens, call.output_tokens) == (18, 7)

    async def test_no_trace_means_nothing_is_recorded_and_nothing_breaks(self):
        provider = provider_with(StubRunnable(results=[structured(Answer(value="ok"))]))

        assert (await provider.generate_structured(PROMPT, Answer)).value == "ok"
