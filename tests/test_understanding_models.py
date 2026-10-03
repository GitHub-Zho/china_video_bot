import json

import pytest

from understanding import (
    AIBaseline,
    HumanReviewOverlay,
    SemanticSegment,
    TranscriptSpan,
    TranscriptV1,
    VideoUnderstandingV1,
)


BASELINE_ID = "seg_2d6ce0d4-2d18-4fb1-bc81-a826df031572"
ADDED_ID = "seg_9cc18bba-42d8-4fe1-a7db-bfe640f04b65"


def make_segment(segment_id=BASELINE_ID, **overrides):
    values = {
        "segment_id": segment_id,
        "start_sec": 0.0,
        "end_sec": 18.5,
        "semantic_summary": "The cook separates the skin from the meat.",
        "visual_description": "A hand works beneath the skin with a narrow tool.",
        "source_salience": 0.86,
        "confidence": 0.91,
    }
    values.update(overrides)
    return SemanticSegment(**values)


def make_understanding(**overrides):
    values = {
        "source_duration_sec": 20.0,
        "ai_baseline": AIBaseline(
            global_summary="The source demonstrates the preparation process.",
            segments=(make_segment(),),
        ),
        "human_review": HumanReviewOverlay(),
    }
    values.update(overrides)
    return VideoUnderstandingV1(**values)


def test_transcript_round_trip_uses_canonical_json_bytes():
    """Catch unstable bytes or a wire round trip that loses transcript fields."""
    transcript = TranscriptV1(
        source_duration_sec=41,
        language=" zh-CN ",
        speech_status="speech",
        spans=(
            TranscriptSpan(
                start_sec=1.2,
                end_sec=4.8,
                text=" 先把鸭皮和肉分离。 ",
                speaker_label=None,
                confidence=0.94,
            ),
        ),
    )

    encoded = transcript.to_json_bytes()

    assert encoded.endswith(b"\n")
    assert b" " not in encoded
    assert json.loads(encoded) == transcript.to_dict()
    assert TranscriptV1.from_json_bytes(encoded) == transcript
    assert transcript.language == "zh-CN"
    assert transcript.spans[0].text == "先把鸭皮和肉分离。"


@pytest.mark.parametrize(
    "values, match",
    [
        (
            {
                "schema_version": "transcript.v1",
                "source_duration_sec": 41.0,
                "language": None,
                "speech_status": "no_speech",
                "spans": [],
                "extra": True,
            },
            "unknown fields",
        ),
        (
            {
                "schema_version": "transcript.v1",
                "source_duration_sec": True,
                "language": None,
                "speech_status": "no_speech",
                "spans": [],
            },
            "source_duration_sec",
        ),
        (
            {
                "schema_version": "transcript.v1",
                "source_duration_sec": 41.0,
                "language": "not_a_language",
                "speech_status": "no_speech",
                "spans": [],
            },
            "language",
        ),
    ],
)
def test_transcript_rejects_non_contract_payloads(values, match):
    """Catch permissive parsing of unknown, boolean-number, or invalid language values."""
    with pytest.raises(ValueError, match=match):
        TranscriptV1.from_dict(values)


@pytest.mark.parametrize(
    "speech_status, spans",
    [
        ("speech", ()),
        (
            "no_speech",
            (TranscriptSpan(0, 1, "speech", None, None),),
        ),
    ],
)
def test_transcript_enforces_speech_status_and_span_consistency(speech_status, spans):
    """Catch successful transcripts whose status contradicts their recognized spans."""
    with pytest.raises(ValueError, match="speech_status"):
        TranscriptV1(10, None, speech_status, spans)


def test_transcript_requires_chronological_bounded_spans():
    """Catch out-of-order or out-of-duration transcript evidence."""
    with pytest.raises(ValueError, match="chronological"):
        TranscriptV1(
            10,
            "en",
            "speech",
            (
                TranscriptSpan(5, 7, "later", None, None),
                TranscriptSpan(2, 6, "overlap is allowed", None, None),
            ),
        )
    with pytest.raises(ValueError, match="source_duration_sec"):
        TranscriptV1(
            10,
            "en",
            "speech",
            (TranscriptSpan(9, 11, "too late", None, None),),
        )


