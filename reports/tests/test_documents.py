from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from guru.education.documents import (
    DocumentError,
    chunk_pages,
    embed_texts,
    extract_pages,
    search_chunks,
)
from guru.education.quiz import build_quiz
from guru.education.summary import build_summary
from guru.server.app import create_app
from guru.storage import Storage

from .conftest import FakeTranslator


# --------------------------------------------------------------------- fixtures
def make_pdf(pages: list[str]) -> bytes:
    """Smallest valid multi-page PDF with Helvetica text, no external tools."""
    font_id = 3 + 2 * len(pages)
    page_ids = [3 + 2 * i for i in range(len(pages))]
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
    ]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {page_ids[i] + 1} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode()
        )
        objs.append(
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"
        )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def make_pptx(slides: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for number, text in enumerate(slides, start=1):
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
                ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>"
                f"<a:t>{text}</a:t>"
                "</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            )
            zf.writestr(f"ppt/slides/slide{number}.xml", xml)
    return buffer.getvalue()


PPTX_SLIDES = [
    "Photosynthesis is the process by which green plants make food using sunlight.",
    "Chlorophyll is a green pigment that captures light energy inside the leaf.",
    "Stomata is a small pore on the leaf surface that lets gases in.",
]

PASSAGES = [
    {"document": "biology.pptx", "page": 1, "text": PPTX_SLIDES[0]},
    {"document": "biology.pptx", "page": 2, "text": PPTX_SLIDES[1]},
    {"document": "biology.pptx", "page": 3, "text": PPTX_SLIDES[2]},
]


# -------------------------------------------------------------------- extraction
def test_text_file_is_a_single_page():
    pages = extract_pages(b"Water boils at 100 degrees Celsius.", "notes.txt")
    assert len(pages) == 1 and pages[0].number == 1
    assert "Water boils" in pages[0].text


def test_pdf_keeps_page_numbers():
    pages = extract_pages(make_pdf(["Sunlight is energy from the sun.", "Roots absorb water."]), "deck.pdf")
    assert [p.number for p in pages] == [1, 2]
    assert "Sunlight" in pages[0].text
    assert "Roots" in pages[1].text


def test_pptx_preserves_slide_numbers():
    pages = extract_pages(make_pptx(PPTX_SLIDES), "biology.pptx")
    assert [p.number for p in pages] == [1, 2, 3]
    assert "Chlorophyll" in pages[1].text


def test_unsupported_type_is_rejected():
    with pytest.raises(DocumentError, match="Unsupported file type"):
        extract_pages(b"MZ\x90\x00", "virus.exe")


def test_empty_upload_is_rejected():
    with pytest.raises(DocumentError, match="empty"):
        extract_pages(b"", "empty.pdf")


def test_oversize_upload_is_rejected(monkeypatch):
    from guru.education import documents

    monkeypatch.setattr(documents, "MAX_BYTES", 16)
    with pytest.raises(DocumentError, match="larger than"):
        extract_pages(b"x" * 64, "big.txt")


def test_pdf_without_text_is_reported_not_silently_empty():
    with pytest.raises(DocumentError, match="No extractable text"):
        extract_pages(make_pdf([""]), "scan.pdf")


def test_corrupt_pdf_is_rejected():
    with pytest.raises(DocumentError, match="Could not read PDF"):
        extract_pages(b"%PDF-1.4 not really a pdf", "broken.pdf")


def test_chunks_keep_their_page_and_stay_bounded():
    pages = extract_pages(make_pptx(PPTX_SLIDES), "biology.pptx")
    chunks = chunk_pages(pages, max_chars=80)
    assert chunks
    assert {c.page for c in chunks} == {1, 2, 3}
    assert all(len(c.text) <= 80 for c in chunks)
    assert all(c.text.strip() for c in chunks)


# -------------------------------------------------------------------- retrieval
def test_embeddings_are_deterministic_and_normalised():
    first = embed_texts(["chlorophyll captures light", "water boils"])
    second = embed_texts(["chlorophyll captures light", "water boils"])
    assert np.allclose(first, second)
    assert np.allclose(np.linalg.norm(first, axis=1), 1.0, atol=1e-5)
    assert not np.allclose(first[0], first[1])


def test_search_finds_the_matching_page_first():
    chunks = [
        {"id": "c1", "document": "biology.pptx", "page": 1, "text": PPTX_SLIDES[0]},
        {"id": "c2", "document": "biology.pptx", "page": 2, "text": PPTX_SLIDES[1]},
        {"id": "c3", "document": "biology.pptx", "page": 3, "text": PPTX_SLIDES[2]},
    ]
    hits = search_chunks("chlorophyll pigment light", chunks, k=2)
    assert hits[0]["page"] == 2
    assert hits[0]["document"] == "biology.pptx"
    assert hits[0]["score"] > 0


def test_search_is_deterministic():
    chunks = [
        {"id": "c1", "document": "a.pdf", "page": 1, "text": "roots absorb water and minerals"},
        {"id": "c2", "document": "a.pdf", "page": 2, "text": "roots absorb water"},
    ]
    assert search_chunks("absorb water", chunks, k=2) == search_chunks("absorb water", chunks, k=2)


def test_search_without_query_or_material_returns_nothing():
    assert search_chunks("", [{"id": "c", "document": "a", "page": 1, "text": "x"}]) == []
    assert search_chunks("anything", []) == []


# ---------------------------------------------------------------------- storage
@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    store = Storage(tmp_path / "docs.db")
    yield store
    store.close()


def _doc(storage: Storage, session: str = "s1", name: str = "biology.pptx") -> dict:
    doc, _created = storage.add_document(
        session, name, "pptx", "hash-1", 3,
        [{"page": 1, "text": PPTX_SLIDES[0]}, {"page": 2, "text": PPTX_SLIDES[1]}],
    )
    return doc


def test_documents_and_chunks_roundtrip(storage):
    doc = _doc(storage)
    assert doc["name"] == "biology.pptx" and doc["pages"] == 3
    listed = storage.list_documents("s1")
    assert listed[0]["chunks"] == 2
    chunks = storage.document_chunks("s1")
    assert [c["page"] for c in chunks] == [1, 2]
    assert chunks[0]["document"] == "biology.pptx"


def test_same_bytes_are_not_ingested_twice(storage):
    _doc(storage)
    again, created = storage.add_document("s1", "copy.pptx", "pptx", "hash-1", 3,
                                          [{"page": 1, "text": "dup"}])
    assert created is False
    assert again["name"] == "biology.pptx"
    assert len(storage.document_chunks("s1")) == 2


def test_documents_are_scoped_per_session(storage):
    _doc(storage, "s1")
    assert storage.list_documents("s2") == []
    assert storage.document_chunks("s2") == []


def test_delete_document_removes_its_chunks(storage):
    doc = _doc(storage)
    assert storage.delete_document("s1", doc["id"]) == 1
    assert storage.list_documents("s1") == []
    assert storage.document_chunks("s1") == []


def test_purging_a_session_removes_its_documents(storage):
    doc = _doc(storage)
    storage.purge_session("s1")
    assert storage.get_document("s1", doc["id"]) is None
    assert storage.document_chunks("s1") == []


# -------------------------------------------------------------------------- API
@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def _session(c: TestClient) -> tuple[str, str]:
    res = c.post("/api/teacher/session", json={"title": "Biology"})
    body = res.json()
    return body["teacher_token"], body["session"]["id"]


def _auth(token: str) -> dict:
    return {"X-Teacher-Token": token}


def test_upload_requires_the_teacher_token(client):
    c, _app = client
    _token, sid = _session(c)
    url = f"/api/teacher/session/{sid}/documents"
    assert c.post(url, content=make_pptx(PPTX_SLIDES), params={"name": "b.pptx"}).status_code == 401
    assert c.post(url, content=b"x", params={"name": "b.pptx"},
                  headers=_auth("wrong")).status_code == 403


def test_upload_list_and_session_state(client):
    c, _app = client
    token, sid = _session(c)
    res = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                 params={"name": "biology.pptx"}, headers=_auth(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["document"]["pages"] == 3
    assert body["chunks"] >= 3
    assert body["duplicate"] is False

    again = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                   params={"name": "biology-copy.pptx"}, headers=_auth(token))
    assert again.json()["duplicate"] is True

    listed = c.get(f"/api/teacher/session/{sid}/documents", headers=_auth(token)).json()
    assert [d["name"] for d in listed["documents"]] == ["biology.pptx"]

    state = c.get(f"/api/teacher/session/{sid}", headers=_auth(token)).json()
    assert state["documents"][0]["name"] == "biology.pptx"


def test_upload_rejects_a_bad_file(client):
    c, _app = client
    token, sid = _session(c)
    res = c.post(f"/api/teacher/session/{sid}/documents", content=b"MZ",
                 params={"name": "payload.exe"}, headers=_auth(token))
    assert res.status_code == 400
    assert "Unsupported file type" in res.json()["detail"]


def test_search_endpoint_cites_the_page(client):
    c, _app = client
    token, sid = _session(c)
    c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
           params={"name": "biology.pptx"}, headers=_auth(token))
    hits = c.get(f"/api/teacher/session/{sid}/documents/search",
                 params={"q": "chlorophyll pigment"}, headers=_auth(token)).json()["hits"]
    assert hits[0]["page"] == 2
    assert hits[0]["document"] == "biology.pptx"


