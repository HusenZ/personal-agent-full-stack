from dataclasses import dataclass

# Fence for user and retrieved content: data, never instructions.
UNTRUSTED_OPEN = "<<<BEGIN_UNTRUSTED_CONTENT>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_CONTENT>>>"

# Truncation bounds token cost and prompt-injection surface.
MAX_UNTRUSTED_LENGTH = 4000


@dataclass(frozen=True)
class Prompt:
    """A single model call's instructions and input.

    Carries a name and version so that a change in wording is attributable when
    evaluation results move.
    """

    name: str
    version: str
    system: str
    user: str


def fence_untrusted(content: str, *, max_length: int = MAX_UNTRUSTED_LENGTH) -> str:
    """Wrap untrusted text so it cannot escape into instruction position.

    The sentinels are stripped from the content first. Without that, a user could
    type the closing sentinel and have everything after it read as though it came
    from the system.
    """
    sanitised = content.replace(UNTRUSTED_OPEN, "").replace(UNTRUSTED_CLOSE, "")

    if len(sanitised) > max_length:
        sanitised = sanitised[:max_length] + "\n[truncated]"

    return f"{UNTRUSTED_OPEN}\n{sanitised}\n{UNTRUSTED_CLOSE}"