def test_understanding_round_trip_preserves_sparse_review_and_canonical_bytes():
    """Catch serialization that expands, drops, or reorders the sparse review payload."""
    added = make_segment(
        ADDED_ID,
        start_sec=0,
        end_sec=9,
        source_salience=None,
        confidence=None,
    )
    review = HumanReviewOverlay(
        global_overrides={"global_summary": "Human-corrected summary."},
        global_locks={"global_summary": "Human-corrected summary."},
        removed_segment_ids=(BASELINE_ID,),
        added_segments=(added,),
        segment_overrides={ADDED_ID: {"semantic_summary": "Careful separation."}},
        segment_locks={ADDED_ID: {"semantic_summary": "Careful separation."}},
    )
    understanding = make_understanding(human_review=review)

    encoded = understanding.to_json_bytes()

    assert encoded.endswith(b"\n")
    assert VideoUnderstandingV1.from_json_bytes(encoded) == understanding
    assert json.loads(encoded)["human_review"]["removed_segment_ids"] == [BASELINE_ID]


@pytest.mark.parametrize(
    "segment_id",
    [
        "2d6ce0d4-2d18-4fb1-bc81-a826df031572",
        "seg_2d6ce0d4-2d18-1fb1-bc81-a826df031572",
        "seg_not-a-uuid",
    ],
)
def test_semantic_segment_requires_canonical_uuid4_identity(segment_id):
    """Catch segment identities that encode order/time or are not opaque UUIDv4 values."""
    with pytest.raises(ValueError, match="segment_id"):
        make_segment(segment_id)


def test_understanding_rejects_overlapping_baseline_segments():
    """Catch a baseline that cannot form the chronological semantic review unit."""
    second = make_segment(
        "seg_a6d48531-d707-4c76-ae17-4c5335a30ee3",
        start_sec=18,
        end_sec=20,
    )
    baseline = AIBaseline(
        "Summary",
        (make_segment(), second),
    )

    with pytest.raises(ValueError, match="overlap"):
        make_understanding(ai_baseline=baseline)


@pytest.mark.parametrize(
    "review_factory, match",
    [
        (
            lambda: HumanReviewOverlay(removed_segment_ids=(ADDED_ID,)),
            "removed_segment_ids",
        ),
        (
            lambda: HumanReviewOverlay(
                segment_overrides={BASELINE_ID: {"confidence": 0.1}}
            ),
            "reviewable",
        ),
        (
            lambda: HumanReviewOverlay(
                segment_overrides={BASELINE_ID: {"semantic_summary": "A"}},
                segment_locks={BASELINE_ID: {"semantic_summary": "B"}},
            ),
            "agree",
        ),
    ],
)
def test_understanding_rejects_invalid_overlay_references_or_fields(
    review_factory, match
):
    """Catch overlays that reference missing segments or alter non-reviewable AI scores."""
    with pytest.raises(ValueError, match=match):
        make_understanding(human_review=review_factory())


def test_understanding_rejects_effective_time_overlap_from_an_override():
    """Catch individually valid overrides that make the effective segment tree overlap."""
    second_id = "seg_a6d48531-d707-4c76-ae17-4c5335a30ee3"
    baseline = AIBaseline(
        "Summary",
        (
            make_segment(end_sec=10),
            make_segment(second_id, start_sec=10, end_sec=20),
        ),
    )
    review = HumanReviewOverlay(
        segment_overrides={second_id: {"start_sec": 9.5}}
    )

    with pytest.raises(ValueError, match="effective segments overlap"):
        make_understanding(ai_baseline=baseline, human_review=review)


def test_understanding_from_dict_rejects_unknown_nested_fields():
    """Catch schema drift hidden inside a valid-looking nested segment object."""
    values = make_understanding().to_dict()
    values["ai_baseline"]["segments"][0]["shot_type"] = "close_up"

    with pytest.raises(ValueError, match="unknown fields"):
        VideoUnderstandingV1.from_dict(values)
