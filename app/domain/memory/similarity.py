import re
from collections.abc import Sequence
from math import sqrt

# Stop words, so unrelated sentences don't look similar.
_STOPWORDS = frozenset(
    """
    a an and are as at be been being but by can could did do does for from had has have
    he her him his how i if in into is it its me my of on or our she should so some than
    that the their them then there these they this to told too up us was we were what
    when which who will with would you your
    """.split()
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9']+")

# Above this, two memories say the same thing and one of them is redundant.
DUPLICATE_THRESHOLD = 0.7

# Same subject but possibly conflicting; only these pairs go to the model.
RELATED_THRESHOLD = 0.25


def tokenise(text: str) -> frozenset[str]:
    return frozenset(
        token for token in _TOKEN_PATTERN.findall(text.lower()) if token not in _STOPWORDS
    )


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Similarity between two embeddings, as cosine over the raw vectors.

    Used when both sides of a comparison were embedded with the same model.
    Unlike token overlap this recognises a paraphrase, which is what makes
    deduplication work on wording the lexical measure cannot see.
    """
    if len(left) != len(right) or not left:
        return 0.0

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))

    if not left_norm or not right_norm:
        return 0.0

    # Clamp floating-point overshoot above 1.
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def similarity(first: str, second: str) -> float:
    """How much two memories overlap, as Jaccard over meaningful tokens.

    The fallback for when either side has no embedding yet, and still the first
    thing tried because it costs nothing. Vector similarity is preferred where
    both sides have one, because it recognises paraphrases this cannot.
    """
    left = tokenise(first)
    right = tokenise(second)

    if not left or not right:
        return 0.0

    intersection = len(left & right)
    if not intersection:
        return 0.0

    return intersection / len(left | right)
