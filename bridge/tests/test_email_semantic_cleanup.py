from __future__ import annotations

import pytest

from app.email_semantic_routing import classify_email_cleanup


@pytest.mark.parametrize(
    "utterance",
    (
        "Remove promotional and social emails and anything unread for over 3 days",
        "Get rid of promotions/social stuff, plus unread mail older than three days",
        "Clean both inboxes of social and promotional mail and unread stuff that's been sitting there more than 3 days",
    ),
)
def test_compound_cleanup_variations_produce_structured_or_scope(utterance: str) -> None:
    intent = classify_email_cleanup(utterance)

    assert intent is not None
    assert intent.operation == "trash"
    assert intent.combination == "OR"
    assert {clause.type for clause in intent.clauses} == {"category", "unread_age"}
    age = next(clause for clause in intent.clauses if clause.type == "unread_age")
    assert age.unread is True
    assert age.older_than_days == 3


@pytest.mark.parametrize(
    ("utterance", "provider_scope"),
    (
        ("Do that in Gmail and Outlook", "all"),
        ("Same for both accounts", "all"),
        ("Actually only Gmail", "google_gmail"),
        ("Actually leave Outlook alone", "google_gmail"),
    ),
)
def test_cleanup_provider_scope_clarifications_are_structured(
    utterance: str, provider_scope: str
) -> None:
    intent = classify_email_cleanup(utterance)

    assert intent is not None
    assert intent.provider_scope == provider_scope


def test_cleanup_scope_revision_can_remove_unread_clause_without_authority() -> None:
    intent = classify_email_cleanup("Don't do the unread ones")

    assert intent is not None
    assert set(intent.remove_clause_types) == {"unread", "unread_age"}
    assert intent.operation is None


def test_cleanup_classifier_does_not_capture_unrelated_long_sentence() -> None:
    assert (
        classify_email_cleanup(
            "Please compare the weather and calendar and tell me whether tomorrow looks busy"
        )
        is None
    )
