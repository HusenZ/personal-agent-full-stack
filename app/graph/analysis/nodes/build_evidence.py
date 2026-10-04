from app.domain.analysis.evidence import RawEvidence, build_evidence_bundle
from app.graph.analysis.state import AnalysisState


async def build_evidence(state: AnalysisState) -> AnalysisState:
    """Assign citable ids to what is known about the user."""
    raw = state.get("raw_evidence") or RawEvidence()

    return {"evidence": build_evidence_bundle(raw)}