def test_documents_do_not_leak_between_sessions(client):
    c, _app = client
    token_a, sid_a = _session(c)
    token_b, sid_b = _session(c)
    c.post(f"/api/teacher/session/{sid_a}/documents", content=make_pptx(PPTX_SLIDES),
           params={"name": "biology.pptx"}, headers=_auth(token_a))
    assert c.get(f"/api/teacher/session/{sid_b}/documents", headers=_auth(token_b)).json()["documents"] == []


def test_upload_is_refused_while_a_lecture_is_running(client, settings):
    """Live captions keep CPU priority; uploads wait until the lecture stops."""
    c, _app = client
    token, sid = _session(c)
    start = c.post(f"/api/teacher/session/{sid}/start",
                   json={"capture": "none"}, headers=_auth(token))
    assert start.status_code == 200, start.text
    try:
        busy = c.post(f"/api/teacher/session/{sid}/documents",
                      content=make_pptx(PPTX_SLIDES), params={"name": "b.pptx"},
                      headers=_auth(token))
        assert busy.status_code == 409
        assert "live captions" in busy.json()["detail"]
    finally:
        c.post(f"/api/teacher/session/{sid}/stop", headers=_auth(token))

    ok = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                params={"name": "b.pptx"}, headers=_auth(token))
    assert ok.status_code == 200


