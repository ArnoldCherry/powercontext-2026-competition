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

"""Captured PDF page Sources and offline page-level evidence retrieval."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.runtime import ScopedMemoryApplication

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[a-z]+|\d+")
_STOPWORDS = frozenset({
    "a",
    "about",
    "above",
    "according",
    "after",
    "again",
    "against",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "company",
    "could",
    "d",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "fy",
    "given",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "m",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "please",
    "quantities",
    "quantity",
    "same",
    "she",
    "should",
    "s",
    "so",
    "some",
    "such",
    "t",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "us",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "answer",
    "based",
    "calculate",
    "calculated",
    "calculation",
    "compute",
    "computed",
    "context",
    "define",
    "defined",
    "definition",
    "details",
    "evidence",
    "give",
    "information",
    "less",
    "provide",
    "provided",
    "question",
    "report",
    "reported",
    "reporting",
    "reports",
    "response",
    "shown",
    "solely",
    "state",
    "stat",
    "use",
    "used",
    "using",
    "within",
})

_INSTRUCTION = re.compile(
    r"^(?:"
    r"respond to the question\b"
    r"|give a response to the question\b"
    r"|answer (?:in|concisely|briefly|the question)\b"
    r"|relying on the details\b"
    r"|assuming the perspective\b"
    r"|use only (?:the )?(?:context|evidence|information)\b"
    r"|using only (?:the )?(?:context|evidence|information)\b"
    r"|if .*\bnot explicitly outlined\b"
    r")",
    re.IGNORECASE,
)

_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("ies", "y"),
    ("ied", "y"),
    ("ying", "y"),
    ("ing", ""),
    ("ment", ""),
    ("ness", ""),
    ("ion", ""),
    ("ity", ""),
    ("ical", ""),
    ("ic", ""),
    ("al", ""),
    ("ly", ""),
    ("ed", ""),
    ("es", ""),
    ("s", ""),
    ("e", ""),
    ("y", ""),
    ("i", ""),
)

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "battl": ("legal", "litigat", "lawsuit", "proced"),
    "buyback": ("repurchas",),
    "capex": ("capit", "expenditur"),
    "earn": ("incom", "profit"),
    "employe": ("workforc", "headcount"),
    "eps": ("earn", "share", "incom"),
    "expenditur": ("capex", "capit"),
    "geograph": ("countr", "territor"),
    "headcount": ("employe", "workforc"),
    "incom": ("earn", "profit"),
    "lawsuit": ("litigat", "legal"),
    "legal": ("litigat", "lawsuit", "proced", "regulator"),
    "litigat": ("legal", "lawsuit", "proced"),
    "proceed": ("litigat", "legal"),
    "profit": ("incom", "earn"),
    "regulator": ("legal",),
    "repurchas": ("buyback",),
    "restructur": ("severanc", "productiv", "impair"),
    "revenu": ("sal",),
    "sal": ("revenu",),
    "severanc": ("restructur",),
    "sharehold": ("stockhold",),
    "stockhold": ("sharehold",),
    "workforc": ("employe", "headcount"),
}

_STATEMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "income": re.compile(r"statement(?:s)? of (?:consolidated )?(?:income|earnings|operations)"),
    "cashflow": re.compile(r"statement(?:s)? of (?:consolidated )?cash flows?"),
    "balance": re.compile(r"balance sheets?"),
    "equity": re.compile(r"statement(?:s)? of (?:consolidated )?(?:stockholders'? |shareholders'? )?equity"),
}

_STATEMENT_TRIGGERS: dict[str, frozenset[str]] = {
    "income": frozenset({"revenu", "sal", "gross", "incom", "margin", "earn", "eps", "profit"}),
    "cashflow": frozenset({
        "cash",
        "flow",
        "capex",
        "capit",
        "depreciat",
        "amortizat",
        "dividend",
        "repurchas",
        "invest",
        "financ",
    }),
    "balance": frozenset({"asset", "liabil", "equit", "inventor", "receivabl", "payabl", "goodwill", "debt", "borrow"}),
    "equity": frozenset({"dividend", "repurchas", "stock", "share", "equit"}),
}

_GEOGRAPHY = re.compile(r"countries and territories")
_LEGAL = re.compile(r"legal proceedings|litigation|lawsuits?|regulatory proceedings")

_ABBREVIATIONS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bd\s*&\s*a\b"), ("depreciat", "amortizat")),
    (re.compile(r"\br\s*&\s*d\b"), ("research", "develop")),
    (re.compile(r"\bs\s*g\s*&\s*a\b|\bsga\b"), ("sell", "gener", "administrativ")),
    (re.compile(r"\bcogs\b"), ("cost", "sal")),
    (re.compile(r"\bm\s*&\s*a\b"), ("acquisit", "merger")),
    (re.compile(r"\bp\s*&\s*l\b"), ("incom", "profit")),
    (re.compile(r"\bfcf\b"), ("cash", "flow")),
    (re.compile(r"\beps\b"), ("earn", "share")),
    (re.compile(r"\bfx\b"), ("foreign", "currenc")),
)
_TWO_DIGIT_FY = re.compile(r"\bfy\s*'?(\d{2})\b")


class PDFDocumentInput(BaseModel):
    """One complete PDF file supplied for full-document parsing."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def reject_blank_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """The durable identity and extracted text of one physical PDF page."""

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def reject_blank_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value

    @field_validator("document_sha256")
    @classmethod
    def require_lowercase_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("document_sha256 must be 64 lowercase hexadecimal characters")  # noqa: TRY003
        return value


class PDFPageSource(Source):
    """A captured physical page that stays readable without the original PDF."""

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page without returning partial results."""

    digest = hashlib.sha256(value.content).hexdigest()
    try:
        pages, count = _open_pdf(value.content)
        texts = tuple(_page_text(pages[index]) for index in range(count))
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF could not be parsed completely") from error  # noqa: TRY003
    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=digest,
            page_number=index,
            text=text,
        )
        for index, text in enumerate(texts, start=1)
    )


