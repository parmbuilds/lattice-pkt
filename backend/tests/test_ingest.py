import dataclasses
import zipfile

import pytest

from app.config import Settings
from app.ingest import Limits, Rejected, extract
from app.ingest.receipt import Flag
from app.ingest.runner import extract_in_child
from tests import hostile_files as hf

LIMITS = Limits.from_settings(Settings())


def _flags(extraction) -> dict[str, int]:
    return {flag.kind: flag.count for flag in extraction.receipt.flags}


def _excerpts(extraction) -> str:
    return "\n".join(hidden.text for hidden in extraction.receipt.hidden)


def _rejected(data: bytes, filename: str, limits: Limits = LIMITS) -> Rejected:
    with pytest.raises(Rejected) as caught:
        extract(data, filename, limits)
    return caught.value


# Sniffing ------------------------------------------------------------------------------------


def test_a_pdf_filename_on_a_docx_zip_is_detected_as_docx():
    extraction = extract(hf.clean_docx(), "report.pdf", LIMITS)
    assert (extraction.detected_type, extraction.parser) == ("docx", "python-docx")


def test_a_docx_filename_on_plain_text_is_rejected_as_unsupported():
    rejected = _rejected(b"just some text\n", "report.docx")
    assert (rejected.reason, rejected.status) == ("unsupported", 415)


def test_invalid_utf8_in_a_txt_file_is_rejected_for_its_encoding():
    rejected = _rejected(hf.INVALID_UTF8, "notes.txt")
    assert (rejected.reason, rejected.status) == ("encoding", 422)


# PDF -----------------------------------------------------------------------------------------


def test_a_pdf_with_a_text_page_and_a_no_text_page_flags_the_empty_page():
    extraction = extract(hf.pdf("Page one text.", None), "scan.pdf", LIMITS)
    assert (extraction.detected_type, extraction.parser) == ("pdf", "pypdf")
    assert extraction.body == "Page one text."
    assert [section.chars for section in extraction.receipt.sections] == [14, 0]
    assert extraction.receipt.sections[1].flags == ("empty_page",)
    assert extraction.receipt.flags == [Flag("empty_page", "page 2", 1)]


def test_a_clean_text_layer_pdf_carries_the_invisible_text_note_and_no_flags():
    extraction = extract(hf.pdf("Clean page."), "clean.pdf", LIMITS)
    assert extraction.body == "Clean page."
    assert extraction.receipt.flags == []
    assert extraction.receipt.notes == ["pdf_invisible_text_not_checked"]


def test_an_encrypted_pdf_is_rejected():
    assert _rejected(hf.encrypted_pdf(), "locked.pdf").reason == "encrypted"


def test_a_pdf_over_the_page_cap_is_rejected():
    limits = dataclasses.replace(LIMITS, max_pdf_pages=1)
    assert _rejected(hf.pdf("One.", "Two."), "long.pdf", limits).reason == "page_limit"


def test_a_pdf_with_a_broken_cross_reference_is_rejected_as_malformed():
    assert _rejected(b"%PDF-1.4\nnot really a pdf\n", "bad.pdf").reason == "malformed"


# DOCX ----------------------------------------------------------------------------------------


def test_docx_hidden_layers_stay_out_of_the_body_and_appear_as_excerpts():
    extraction = extract(hf.hidden_docx(), "memo.docx", LIMITS)
    assert extraction.body == "Visible intro.\n\nVisible end."
    flags = _flags(extraction)
    for kind in ("comment", "tracked_insertion", "tracked_deletion", "hidden_run", "white_text"):
        assert flags[kind] == 1, kind
    excerpts = _excerpts(extraction)
    for secret in (
        "COMMENT-SECRET",
        "INSERTED-SECRET",
        "DELETED-SECRET",
        "VANISH-SECRET",
        "WHITE-SECRET",
    ):
        assert secret not in extraction.body
        assert secret in excerpts


def test_a_docx_table_is_flagged_as_dropped_and_a_text_box_as_unaccounted():
    flags = _flags(extract(hf.hidden_docx(), "memo.docx", LIMITS))
    assert flags["dropped_table"] == 1
    assert flags["unaccounted_content"] == len("TEXTBOX-SECRET")


def test_a_clean_docx_has_its_paragraphs_and_no_flags():
    extraction = extract(hf.clean_docx(), "memo.docx", LIMITS)
    assert extraction.body == "Plain paragraph.\n\nSecond paragraph."
    assert extraction.receipt.flags == []


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (hf.many_member_zip(), "zip_limit"),
        (hf.ratio_bomb_zip(), "zip_limit"),
        (hf.traversal_zip(), "unsafe_zip"),
    ],
    ids=["2000-members", "1000-to-1-ratio", "traversal-name"],
)
def test_a_zip_beyond_its_bounds_is_rejected_before_any_member_is_read(monkeypatch, data, reason):
    def no_reads(*args, **kwargs):
        raise AssertionError("a member was opened")

    monkeypatch.setattr(zipfile.ZipFile, "open", no_reads)
    assert _rejected(data, "memo.docx").reason == reason