def test_upload_can_be_allowed_during_a_lecture_by_configuration(settings):
    settings.education.allow_upload_while_live = True
    app = create_app(settings)
    with TestClient(app) as c:
        token, sid = _session(c)
        c.post(f"/api/teacher/session/{sid}/start", json={"capture": "none"}, headers=_auth(token))
        try:
            res = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                         params={"name": "b.pptx"}, headers=_auth(token))
            assert res.status_code == 200
        finally:
            c.post(f"/api/teacher/session/{sid}/stop", headers=_auth(token))


def test_second_concurrent_upload_is_rejected(client):
    from guru.server import app as app_module

    c, _app = client
    token, sid = _session(c)
    assert app_module._UPLOAD_LOCK.acquire(blocking=False)
    try:
        res = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                     params={"name": "b.pptx"}, headers=_auth(token))
        assert res.status_code == 429
    finally:
        app_module._UPLOAD_LOCK.release()


def test_session_document_limit_is_enforced(client, settings):
    settings.education.max_documents_per_session = 1
    c, _app = client
    token, sid = _session(c)
    assert c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                  params={"name": "one.pptx"}, headers=_auth(token)).status_code == 200
    second = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES + ["Extra."]),
                    params={"name": "two.pptx"}, headers=_auth(token))
    assert second.status_code == 409


def test_upload_reports_processing_time(client):
    c, _app = client
    token, sid = _session(c)
    body = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                  params={"name": "b.pptx"}, headers=_auth(token)).json()
    assert body["processing_ms"] >= 0