def _open_pdf(content: bytes) -> tuple[Any, int]:
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF requires a user password")  # noqa: TRY003
    pages = reader.pages
    count = len(pages)
    if count < 1:
        raise ValueError("PDF contains no physical pages")  # noqa: TRY003
    return pages, count


def _page_text(page: Any, /) -> str:
    text = (page.extract_text() or "").replace("\x00", "")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
        text = "".join(character for character in text if not 0xD800 <= ord(character) <= 0xDFFF)
    return text.strip()


def _page_source_name(document_id: str, document_sha256: str, page_number: int) -> str:
    identity = f"{len(document_id)}:{document_id}:{document_sha256}:{page_number}"
    name = f"{PDF_PAGE_SOURCE_NAME}:{identity}"
    if len(name) <= MAX_SOURCE_ID_LENGTH:
        return name
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{PDF_PAGE_SOURCE_NAME}:{digest}:{page_number}"


class PDFPageAdapter:
    """Resolve and read one captured physical page."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=_page_source_name(value.document_id, value.document_sha256, value.page_number),
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose page text with the document digest and physical page number."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


@dataclass(frozen=True, slots=True)
class _PageRecord:
    citation: MemoryCitation
    text: str
    document_id: str
    document_terms: frozenset[str]
    body_terms: tuple[str, ...]
    body_counts: Counter[str]
    prefix: str
    lower_text: str
    order: int


@dataclass(frozen=True, slots=True)
class _Concept:
    primary: str
    variants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Cues:
    statements: tuple[str, ...]
    geography: bool
    legal: bool


async def search_pdf_pages(
    memory: ScopedMemoryApplication,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact citations for the most relevant distinct stored pages."""

    if limit < 1 or not question.strip():
        return ()
    records = await _stored_pages(memory, question, limit)
    if not records:
        return ()
    concepts = _question_concepts(question)
    if not concepts:
        return ()
    cues = _question_cues(question)
    target_documents = _target_documents(records, concepts)
    ranked = _rank_pages(records, concepts, _question_bigrams(question), cues, target_documents)
    if not target_documents:
        target_documents = _best_document(ranked)
    return _select_citations(ranked, limit, cues, target_documents)


