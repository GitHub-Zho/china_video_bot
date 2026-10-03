import understanding
import pytest

from understanding import (
    AIBaseline,
    HumanReviewOverlay,
    SemanticSegment,
    VideoUnderstandingV1,
)


FIRST_ID = "seg_2d6ce0d4-2d18-4fb1-bc81-a826df031572"
SECOND_ID = "seg_a6d48531-d707-4c76-ae17-4c5335a30ee3"
ADDED_ID = "seg_9cc18bba-42d8-4fe1-a7db-bfe640f04b65"


def segment(segment_id, start, end, summary, description, salience=0.8, confidence=0.9):
    return SemanticSegment(
        segment_id=segment_id,
        start_sec=start,
        end_sec=end,
        semantic_summary=summary,
        visual_description=description,
        source_salience=salience,
        confidence=confidence,
    )


def payload(review=None):
    return VideoUnderstandingV1(
        source_duration_sec=20,
        ai_baseline=AIBaseline(
            global_summary="AI summary",
            segments=(
                segment(FIRST_ID, 0, 10, "AI first", "First view"),
                segment(SECOND_ID, 10, 20, "AI second", "Second view"),
            ),
        ),
        human_review=review or HumanReviewOverlay(),
    )


def test_effective_view_uses_lock_then_override_then_baseline():
    """Catch a lower-priority AI or override value replacing a confirmed lock."""
    derive_summary = getattr(understanding, "effective_global_summary", None)
    derive_segments = getattr(understanding, "effective_segments", None)
    assert derive_summary is not None and derive_segments is not None

    reviewed = payload(
        HumanReviewOverlay(
            global_overrides={"global_summary": "Human summary"},
            global_locks={"global_summary": "Human summary"},
            segment_overrides={FIRST_ID: {"semantic_summary": "Human first"}},
            segment_locks={FIRST_ID: {"semantic_summary": "Human first"}},
        )
    )

    assert derive_summary(reviewed) == "Human summary"
    assert derive_segments(reviewed)[0].semantic_summary == "Human first"
    assert derive_segments(reviewed)[1].semantic_summary == "AI second"


def test_effective_segments_remove_add_sort_and_preserve_score_ownership():
    """Catch removed AI segments leaking back or human segments gaining AI scores."""
    derive_segments = getattr(understanding, "effective_segments", None)
    assert derive_segments is not None
    added = segment(
        ADDED_ID,
        2,
        8,
        "Human segment",
        "Human view",
        salience=None,
        confidence=None,
    )
    reviewed = payload(
        HumanReviewOverlay(
            removed_segment_ids=(FIRST_ID,),
            added_segments=(added,),
            segment_overrides={ADDED_ID: {"start_sec": 1}},
        )
    )

    effective = derive_segments(reviewed)

    assert [item.segment_id for item in effective] == [ADDED_ID, SECOND_ID]
    assert effective[0].start_sec == 1
    assert effective[0].source_salience is None
    assert effective[0].confidence is None
    assert effective[1].source_salience == 0.8
    assert effective[1].confidence == 0.9


def test_draft_edits_and_locks_without_mutating_the_base_payload():
    """Catch review edits overwriting the immutable AI baseline or a locked snapshot."""
    draft_type = getattr(understanding, "UnderstandingDraft", None)
    assert draft_type is not None
    base = payload()
    draft = draft_type.from_payload(base)

    draft.set_global_summary("Reviewed summary")
    draft.lock_global_summary()
    draft.set_global_summary("Reviewed summary again")
    draft.set_segment_field(FIRST_ID, "semantic_summary", "Reviewed first")
    draft.lock_segment_field(FIRST_ID, "semantic_summary")
    draft.set_segment_field(FIRST_ID, "semantic_summary", "Reviewed first again")
    saved = draft.to_payload()

    assert base.ai_baseline.global_summary == "AI summary"
    assert base.ai_baseline.segments[0].semantic_summary == "AI first"
    assert saved.human_review.global_overrides == {
        "global_summary": "Reviewed summary again"
    }
    assert saved.human_review.global_locks == {
        "global_summary": "Reviewed summary again"
    }
    assert saved.human_review.segment_locks[FIRST_ID]["semantic_summary"] == (
        "Reviewed first again"
    )


def test_draft_add_remove_and_restore_preserve_structural_history():
    """Catch structural review deleting baseline history or leaving removed additions behind."""
    draft_type = getattr(understanding, "UnderstandingDraft", None)
    assert draft_type is not None
    draft = draft_type.from_payload(payload())

    draft.remove_segment(FIRST_ID)
    added_id = draft.add_segment(1, 5, "Added", "Added view")
    first = draft.to_payload()

    assert FIRST_ID in first.human_review.removed_segment_ids
    assert FIRST_ID in {item.segment_id for item in first.ai_baseline.segments}
    assert added_id in {item.segment_id for item in first.human_review.added_segments}

    draft.remove_segment(added_id)
    draft.restore_baseline_segment(FIRST_ID)
    restored = draft.to_payload()
    assert FIRST_ID not in restored.human_review.removed_segment_ids
    assert added_id not in {item.segment_id for item in restored.human_review.added_segments}


def test_draft_split_replaces_effective_segment_with_two_new_added_segments():
    """Catch split operations mutating the original baseline identity or range history."""
    draft_type = getattr(understanding, "UnderstandingDraft", None)
    assert draft_type is not None
    draft = draft_type.from_payload(payload())

    left_id, right_id = draft.split_segment(FIRST_ID, 4)
    saved = draft.to_payload()
    effective = understanding.effective_segments(saved)

    assert FIRST_ID in saved.human_review.removed_segment_ids
    assert {left_id, right_id}.isdisjoint({FIRST_ID, SECOND_ID})
    assert [(item.start_sec, item.end_sec) for item in effective] == [
        (0.0, 4.0),
        (4.0, 10.0),
        (10.0, 20.0),
    ]


def test_draft_merge_requires_consecutive_segments_and_saves_one_full_segment():
    """Catch merges that accept non-consecutive selections or persist patch fragments."""
    draft_type = getattr(understanding, "UnderstandingDraft", None)
    assert draft_type is not None
    third_id = "seg_884693f5-c8f7-46b5-bfab-94a9f431e86d"
    base = VideoUnderstandingV1(
        30,
        AIBaseline(
            "Summary",
            (
                segment(FIRST_ID, 0, 10, "One", "View one"),
                segment(SECOND_ID, 10, 20, "Two", "View two"),
                segment(third_id, 20, 30, "Three", "View three"),
            ),
        ),
        HumanReviewOverlay(),
    )
    draft = draft_type.from_payload(base)

    with pytest.raises(ValueError, match="consecutive"):
        draft.merge_segments(
            (FIRST_ID, third_id),
            semantic_summary="Invalid",
            visual_description="Invalid view",
        )

    merged_id = draft.merge_segments(
        (FIRST_ID, SECOND_ID),
        semantic_summary="Merged action",
        visual_description="Merged view",
    )
    effective = understanding.effective_segments(draft.to_payload())
    assert [(item.segment_id, item.start_sec, item.end_sec) for item in effective] == [
        (merged_id, 0.0, 20.0),
        (third_id, 20.0, 30.0),
    ]
