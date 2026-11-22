"""Local document ingestion and retrieval with page/slide provenance.

PDF text is read with ``pypdf`` (pure Python, no network); PPTX is a zip of XML
and is parsed with the standard library, so no extra dependency is needed for
slides.  Nothing here calls a model server or the network: retrieval is a
deterministic hybrid of hashed n-gram vectors and term overlap, which means every
hit can be traced back to a specific page of a specific uploaded file.

Uploaded text is *data*.  It is never executed, never treated as instructions and
never fed to a shell or an LLM, so a slide that says "ignore previous
instructions" is just text that may be quoted.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 400
MAX_EXTRACTED_CHARS = 400_000
SUPPORTED_SUFFIXES = {".pdf", ".pptx", ".txt", ".md"}
DIM = 512

# PPTX slide XML namespaces.
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")
_WORD_RE = re.compile(r"[a-z0-9]+|[\u0980-\u09FF]+")


class DocumentError(ValueError):
    """Raised for any file that cannot be safely ingested."""


@dataclass(frozen=True)
class Page:
    number: int  # 1-based page (PDF) or slide (PPTX) number
    text: str


@dataclass(frozen=True)
class Chunk:
    page: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return {"page": self.page, "text": self.text}


def _suffix(filename: str) -> str:
    return Path(filename).suffix.lower()


def _check_file(data: bytes, filename: str) -> str:
    suffix = _suffix(filename)
    if suffix not in SUPPORTED_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise DocumentError(f"Unsupported file type {suffix or '(none)'}; allowed: {allowed}")
    if not data:
        raise DocumentError("The uploaded file is empty")
    if len(data) > MAX_BYTES:
        raise DocumentError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
    return suffix


def extract_pages(data: bytes, filename: str) -> list[Page]:
    """Extract text per page/slide. Raises ``DocumentError`` on bad input."""
    suffix = _check_file(data, filename)
    if suffix == ".pdf":
        pages = _extract_pdf(data)
    elif suffix == ".pptx":
        pages = _extract_pptx(data)
    else:
        text = data.decode("utf-8", errors="replace")
        pages = [Page(number=1, text=text)]
    if sum(len(p.text) for p in pages) > MAX_EXTRACTED_CHARS:
        raise DocumentError(f"Extracted text exceeds {MAX_EXTRACTED_CHARS} characters")
    if not any(p.text.strip() for p in pages):
        raise DocumentError(
            "No extractable text found. A scanned PDF needs OCR, which this offline build does not include."
        )
    return pages


def _extract_pdf(data: bytes) -> list[Page]:
    from pypdf import PdfReader  # imported lazily: the server starts without it

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PAGES:
            raise DocumentError(f"PDF has more than {MAX_PAGES} pages")
        return [
            Page(number=i, text=(page.extract_text() or ""))
            for i, page in enumerate(reader.pages, start=1)
        ]
    except DocumentError:
        raise
    except Exception as exc:  # any parser failure is a bad upload, not a crash
        raise DocumentError(f"Could not read PDF: {exc}") from exc


def _extract_pptx(data: bytes) -> list[Page]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            # Guard against a decompression bomb: cap the declared uncompressed size.
            declared = sum(info.file_size for info in zf.infolist())
            if declared > MAX_BYTES * 8:
                raise DocumentError("PPTX expands too large to be safe")
            slides = sorted(
                ((int(m.group(1)), name) for name in names if (m := _SLIDE_RE.match(name))),
                key=lambda t: t[0],
            )
            if not slides:
                raise DocumentError("No slides found in this PPTX file")
            if len(slides) > MAX_PAGES:
                raise DocumentError(f"Deck has more than {MAX_PAGES} slides")
            return [
                Page(number=number, text=_slide_text(zf.read(name)))
                for number, name in slides
            ]
    except DocumentError:
        raise
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DocumentError(f"Could not read PPTX: {exc}") from exc


def _slide_text(xml: bytes) -> str:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    lines: list[str] = []
    for paragraph in root.iter(f"{_A}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_A}t")).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def chunk_pages(pages: list[Page], max_chars: int = 700) -> list[Chunk]:
    """Split page text into sentence-aligned chunks, keeping the page number."""
    chunks: list[Chunk] = []
    for page in pages:
        text = re.sub(r"[ \t]+", " ", page.text).strip()
        if not text:
            continue
        buffer = ""
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip()
            if not sentence:
                continue
            if buffer and len(buffer) + len(sentence) + 1 > max_chars:
                chunks.append(Chunk(page=page.number, text=buffer))
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}".strip()
        if buffer:
            chunks.append(Chunk(page=page.number, text=buffer))
    return chunks


def _term_weights(text: str) -> dict[str, float]:
    """Word unigrams plus bigrams: enough signal for classroom material."""
    words = _WORD_RE.findall(text.lower())
    weights = {w: 1.0 for w in words}
    for left, right in itertools.pairwise(words):
        weights[f"{left}_{right}"] = 0.5
    return weights


def _bucket(token: str) -> tuple[int, float]:
    """Stable signed hash bucket (``hash()`` is salted per process)."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % DIM, 1.0 if (value >> 63) & 1 else -1.0


def embed_texts(texts: list[str]) -> np.ndarray:
    """Deterministic hashed vectors, L2-normalised. No model download."""
    matrix = np.zeros((len(texts), DIM), dtype=np.float32)
    for row, text in enumerate(texts):
        for token, weight in _term_weights(text).items():
            index, sign = _bucket(token)
            matrix[row, index] += sign * weight
        norm = float(np.linalg.norm(matrix[row]))
        if norm:
            matrix[row] /= norm
    return matrix


def search_chunks(query: str, chunks: list[dict], k: int = 5) -> list[dict]:
    """Hybrid lexical/vector search over stored chunks.

    ``chunks`` items carry ``document``, ``page`` and ``text`` and may carry
    ``text_bn``, the Bangla side written at upload time.  A chunk is scored on the
    better of its two sides and always cites the original text, so a Bangla query
    against an English slide still quotes the English source.  Scores are
    deterministic, so the same material always yields the same citations.
    """
    query = (query or "").strip()
    if not query or not chunks:
        return []
    variants: list[tuple[int, str]] = []
    for index, chunk in enumerate(chunks):
        variants.append((index, str(chunk.get("text", ""))))
        bangla = str(chunk.get("text_bn") or "")
        if bangla:
            variants.append((index, bangla))
    vectors = embed_texts([query] + [text for _i, text in variants])
    cosine = vectors[1:] @ vectors[0]
    query_terms = set(_WORD_RE.findall(query.lower()))
    best: dict[int, float] = {}
    for (index, text), similarity in zip(variants, cosine):
        terms = set(_WORD_RE.findall(text.lower()))
        overlap = len(query_terms & terms) / len(query_terms) if query_terms else 0.0
        score = 0.7 * float(similarity) + 0.3 * overlap
        if score > best.get(index, 0.0):
            best[index] = score
    hits: list[dict] = []
    for index, chunk in enumerate(chunks):
        score = best.get(index, 0.0)
        if score <= 0:
            continue
        text = str(chunk.get("text", ""))
        hits.append(
            {
                "document": chunk.get("document", ""),
                "document_id": chunk.get("document_id"),
                "page": int(chunk.get("page") or 0),
                "chunk_id": chunk.get("id"),
                "score": round(score, 4),
                "text": text,
                "text_bn": chunk.get("text_bn") or "",
            }
        )
    hits.sort(key=lambda h: (-h["score"], h["document"], h["page"], h["chunk_id"] or ""))
    return hits[: max(1, k)]