def test_delete_document_requires_confirmation(client):
    c, _app = client
    token, sid = _session(c)
    doc = c.post(f"/api/teacher/session/{sid}/documents", content=make_pptx(PPTX_SLIDES),
                 params={"name": "biology.pptx"}, headers=_auth(token)).json()["document"]
    url = f"/api/teacher/session/{sid}/documents/{doc['id']}"
    assert c.delete(url, headers=_auth(token)).status_code == 400
    assert c.delete(url, params={"confirm": "true"}, headers=_auth(token)).status_code == 200
    assert c.get(f"/api/teacher/session/{sid}/documents", headers=_auth(token)).json()["documents"] == []


# -------------------------------------------------------------------- grounding
def test_quiz_from_slides_cites_document_and_page():
    quiz = build_quiz([], FakeTranslator(), count=3, passages=PASSAGES)
    assert quiz["questions"], "slides should be enough to build a quiz"
    for q in quiz["questions"]:
        source = q["evidence"]["source"]
        assert source["document"] == "biology.pptx"
        assert 1 <= source["page"] <= 3
        assert q["source_page"] == source["page"]
        # The quote must be text that exists on that slide.
        assert q["evidence"]["quote_en"] in " ".join(
            p["text"] for p in PASSAGES if p["page"] == source["page"]
        )
    assert quiz["source_documents"]
    assert quiz["insufficient_evidence"] is False


def test_one_chunk_with_several_definitions_still_yields_questions():
    """A .txt upload is one retrieval chunk holding every sentence (regression)."""
    chunk = {"document": "biology.txt", "page": 1,
             "text": " ".join(PPTX_SLIDES)}
    quiz = build_quiz([], FakeTranslator(), count=3, passages=[chunk])
    assert len(quiz["questions"]) >= 2
    assert all(q["source_page"] == 1 for q in quiz["questions"])


def test_quiz_never_invents_a_page():
    """A passage without a valid page is skipped instead of being cited as page 0."""
    bad = [{"document": "biology.pptx", "page": 0, "text": PPTX_SLIDES[0]}]
    quiz = build_quiz([], FakeTranslator(), count=2, passages=bad)
    assert quiz["questions"] == []


def test_transcript_quiz_is_unchanged_without_documents():
    segments = [{"id": "s1", "seq": 1, "start_s": 0.0, "en": PPTX_SLIDES[0]}]
    with_docs = build_quiz(segments, FakeTranslator(), count=2)
    assert with_docs["source_documents"] == []
    for q in with_docs["questions"]:
        assert q["evidence"]["source"] is None
        assert q["source_segment_id"] == "s1"


def test_quiz_endpoint_is_grounded_in_an_uploaded_text_file(client):
    """End-to-end: upload -> retrieval -> draft, before any lecture is recorded."""
    c, _app = client
    token, sid = _session(c)
    upload = c.post(f"/api/teacher/session/{sid}/documents",
                    content="\n".join(PPTX_SLIDES).encode(),
                    params={"name": "biology.txt"}, headers=_auth(token))
    assert upload.status_code == 200, upload.text

    res = c.post(f"/api/teacher/session/{sid}/quiz",
                 json={"count": 3, "use_documents": True}, headers=_auth(token))
    assert res.status_code == 200, res.text
    content = res.json()["content"]
    assert content["questions"], "slides alone should support a draft quiz"
    assert content["source_documents"] == [{"document": "biology.txt", "page": 1}]
    # Keys stay hidden from students until the teacher approves the draft.
    assert res.json()["material"]["status"] == "draft"


def test_summary_includes_document_sources():
    summary = build_summary([], FakeTranslator(), passages=PASSAGES)
    assert summary["insufficient_content"] is False
    assert summary["sources"]
    assert summary["sources"][0]["document"] == "biology.pptx"
    assert summary["summary_en"][0]["page"] == 1
    assert summary["summary_bn"][0]["text"].startswith("বাংলা: ")


def test_summary_without_any_source_is_still_honest():
    summary = build_summary([], FakeTranslator())
    assert summary["insufficient_content"] is True
    assert summary["sources"] == []
