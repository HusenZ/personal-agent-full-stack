import asyncio
import logging
from collections.abc import Callable
from typing import Any, cast

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.infrastructure.llm.exceptions import (
    LLMConfigurationError,
    LLMInvalidOutputError,
    LLMUnavailableError,
)
from app.infrastructure.llm.prompt import Prompt
from app.infrastructure.observability.tracing import record_token_usage

logger = logging.getLogger(__name__)

# Retryable: the request never reached the model, or output was unusable.
_RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    OutputParserException,
    ValidationError,
    TimeoutError,
)

# Not retryable: bad credentials or request.
_CREDENTIAL_ERRORS = (AuthenticationError, PermissionDeniedError)
_REQUEST_ERRORS = (BadRequestError,)


def _token_usage(result: Any) -> tuple[int | None, int | None]:
    """Read usage from a chat message, or from the raw message of a structured call."""
    message = result.get("raw") if isinstance(result, dict) else result
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return None, None
    return usage.get("input_tokens"), usage.get("output_tokens")


def _carries_text(message: Any) -> bool:
    content = getattr(message, "content", message)
    return isinstance(content, str) and bool(content.strip())


class OpenAIStructuredProvider:
    """LLMProvider backed by OpenAI through LangChain.

    The chat model is built on first use rather than at construction so that an
    application without credentials still starts, and only fails when something
    actually asks for a completion.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: BaseChatModel | None = None

    def _chat_model(self) -> BaseChatModel:
        if self._model is not None:
            return self._model

        if self._settings.openai_api_key is None:
            raise LLMConfigurationError("OPENAI_API_KEY is not configured")

        from langchain_openai import ChatOpenAI

        self._model = ChatOpenAI(
            model=self._settings.llm_model,
            api_key=self._settings.openai_api_key,
            temperature=self._settings.llm_temperature,
            timeout=self._settings.llm_timeout_seconds,
            # We retry ourselves so attempts use our timeout and logging.
            max_retries=0,
        )
        return self._model

    async def generate_structured[ModelT: BaseModel](
        self, prompt: Prompt, schema: type[ModelT]
    ) -> ModelT:
        # Strict schema enforces shape; include_raw exposes token usage.
        runnable = self._chat_model().with_structured_output(
            schema, method="json_schema", strict=True, include_raw=True
        )

        result = await self._invoke_with_retries(
            runnable,
            prompt,
            accept=lambda value: (
                isinstance(value, dict) and isinstance(value.get("parsed"), schema)
            ),
        )

        return cast(ModelT, result["parsed"])

    async def generate_text(self, prompt: Prompt) -> str:
        message = await self._invoke_with_retries(self._chat_model(), prompt, accept=_carries_text)

        return cast(str, getattr(message, "content", message)).strip()

    async def _invoke_with_retries(
        self, runnable: Any, prompt: Prompt, accept: Callable[[Any], bool]
    ) -> Any:
        """Run one model call, bounded by both a timeout and an attempt count.

        A response that fails ``accept`` is treated like any other transient
        failure: the provider produced something unusable, and another attempt
        may well produce something usable.
        """
        messages = [SystemMessage(content=prompt.system), HumanMessage(content=prompt.user)]

        last_error: Exception | None = None

        for attempt in range(1, self._settings.llm_max_attempts + 1):
            try:
                async with asyncio.timeout(self._settings.llm_timeout_seconds):
                    result = await runnable.ainvoke(messages)
                # Record usage per attempt; unusable responses are still billed.
                record_token_usage(*_token_usage(result))
            except _CREDENTIAL_ERRORS as error:
                logger.error(
                    "llm_credentials_rejected",
                    extra={"prompt": prompt.name, "prompt_version": prompt.version},
                )
                raise LLMConfigurationError(
                    "the configured OpenAI credentials were rejected"
                ) from error
            except _REQUEST_ERRORS as error:
                # Our own request was malformed; retrying sends the same bad request.
                logger.error(
                    "llm_request_rejected",
                    extra={"prompt": prompt.name, "prompt_version": prompt.version},
                )
                raise LLMInvalidOutputError(f"provider rejected the request: {error}") from error
            except _RETRYABLE as error:
                last_error = error
                logger.warning(
                    "llm_request_failed",
                    extra={
                        "prompt": prompt.name,
                        "prompt_version": prompt.version,
                        "attempt": attempt,
                        "max_attempts": self._settings.llm_max_attempts,
                        "error_type": type(error).__name__,
                    },
                )
                continue

            if accept(result):
                return result

            last_error = LLMInvalidOutputError(f"{prompt.name} returned an unusable response")
            logger.warning(
                "llm_response_unusable",
                extra={"prompt": prompt.name, "attempt": attempt},
            )

        raise LLMUnavailableError(
            f"{prompt.name} failed after {self._settings.llm_max_attempts} attempts"
        ) from last_error