async def _stored_pages(
    memory: ScopedMemoryApplication,
    question: str,
    limit: int,
) -> tuple[_PageRecord, ...]:
    """Read the current head so offline reranking sees every stored page; fall back to FTS search."""

    lister = getattr(memory, "list", None)
    if callable(lister):
        page = await lister()
        records = tuple(
            _page_record(record.citation, record.entry.text, order)
            for order, record in enumerate(page.entries)
            if record.entry.text.strip()
        )
        return records
    from powercontext.builtin.runtime import SearchMemoryRequest

    hits = await memory.search(
        SearchMemoryRequest(query=question, mode="fts", limit=max(limit * 8, 64)),
    )
    return tuple(
        _page_record(
            MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            ),
            hit.text,
            order,
        )
        for order, hit in enumerate(hits.hits)
        if hit.text.strip()
    )


def _page_record(citation: MemoryCitation, text: str, order: int, /) -> _PageRecord:
    document_id, body = _split_page_text(text)
    body_terms = _terms(body)
    return _PageRecord(
        citation=citation,
        text=text,
        document_id=document_id,
        document_terms=frozenset(_terms(document_id)),
        body_terms=body_terms,
        body_counts=Counter(body_terms),
        prefix=" ".join(body[:240].split()).casefold(),
        lower_text=body.casefold(),
        order=order,
    )


def _split_page_text(text: str) -> tuple[str, str]:
    """Split the pipeline's ``DOCUMENT_ID\\nPAGE_TEXT`` prefix, tolerating page-only text."""

    first, separator, rest = text.partition("\n")
    if separator and _looks_like_document_id(first):
        return first, rest
    return "", text


def _looks_like_document_id(value: str) -> bool:
    if (
        not value
        or any(character.isspace() for character in value)
        or not any(character.isalpha() for character in value)
    ):
        return False
    return value.upper() == value or "_" in value


def _question_concepts(question: str) -> tuple[_Concept, ...]:
    concepts: list[_Concept] = []
    seen: set[str] = set()
    for term in _expanded_question_terms(question):
        if term in seen:
            continue
        seen.add(term)
        variants = tuple(dict.fromkeys((term, *_SYNONYMS.get(term, ()))))
        concepts.append(_Concept(primary=term, variants=variants))
    return tuple(concepts)


def _question_bigrams(question: str) -> tuple[tuple[str, str], ...]:
    terms = _expanded_question_terms(question)
    return tuple(dict.fromkeys(pairwise(terms)))


def _expanded_question_terms(question: str) -> list[str]:
    text = _strip_instructions(question)
    folded = text.casefold()
    terms = _terms(text)
    for pattern, expansions in _ABBREVIATIONS:
        if pattern.search(folded):
            terms.extend(expansions)
    terms.extend(f"20{match.group(1)}" for match in _TWO_DIGIT_FY.finditer(folded))
    return terms


