class LLMError(Exception):
    """Base for every failure of the model provider.

    Provider and framework exceptions are translated into this hierarchy at the
    infrastructure boundary so that application and graph code never has to
    import an SDK to handle a failure.
    """


class LLMConfigurationError(LLMError):
    """The provider cannot be used at all, for example a missing API key."""


class LLMUnavailableError(LLMError):
    """The provider failed in a way that retrying could plausibly fix."""


class LLMInvalidOutputError(LLMError):
    """The model returned something that does not satisfy the requested schema."""
