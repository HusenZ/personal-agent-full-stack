from dataclasses import dataclass
from enum import StrEnum


class EvidenceKind(StrEnum):
    GOAL = "goal"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    OBSERVATION = "observation"


@dataclass(frozen=True)
class EvidenceItem:
    """One thing known about the user, addressable by a stable id.

    The id is what a generated claim cites, and what grounding is checked
    against, so it must be stable within a bundle and meaningful to read in a
    stored analysis long after it was produced.
    """

    id: str
    kind: EvidenceKind
    content: str


@dataclass(frozen=True)
class EvidenceBundle:
    items: tuple[EvidenceItem, ...]

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.items)

    def is_empty(self) -> bool:
        return not self.items

    def render(self) -> str:
        """Format the bundle for a prompt, one citable line per item."""
        return "\n".join(f"[{item.id}] ({item.kind.value}) {item.content}" for item in self.items)


@dataclass(frozen=True)
class RawEvidence:
    """The user's structured profile, before ids are assigned.

    Later phases add semantic memories and temporal events here; the bundle
    builder is the single place that has to change when they do.
    """

    goals: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    preferences: tuple[tuple[str, str], ...] = ()
    observations: tuple[str, ...] = ()


def build_evidence_bundle(raw: RawEvidence) -> EvidenceBundle:
    """Assign citable ids to everything known about the user.

    Ids are positional rather than database keys so that a stored analysis stays
    readable, and so the bundle can be rebuilt identically from a snapshot.
    """
    items: list[EvidenceItem] = []

    for index, goal in enumerate(raw.goals, start=1):
        items.append(EvidenceItem(id=f"goal:{index}", kind=EvidenceKind.GOAL, content=goal))

    for index, constraint in enumerate(raw.constraints, start=1):
        items.append(
            EvidenceItem(id=f"constraint:{index}", kind=EvidenceKind.CONSTRAINT, content=constraint)
        )

    for key, value in raw.preferences:
        items.append(
            EvidenceItem(
                id=f"preference:{key}", kind=EvidenceKind.PREFERENCE, content=f"{key}: {value}"
            )
        )

    for index, observation in enumerate(raw.observations, start=1):
        items.append(
            EvidenceItem(
                id=f"observation:{index}", kind=EvidenceKind.OBSERVATION, content=observation
            )
        )

    return EvidenceBundle(items=tuple(items))
