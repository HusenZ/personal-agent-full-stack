from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

ClaimText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

# Cap claims per analysis; more is just noise.
MAX_CLAIMS = 40


class ClaimType(StrEnum):
    """How strongly a statement about the user is being asserted.

    The distinction is the product's central safety rule: an inference dressed
    up as a fact is the failure mode that makes a personal assistant untrustworthy.
    """

    FACT = "fact"
    OBSERVATION = "observation"
    INFERENCE = "inference"
    RECOMMENDATION = "recommendation"
    UNCERTAINTY = "uncertainty"


class AnalysisSection(StrEnum):
    CURRENT_SITUATION = "current_situation"
    GOALS = "goals"
    STRENGTHS = "strengths"
    CONSTRAINTS = "constraints"
    BOTTLENECKS = "bottlenecks"
    PRIORITIES = "priorities"
    SUGGESTED_ACTIONS = "suggested_actions"


class AnalysisClaim(BaseModel):
    section: AnalysisSection
    claim_type: ClaimType
    content: ClaimText
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class AnalysisResult(BaseModel):
    """The structured-output schema the model is constrained to."""

    claims: list[AnalysisClaim] = Field(default_factory=list, max_length=MAX_CLAIMS)