def test_a_zip_without_a_word_document_is_unsupported():
    rejected = _rejected(hf.zip_of({"notes.txt": b"hello"}), "notes.docx")
    assert (rejected.reason, rejected.status) == ("unsupported", 415)


# HTML and Markdown ---------------------------------------------------------------------------


def test_html_keeps_only_visible_text():
    extraction = extract(hf.HIDDEN_HTML, "page.html", LIMITS)
    assert extraction.body == "Ops\n\nVisible one.\n\nVisible two."
    assert _flags(extraction) == {"hidden_element": 2, "script": 1, "comment": 1}
    excerpts = _excerpts(extraction)
    for secret in ("HIDDEN-DIV", "SCRIPT_SECRET()", "COMMENT-SECRET", "STYLE-HIDDEN"):
        assert secret in excerpts


def test_an_external_stylesheet_makes_html_visibility_ambiguous():
    extraction = extract(hf.LINKED_STYLESHEET_HTML, "page.html", LIMITS)
    assert extraction.body == "Visible."
    assert _flags(extraction) == {"visibility_ambiguous": 1}


def test_a_wiki_export_with_classes_and_a_plain_stylesheet_has_no_flags():
    extraction = extract(hf.wiki_html(), "wiki.html", LIMITS)
    assert extraction.body == "Failover\n\nSwitch regions."
    assert extraction.receipt.flags == []


def test_a_hiding_declaration_in_a_style_block_makes_html_visibility_ambiguous():
    extraction = extract(hf.wiki_html(".x { display: none }"), "wiki.html", LIMITS)
    assert _flags(extraction) == {"visibility_ambiguous": 1}


def test_a_hiding_style_attribute_makes_html_visibility_ambiguous():
    extraction = extract(b'<p style="opacity: 0">Faint.</p>', "page.html", LIMITS)
    assert extraction.body == "Faint."
    assert _flags(extraction) == {"visibility_ambiguous": 1}


def test_markdown_html_comments_are_excluded_and_flagged():
    extraction = extract(hf.MARKDOWN_WITH_COMMENT, "runbook.md", LIMITS)
    assert extraction.detected_type == "md"
    assert extraction.body == "# Runbook\n\nVisible.\n\nMore.\n"
    assert _flags(extraction) == {"comment": 1}
    assert "COMMENT-SECRET" in _excerpts(extraction)


# Invisible characters ------------------------------------------------------------------------


def test_a_byte_order_mark_and_soft_hyphens_are_dropped_without_flags():
    extraction = extract(hf.BOM_SOFT_HYPHEN_TEXT, "notes.txt", LIMITS)
    assert extraction.body == "Cooperate with ops.\n"
    assert extraction.receipt.flags == []


def test_invisible_characters_are_stripped_flagged_and_shown_escaped():
    extraction = extract(hf.INVISIBLE_TEXT.encode(), "notes.txt", LIMITS)
    assert extraction.body == "Rotate keys.LIAME Tag done.\n"
    assert extraction.receipt.flags == [Flag("invisible_chars", "body", 6)]
    assert _excerpts(extraction) == r"\u200b\u202e\u202c\U000e0041\U000e0042\U000e007f"


# Receipt hash --------------------------------------------------------------------------------


def test_the_receipt_hash_is_stable_and_changes_when_a_flag_changes():
    first = extract(hf.HIDDEN_HTML, "page.html", LIMITS).receipt.sha256
    assert extract(hf.HIDDEN_HTML, "page.html", LIMITS).receipt.sha256 == first
    one_more_comment = hf.HIDDEN_HTML.replace(b"</body>", b"<!-- another --></body>")
    assert extract(one_more_comment, "page.html", LIMITS).receipt.sha256 != first


# Child-process runner ------------------------------------------------------------------------


async def test_the_child_process_returns_the_same_extraction():
    data = hf.hidden_docx()
    assert await extract_in_child(data, "memo.docx", LIMITS) == extract(data, "memo.docx", LIMITS)


async def test_a_rejection_in_the_child_process_reaches_the_caller():
    with pytest.raises(Rejected) as caught:
        await extract_in_child(hf.encrypted_pdf(), "locked.pdf", LIMITS)
    assert (caught.value.reason, caught.value.status) == ("encrypted", 422)


async def test_an_extraction_past_its_timeout_is_rejected():
    limits = dataclasses.replace(LIMITS, timeout_seconds=0.001)
    with pytest.raises(Rejected) as caught:
        await extract_in_child(hf.pdf("Slow."), "slow.pdf", limits)
    assert caught.value.reason == "timeout"