def _strip_instructions(question: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", question)
    kept = [sentence for sentence in sentences if not _INSTRUCTION.match(sentence.strip())]
    return " ".join(kept) or question


def _terms(value: str) -> list[str]:
    terms: list[str] = []
    for token in _TOKEN.findall(value.casefold()):
        if token in _STOPWORDS or (len(token) < 2 and not token.isdigit()):
            continue
        term = _stem(token)
        if term and term not in _STOPWORDS:
            terms.append(term)
    return terms


def _stem(token: str) -> str:
    if token.isdigit():
        return token
    term = token
    changed = True
    while changed and len(term) > 3:
        changed = False
        for suffix, replacement in _SUFFIXES:
            if not term.endswith(suffix):
                continue
            candidate = term[: -len(suffix)] + replacement
            if len(candidate) < 3:
                continue
            if candidate != term:
                term = candidate
                changed = True
                break
    return term


def _rank_pages(
    records: tuple[_PageRecord, ...],
    concepts: tuple[_Concept, ...],
    bigrams: tuple[tuple[str, str], ...],
    cues: _Cues,
    target_documents: frozenset[str],
) -> list[tuple[float, _PageRecord]]:
    total = len(records)
    lengths = [len(record.body_terms) for record in records]
    average_length = (sum(lengths) / total) if total else 0.0
    document_frequency = _document_frequency(records, concepts)
    idf = {term: _idf(frequency, total) for term, frequency in document_frequency.items()}
    concept_idf = {
        concept.primary: max(idf.get(variant, _idf(0, total)) for variant in concept.variants) for concept in concepts
    }
    total_idf = sum(concept_idf.values()) or 1.0
    ranked: list[tuple[float, _PageRecord]] = []
    for record, length in zip(records, lengths, strict=True):
        score = _lexical_score(record, length, average_length, concepts, idf)
        matched = [concept.primary for concept in concepts if _matches(record, concept)]
        coverage = sum(concept_idf[name] for name in matched) / total_idf
        phrases = sum(0.5 for left, right in bigrams if _has_bigram(record, left, right))
        document_bonus = _document_bonus(record, matched)
        structural_bonus = 0.0
        if not target_documents or record.document_id in target_documents:
            structural_bonus = _structural_bonus(record, cues)
        ranked.append((score + (3.0 * coverage * total_idf) + phrases + document_bonus + structural_bonus, record))
    ranked.sort(key=lambda item: (-item[0], item[1].order))
    return ranked


def _question_cues(question: str) -> _Cues:
    cleaned = _strip_instructions(question).casefold()
    terms = set(_expanded_question_terms(question))
    statements: list[str] = []

    def add(statement: str) -> None:
        if statement not in statements:
            statements.append(statement)

    income_phrases = re.search(
        r"operating (?:income|expenses?|profit)|cost of (?:sales|revenue|goods)|income tax", cleaned
    )
    income_terms = {
        "incom",
        "revenu",
        "sal",
        "gross",
        "margin",
        "eps",
        "earn",
        "profit",
        "tax",
        "interest",
        "outstand",
        "research",
        "develop",
        "advertis",
        "pension",
        "sell",
        "administrativ",
    }
    if income_phrases or terms.intersection(income_terms):
        add("income")
    if ("cash" in terms and "flow" in terms) or terms.intersection({
        "capex",
        "depreciat",
        "amortizat",
        "dividend",
        "repurchas",
        "invest",
        "financ",
    }):
        add("cashflow")
    if terms.intersection({
        "asset",
        "liabil",
        "equit",
        "inventor",
        "receivabl",
        "payabl",
        "goodwill",
        "debt",
        "borrow",
    }):
        add("balance")
    if "balanc" in terms and "sheet" in terms:
        add("balance")
    if ("work" in terms and "capit" in terms) or ("cash" in terms and "equivalent" in terms):
        add("balance")
    if "equit" in terms and terms.intersection({"dividend", "repurchas", "stock", "share"}):
        add("equity")
    geography = bool(terms.intersection({"geograph", "countr", "territor"}))
    legal = bool(terms.intersection({"leg", "litigat", "battl", "lawsuit"}))
    return _Cues(statements=tuple(statements), geography=geography, legal=legal)


def _target_documents(records: tuple[_PageRecord, ...], concepts: tuple[_Concept, ...]) -> frozenset[str]:
    targets: set[str] = set()
    for record in records:
        if any(
            len(concept.primary) >= 3 and not concept.primary.isdigit() and concept.primary in record.document_terms
            for concept in concepts
        ):
            targets.add(record.document_id)
    return frozenset(targets)


def _best_document(ranked: list[tuple[float, _PageRecord]]) -> frozenset[str]:
    scores: dict[str, float] = {}
    for score, record in ranked:
        if score > 0.0:
            scores[record.document_id] = scores.get(record.document_id, 0.0) + score
    if not scores:
        return frozenset()
    return frozenset({max(scores, key=scores.__getitem__)})


def _structural_bonus(record: _PageRecord, cues: _Cues) -> float:
    bonus = 0.0
    for statement in cues.statements:
        if _STATEMENT_PATTERNS[statement].search(record.prefix):
            bonus += 4.0
            continue
        if len(_STATEMENT_TRIGGERS[statement].intersection(record.body_counts)) >= 2:
            bonus += 2.0
    if cues.geography and _GEOGRAPHY.search(record.lower_text):
        bonus += 8.0
    if cues.legal and _LEGAL.search(record.lower_text):
        bonus += 8.0
    return bonus


def _statement_pages(
    ranked: list[tuple[float, _PageRecord]],
    cues: _Cues,
    target_documents: frozenset[str],
) -> list[_PageRecord]:
    """Force the canonical statement page for each statement named by the question."""

    pages: list[_PageRecord] = []
    seen: set[str] = set()
    for statement in cues.statements:
        candidates = [
            (score, record)
            for score, record in ranked
            if (not target_documents or record.document_id in target_documents)
            and _STATEMENT_PATTERNS[statement].search(record.prefix)
        ]
        if not candidates:
            continue
        record = max(candidates, key=lambda item: item[0])[1]
        if record.citation.entry_id not in seen:
            seen.add(record.citation.entry_id)
            pages.append(record)
    score_by_record = {id(record): score for score, record in ranked}
    pages.sort(key=lambda record: -score_by_record[id(record)])
    return pages


def _document_frequency(
    records: tuple[_PageRecord, ...],
    concepts: tuple[_Concept, ...],
) -> dict[str, int]:
    variants = {variant for concept in concepts for variant in concept.variants}
    frequency = dict.fromkeys(variants, 0)
    for record in records:
        for variant in variants.intersection(record.body_counts):
            frequency[variant] += 1
    return frequency


def _idf(frequency: int, total: int) -> float:
    return math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))


