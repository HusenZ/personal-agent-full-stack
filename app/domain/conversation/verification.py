from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

# Shorter quotes match too easily to locate anything.
MIN_QUOTE_LENGTH = 8


class UnsupportedReason(StrEnum):
    # Asserts something about the user that nothing they said supports.
    UNSUPPORTED = "unsupported"
    # Asserts something about the user that what they said contradicts.
    CONTRADICTED = "contradicted"
    # States a figure about their activity that differs from the exact figures.
    WRONG_FIGURE = "wrong_figure"


class UnsupportedStatement(BaseModel):
    quote: str = Field(
        min_length=1,
        max_length=300,
        description="The statement exactly as it appears in the reply.",
    )
    reason: UnsupportedReason


class AnswerVerification(BaseModel):
    """A model's list of statements in a reply that its context does not support.

    Findings, not a verdict: whether the reply is grounded is decided by which
    findings survive checking against the reply, not by a flag the model sets.
    A verifier asked for a boolean can say "false" without being able to point
    at anything wrong.
    """

    unsupported: list[UnsupportedStatement] = Field(default_factory=list, max_length=10)


@dataclass(frozen=True)
class VerificationPolicy:
    enabled: bool = True

    # Includes the first draft, so 2 allows one regeneration.
    max_generation_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_generation_attempts < 1:
            raise ValueError("max_generation_attempts must be at least 1")


DEFAULT_VERIFICATION_POLICY = VerificationPolicy()


def confirm_findings(
    reply: str, verification: AnswerVerification
) -> tuple[UnsupportedStatement, ...]:
    """Keep only the findings that point at text the reply actually contains.

    Checked here rather than trusted: a verifier will quote statements that are
    not there, and a regeneration told to remove them would be chasing nothing.
    A finding that cannot be located is dropped, which errs towards accepting
    the reply; the alternative is rejecting replies on the verifier's say-so.
    """
    haystack = _normalise(reply)
    confirmed: list[UnsupportedStatement] = []
    seen: set[str] = set()

    for finding in verification.unsupported:
        needle = _normalise(finding.quote)
        if len(needle) < MIN_QUOTE_LENGTH or needle not in haystack or needle in seen:
            continue
        seen.add(needle)
        confirmed.append(finding)

    return tuple(confirmed)


def _normalise(text: str) -> str:
    # Models swap typographic and ASCII quotes; that's not a misquote.
    translated = text.translate(_TYPOGRAPHY)
    return " ".join(translated.split()).casefold().strip(" .,;:!?\"'")


_TYPOGRAPHY = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)
