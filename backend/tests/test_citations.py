"""U5: the per-sentence citation check (KTD6; R8, R9, R10)."""

import json
from pathlib import Path

from app.ai.citations import check, complete_lines, outcome

LABELS = json.loads((Path(__file__).parent / "data" / "citation_labels.json").read_text())
# The share of hand-labeled supported sentences the check must pass; every unsupported one fails.
SUPPORTED_SHARE = 0.85

SENT = {"P1": "Restart the checkout replicas one at a time.", "P2": "Flush the legacy cache."}


def test_a_sentence_sharing_wording_with_the_passage_it_cites_passes():
    assert check("Restart checkout replicas one at a time. [P1]", SENT) == {
        "text": "Restart checkout replicas one at a time.",
        "markers": ["P1"],
        "supported": True,
    }


def test_a_sentence_citing_an_id_that_was_never_sent_fails():
    assert check("Restart checkout replicas one at a time. [P7]", SENT)["supported"] is False


def test_a_sentence_with_no_marker_fails():
    result = check("Restart checkout replicas one at a time.", SENT)
    assert result == {
        "text": "Restart checkout replicas one at a time.",
        "markers": [],
        "supported": False,
    }


def test_a_sentence_sharing_only_stopwords_fails():
    assert check("It was the party at a lake. [P1]", SENT)["supported"] is False


def test_an_inverted_step_fails_on_its_negation():
    sent = {"P1": "Do not fail over the primary while replication lag exceeds 5 s."}
    inverted = "Fail over the primary while replication lag exceeds 5 s. [P1]"
    assert check(inverted, sent)["supported"] is False
    assert check("Do not fail over the primary while replication lag exceeds 5 s. [P1]", sent)[
        "supported"
    ]


def test_every_cited_passage_must_share_the_wording():
    assert check("Restart checkout replicas one at a time. [P1] [P2]", SENT)["supported"] is False


def test_every_labeled_unsupported_sentence_fails_and_most_supported_ones_pass():
    passages = LABELS["passages"]
    passed = [s for s in LABELS["unsupported"] if check(s, passages)["supported"]]
    assert passed == []
    supported = [check(s, passages)["supported"] for s in LABELS["supported"]]
    assert sum(supported) / len(supported) >= SUPPORTED_SHARE


def test_html_escaped_wording_from_the_prompt_is_restored():
    sent = {"P1": "Set retries < 3 & alert."}
    assert check("Set retries &lt; 3 &amp; alert. [P1]", sent) == {
        "text": "Set retries < 3 & alert.",
        "markers": ["P1"],
        "supported": True,
    }


def test_streamed_text_is_released_one_completed_line_at_a_time():
    assert complete_lines("First. [P1]\n\nSecond") == (["First. [P1]"], "Second")
    assert complete_lines("no newline yet") == ([], "no newline yet")


def test_outcome_is_not_covered_when_no_sentence_passes():
    supported = {"supported": True}
    failed = {"supported": False}
    assert outcome([failed, failed]) == "not_covered"  # covers AE3
    assert outcome([]) == "not_covered"
    assert outcome([supported, failed]) == "partly_supported"
    assert outcome([supported]) == "answered"
