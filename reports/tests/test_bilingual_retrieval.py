"""Bangla queries must reach English material, and provenance must stay English.

The measured weak spot was bn->en retrieval (top-1 0.167): a Bangla query could
not match English slide text at all. The fix indexes the Bangla side of every
chunk at upload time and scores a chunk on the better side, while the citation
still quotes the original English text.
"""

from __future__ import annotations

from pathlib import Path

from guru.education.documents import chunk_pages, extract_pages, search_chunks
from guru.storage import Storage

CHUNKS = [
    {
        "id": "c1",
        "document": "biology.pptx",
        "page": 1,
        "text": "Photosynthesis is the process by which green plants make food using sunlight.",
        "text_bn": "সালোকসংশ্লেষণ সেই প্রক্রিয়া যেখানে সবুজ উদ্ভিদ সূর্যালোক ব্যবহার করে খাদ্য তৈরি করে।",
    },
    {
        "id": "c2",
        "document": "physics.pptx",
        "page": 3,
        "text": "Voltage pushes the current and resistance opposes it.",
        "text_bn": "ভোল্টেজ প্রবাহকে চালায় এবং রোধ বাধা দেয়।",
    },
]


def test_bangla_query_matches_english_slide():
    hits = search_chunks("উদ্ভিদ কীভাবে সূর্যালোক দিয়ে খাদ্য তৈরি করে", CHUNKS, k=2)
    assert hits
    assert hits[0]["document"] == "biology.pptx"
    assert hits[0]["page"] == 1


def test_english_query_still_matches_without_using_bangla_side():
    hits = search_chunks("what opposes electric current", CHUNKS, k=2)
    assert hits[0]["document"] == "physics.pptx"


def test_citation_quotes_the_original_english_text():
    hits = search_chunks("রোধ কী করে", CHUNKS, k=1)
    assert hits[0]["text"].startswith("Voltage pushes the current")
    assert "resistance" in hits[0]["text"]


def test_chunks_without_bangla_side_behave_as_before():
    plain = [{"id": "c9", "document": "notes.txt", "page": 1, "text": "Chlorophyll is a green pigment."}]
    hits = search_chunks("chlorophyll", plain, k=1)
    assert hits and hits[0]["document"] == "notes.txt"


def test_empty_query_and_empty_corpus_return_nothing():
    assert search_chunks("", CHUNKS) == []
    assert search_chunks("chlorophyll", []) == []


def test_storage_round_trips_the_bangla_side(tmp_path: Path):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        sid = storage.create_session("class", "tok", "ABC123")
        storage.add_document(
            sid, "slides.txt", "txt", "hash1", 1,
            [{"page": 1, "text": "Chlorophyll captures light.", "text_bn": "ক্লোরোফিল আলো ধরে।"}],
        )
        chunks = storage.document_chunks(sid)
        assert chunks[0]["text_bn"] == "ক্লোরোফিল আলো ধরে।"
        hits = search_chunks("ক্লোরোফিল কী ধরে", chunks, k=1)
        assert hits[0]["text"] == "Chlorophyll captures light."
    finally:
        storage.close()


def test_bangla_source_document_needs_no_translation():
    pages = extract_pages("সালোকসংশ্লেষণ হলো উদ্ভিদের খাদ্য তৈরির প্রক্রিয়া।".encode(), "note.txt")
    chunks = chunk_pages(pages)
    assert chunks and chunks[0].page == 1
