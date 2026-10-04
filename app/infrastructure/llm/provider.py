from typing import Protocol

from pydantic import BaseModel

from app.infrastructure.llm.prompt import Prompt


class LLMProvider(Protocol):
    """The only way the rest of the system talks to a language model."""

    async def generate_structured[ModelT: BaseModel](
        self, prompt: Prompt, schema: type[ModelT]
    ) -> ModelT:
        """Produce a validated object.

        Preferred wherever the result feeds application logic, because free-form
        text would have to be parsed before it could be trusted.
        """
        ...

    async def generate_text(self, prompt: Prompt) -> str:
        """Produce prose meant to be read by a person.

        Separate from the structured path because forcing a conversational reply
        through a JSON schema degrades it, and because a reply is what a future
        streaming endpoint needs to emit token by token.
        """
        ...
