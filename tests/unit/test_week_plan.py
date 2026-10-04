from datetime import date

from app.domain.planning.week import (
    MAX_BLOCKS_PER_DAY,
    BlockCategory,
    ProposedBlock,
    ProposedWeek,
    check_week,
    plan_dates,
)

FIRST_DAY = date(2026, 10, 3)
EVIDENCE = frozenset({"goal:1", "constraint:1"})


def block(day: int = 0, start: str | None = "18:00", minutes: int = 45, **extra: object):
    return ProposedBlock(
        day=day,
        start=start,
        duration_minutes=minutes,
        title=str(extra.get("title", "Easy run")),
        category=BlockCategory(extra.get("category", "health")),
        reason="Builds towards the half marathon.",
        evidence_ids=list(extra.get("evidence_ids", ["goal:1"])),  # type: ignore[call-overload]
    )


def check(*blocks: ProposedBlock):
    return check_week(
        ProposedWeek(summary="A steady week.", blocks=list(blocks)), FIRST_DAY, EVIDENCE
    )


class TestPlacement:
    def test_days_are_numbered_from_the_first_day(self):
        result = check(block(day=0), block(day=6))

        assert [b.on for b in result.blocks] == [date(2026, 10, 3), date(2026, 10, 9)]
        assert plan_dates(FIRST_DAY)[-1] == date(2026, 10, 9)

    def test_a_day_outside_the_week_is_rejected(self):
        result = check(block(day=7), block(day=-1))

        assert result.blocks == ()
        assert result.rejected == {"day_out_of_range": 2}

    def test_each_day_is_ordered_by_time_with_anytime_blocks_last(self):
        result = check(
            block(start=None, title="Read"),
            block(start="19:00", title="Stretch", minutes=15),
            block(start="07:30", title="Run"),
        )

        assert [b.title for b in result.blocks] == ["Run", "Stretch", "Read"]


class TestRules:
    def test_overlapping_timed_blocks_keep_the_earlier(self):
        result = check(block(start="18:00", minutes=60), block(start="18:30", title="Call"))

        assert [b.title for b in result.blocks] == ["Easy run"]
        assert result.rejected == {"overlap": 1}

    def test_back_to_back_blocks_do_not_overlap(self):
        result = check(block(start="18:00", minutes=30), block(start="18:30", title="Stretch"))

        assert len(result.blocks) == 2

    def test_a_day_is_capped(self):
        result = check(
            *[block(start=None, title=f"Task {n}") for n in range(MAX_BLOCKS_PER_DAY + 2)]
        )

        assert len(result.blocks) == MAX_BLOCKS_PER_DAY
        assert result.rejected == {"too_many_in_day": 2}

    def test_impossible_times_and_durations_are_rejected(self):
        result = check(
            block(start="25:00"),
            block(start="7pm"),
            block(minutes=0),
            block(minutes=24 * 60),
            block(start="23:30", minutes=60),
        )

        assert result.blocks == ()
        assert result.rejected == {
            "bad_time": 2,
            "bad_duration": 2,
            "past_midnight": 1,
        }

    def test_an_invented_citation_is_dropped_but_the_block_kept(self):
        result = check(block(evidence_ids=["goal:1", "goal:9"]))

        assert result.blocks[0].evidence_ids == ("goal:1",)
        assert result.rejected == {"unknown_evidence": 1}
