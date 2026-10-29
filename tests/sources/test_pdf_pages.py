# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import hashlib
import io
from types import SimpleNamespace

import pytest

pypdf = pytest.importorskip("pypdf")

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from powercontext.artifacts import ArtifactRef, MemoryCitation  # noqa: E402
from powercontext.sources import SourceDefinitionRegistry, project_source_for_transport  # noqa: E402
from powercontext.sources.pdf_pages import (  # noqa: E402
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY  # noqa: E402


def _pdf_bytes(page_texts: tuple[str | None, ...]) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=300, height=300)
        if text is None:
            continue
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 200 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_parse_all_physical_pages_and_digest() -> None:
    content = _pdf_bytes(("geography countries territories", None, "net revenue 2022"))
    pages = parse_pdf_document(PDFDocumentInput(document_id="report", content=content))

    assert isinstance(pages, tuple)
    assert [(page.page_number, page.text) for page in pages] == [
        (1, "geography countries territories"),
        (2, ""),
        (3, "net revenue 2022"),
    ]
    assert {page.document_id for page in pages} == {"report"}
    assert {page.document_sha256 for page in pages} == {hashlib.sha256(content).hexdigest()}


def test_blank_pages_revisions_and_exact_projection() -> None:
    original_pdf = _pdf_bytes((None, ""))
    original = parse_pdf_document(PDFDocumentInput(document_id="control", content=original_pdf))
    assert [(page.page_number, page.text) for page in original] == [(1, ""), (2, "")]

    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(original[0]))
    assert asyncio.run(registry.read(source)) == original[0]
    observation = project_source_for_transport(registry, source)
    evidence = observation.projection(TEXT_EVIDENCE_PROJECTION_KEY)
    assert evidence == {
        "source_type": "pdf-page",
        "source_id": source.name,
        "content": "control\n",
        "metadata": {
            "document_id": "control",
            "document_sha256": original[0].document_sha256,
            "page_number": 1,
        },
    }

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.add_blank_page(width=100, height=200)
    writer.add_metadata({"/Title": "New revision"})
    buffer = io.BytesIO()
    writer.write(buffer)
    updated = parse_pdf_document(PDFDocumentInput(document_id="control", content=buffer.getvalue()))

    updated_source = asyncio.run(registry.resolve(updated[0]))
    assert updated_source.name != source.name
    assert updated_source.document_sha256 != source.document_sha256
    assert len(source.name) <= 256


def test_long_document_id_keeps_source_name_bounded() -> None:
    content = _pdf_bytes((None,))
    page = parse_pdf_document(PDFDocumentInput(document_id="x" * 500, content=content))[0]
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    first = asyncio.run(registry.resolve(page))
    second = asyncio.run(registry.resolve(page))
    assert first.name == second.name
    assert len(first.name) <= 256

    other = parse_pdf_document(PDFDocumentInput(document_id="y" * 500, content=content))[0]
    assert asyncio.run(registry.resolve(other)).name != first.name


def test_empty_password_permission_is_readable() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="owner")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=buffer.getvalue()))
    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


def test_password_protected_document_fails() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="secret", owner_password="owner")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="locked", content=buffer.getvalue()))


@pytest.mark.parametrize("raw", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_corrupt_documents_fail_atomically(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="invalid", content=raw))


@pytest.mark.parametrize("document_id", ["", "   ", "\t\n"])
def test_blank_document_id_is_rejected(document_id: str) -> None:
    with pytest.raises(ValueError):
        PDFDocumentInput(document_id=document_id, content=b"irrelevant")