def _lexical_score(
    record: _PageRecord,
    length: int,
    average_length: float,
    concepts: tuple[_Concept, ...],
    idf: dict[str, float],
) -> float:
    score = 0.0
    normalization = 1.2 * (1.0 - 0.75 + (0.75 * length / average_length if average_length else 0.0))
    for concept in concepts:
        best = 0.0
        for variant in concept.variants:
            frequency = record.body_counts.get(variant, 0)
            if frequency:
                best = max(best, idf.get(variant, 0.0) * frequency * 2.2 / (frequency + normalization))
        score += best
    return score


def _matches(record: _PageRecord, concept: _Concept) -> bool:
    return any(record.body_counts.get(variant, 0) > 0 for variant in concept.variants)


def _has_bigram(record: _PageRecord, left: str, right: str) -> bool:
    terms = record.body_terms
    return any(terms[index] == left and terms[index + 1] == right for index in range(len(terms) - 1))


def _document_bonus(record: _PageRecord, matched: list[str]) -> float:
    shared = [term for term in matched if term in record.document_terms and not term.isdigit() and len(term) >= 3]
    return 4.0 if shared else 0.0


def _select_citations(
    ranked: list[tuple[float, _PageRecord]],
    limit: int,
    cues: _Cues,
    target_documents: frozenset[str],
) -> tuple[MemoryCitation, ...]:
    citations: list[MemoryCitation] = []
    seen_entries: set[str] = set()
    seen_texts: set[str] = set()
    ordered = [
        *_statement_pages(ranked, cues, target_documents),
        *(record for score, record in ranked if score > 0.0),
    ]
    for record in ordered:
        if len(citations) >= limit:
            break
        entry_id = record.citation.entry_id
        if entry_id in seen_entries or record.text in seen_texts:
            continue
        seen_entries.add(entry_id)
        seen_texts.add(record.text)
        citations.append(record.citation)
    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "parse_pdf_document",
    "search_pdf_pages",
]