def test_search_is_bounded_distinct_and_statement_aware() -> None:
    memory_ref = ArtifactRef(family="memory", artifact_id="memory-1", revision=1)

    def record(index: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(
            citation=MemoryCitation(
                memory_ref=memory_ref,
                entry_id=f"entry-{index}",
                entry_version_id=f"version-{index}",
            ),
            entry=SimpleNamespace(text=text),
        )

    records = (
        record(1, "PEPSICO_2022_10K\ngeography countries and territories revenue 2022"),
        record(2, "PEPSICO_2022_10K\nConsolidated Statement of Income net revenue 2022"),
        record(3, "PEPSICO_2022_10K\nunrelated packaging disclosure"),
    )
    memory = SimpleNamespace(list=_listing(records))

    citations = asyncio.run(
        search_pdf_pages(
            memory,
            "What is PepsiCo's FY2022 net revenue shown in the income statement?",
            limit=2,
        )
    )
    assert len(citations) == 2
    assert citations[0].entry_id == "entry-2"
    assert len({citation.entry_id for citation in citations}) == len(citations)
    assert asyncio.run(search_pdf_pages(memory, "net revenue", limit=0)) == ()
    assert asyncio.run(search_pdf_pages(memory, "   ", limit=5)) == ()


def test_search_targets_named_document_statement_page() -> None:
    memory_ref = ArtifactRef(family="memory", artifact_id="memory-2", revision=1)

    def record(index: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(
            citation=MemoryCitation(
                memory_ref=memory_ref,
                entry_id=f"entry-{index}",
                entry_version_id=f"version-{index}",
            ),
            entry=SimpleNamespace(text=text),
        )

    records = (
        record(1, "PEPSICO_2022_10K\ngeography and other disclosures"),
        record(2, "PEPSICO_2022_10K\nConsolidated Statement of Income net revenue 2022"),
        record(3, "BOEING_2022_10K\nnet revenue net revenue net revenue gross profit"),
        record(4, "BOEING_2022_10K\nConsolidated Statement of Income net revenue 2022"),
    )
    memory = SimpleNamespace(list=_listing(records))

    citations = asyncio.run(
        search_pdf_pages(
            memory,
            "What is PepsiCo's FY2022 net revenue shown in the income statement?",
            limit=2,
        )
    )
    assert citations[0].entry_id == "entry-2"


def _record_factory(memory_ref: ArtifactRef):
    def record(index: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(
            citation=MemoryCitation(
                memory_ref=memory_ref,
                entry_id=f"entry-{index}",
                entry_version_id=f"version-{index}",
            ),
            entry=SimpleNamespace(text=text),
        )

    return record


def test_search_scopes_to_named_document() -> None:
    memory_ref = ArtifactRef(family="memory", artifact_id="memory-3", revision=1)
    record = _record_factory(memory_ref)
    records = (
        record(1, "PEPSICO_2022_10K\ngeography countries and territories"),
        record(2, "PEPSICO_2022_10K\nConsolidated Statement of Income net revenue 2022"),
        record(3, "BOEING_2022_10K\nnet revenue net revenue net revenue gross profit"),
        record(4, "BOEING_2022_10K\nConsolidated Statement of Income net revenue 2022"),
        record(5, "BOEING_2022_10K\ncustomer concentration and liquidity"),
    )
    memory = SimpleNamespace(list=_listing(records))

    citations = asyncio.run(
        search_pdf_pages(memory, "What is PepsiCo's FY2022 net revenue in the income statement?", limit=5)
    )
    assert citations
    assert {citation.entry_id for citation in citations} <= {"entry-1", "entry-2"}


def test_canonical_statement_title_beats_index_page() -> None:
    memory_ref = ArtifactRef(family="memory", artifact_id="memory-4", revision=1)
    record = _record_factory(memory_ref)
    records = (
        record(
            1,
            "PEPSICO_2022_10K\nindex to consolidated financial statements consolidated statements of income "
            "balance sheet cash flow statement",
        ),
        record(2, "PEPSICO_2022_10K\nConsolidated Statement of Income net revenue 2022 operating profit"),
        record(3, "PEPSICO_2022_10K\nunrelated packaging disclosure"),
    )
    memory = SimpleNamespace(list=_listing(records))

    citations = asyncio.run(search_pdf_pages(memory, "What is PepsiCo's FY2022 net revenue?", limit=2))
    assert citations[0].entry_id == "entry-2"


def test_geography_section_page_is_retained() -> None:
    memory_ref = ArtifactRef(family="memory", artifact_id="memory-5", revision=1)
    record = _record_factory(memory_ref)
    records = [
        record(index, f"PEPSICO_2022_10K\nmore than 200 countries and territories disclosure number {index}")
        for index in range(1, 8)
    ]
    records.append(
        record(
            8,
            "PEPSICO_2022_10K\nAfrica, Middle East and South Asia (AMESA), which includes all of our "
            "beverage and convenient food businesses in Africa, the Middle East and South Asia",
        )
    )
    memory = SimpleNamespace(list=_listing(tuple(records)))

    citations = asyncio.run(search_pdf_pages(memory, "What are PepsiCo's geographies in FY2022?", limit=10))
    assert "entry-8" in {citation.entry_id for citation in citations}


def test_numeric_document_name_matches_3m() -> None:
    memory_ref = ArtifactRef(family="memory", artifact_id="memory-6", revision=1)
    record = _record_factory(memory_ref)
    records = (
        record(1, "3M_2022_10K\ntotal net revenue 2022"),
        record(2, "PEPSICO_2022_10K\ntotal net revenue 2022"),
    )
    memory = SimpleNamespace(list=_listing(records))

    citations = asyncio.run(search_pdf_pages(memory, "What was 3M's FY2022 revenue?", limit=2))
    assert citations
    assert {citation.entry_id for citation in citations} == {"entry-1"}


def _listing(records):
    async def list_entries():
        return SimpleNamespace(entries=records)

    return list_entries
