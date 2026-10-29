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

"""Select literal, query-relevant spans from already recalled Memory entries.

The selector is deliberately offline: it scores sentence-level spans with term
statistics computed from the supplied candidate text, then fits whole spans into
the caller's byte budget while counting the rendered citation overhead. It never
rewrites, summarizes, or truncates the selected text.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CLOSING_PUNCTUATION = "\"')]\u201d\u2019"
_OPENING_PUNCTUATION = "\"'([\u201c\u2018"
_LONG_SPAN_RESERVE = 640
_MIN_SPLIT_CHARS = 160
_MIN_PREFIX_MATCH = 5
_DOCUMENT_PRIOR = 0.4
_NEIGHBOR_DAMPING = 0.5
_RELATIVE_FLOOR = 0.35
_NEIGHBOR_LIMIT = 2

_ABBREVIATIONS = frozenset({
    "al",
    "approx",
    "ca",
    "cf",
    "dr",
    "e.g",
    "eq",
    "eqs",
    "etc",
    "fig",
    "figs",
    "i.e",
    "inc",
    "ltd",
    "mr",
    "mrs",
    "ms",
    "no",
    "prof",
    "ref",
    "refs",
    "resp",
    "sec",
    "st",
    "vs",
})

_STOPWORDS = frozenset({
    "a",
    "about",
    "above",
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
    "could",
    "data",
    "demonstrate",
    "demonstrated",
    "demonstrates",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "find",
    "finds",
    "for",
    "found",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "indicate",
    "indicated",
    "indicates",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "however",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "may",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "now",
    "observe",
    "observed",
    "observes",
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
    "present",
    "presented",
    "presents",
    "report",
    "reported",
    "reports",
    "result",
    "results",
    "same",
    "she",
    "should",
    "show",
    "showed",
    "shown",
    "shows",
    "so",
    "some",
    "study",
    "studies",
    "suggest",
    "suggested",
    "suggests",
    "such",
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
})


@dataclass(frozen=True)
class EvidenceEntry:
    """One recalled Memory entry version available for literal selection."""

    scope_id: str
    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str


@dataclass(frozen=True)
class EvidenceFragment:
    """One exact character interval selected from an evidence entry."""

    entry: EvidenceEntry
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class EvidenceSelection:
    """The rendered evidence context together with the selected intervals."""

    content: str | None
    fragments: tuple[EvidenceFragment, ...] = ()
    dropped: int = 0


@dataclass
class _Chunk:
    entry_index: int
    start: int
    end: int
    text: str
    stems: tuple[str, ...]
    score: float = 0.0


def select_evidence(query: str, max_bytes: int, entries: Sequence[EvidenceEntry]) -> EvidenceSelection:
    """Choose literal entry spans that answer ``query`` within ``max_bytes``.

    The budget covers the complete rendered context: trust policy, envelope,
    citations, JSON framing, and the selected original text. Empty selection is
    reported as ``content=None``; the caller turns that into an empty Context.
    """

    chunks = _chunks(entries, max_bytes)
    if not chunks:
        return EvidenceSelection(content=None)
    _score_chunks(query, chunks, len(entries))
    selected: list[list[tuple[int, int]]] = [[] for _ in entries]
    selected_chunks: set[int] = set()
    dropped = 0
    ranked = sorted(
        range(len(chunks)),
        key=lambda index: (-chunks[index].score, chunks[index].entry_index, chunks[index].start),
    )
    top_score = max((chunk.score for chunk in chunks), default=0.0)
    if top_score > 0:
        floor = top_score * _RELATIVE_FLOOR
        for index in ranked:
            chunk = chunks[index]
            if chunk.score < floor:
                break
            if _extend_selection(entries, chunk, selected, max_bytes):
                selected_chunks.add(index)
            else:
                dropped += 1
        _expand_neighbors(entries, chunks, selected, selected_chunks, max_bytes, top_score)
        _fill_remaining(entries, chunks, ranked, selected, selected_chunks, max_bytes, floor)
    if not any(selected):
        return EvidenceSelection(content=None, dropped=dropped)
    content = _render(entries, selected)
    fragments = tuple(
        EvidenceFragment(entry=entries[entry_index], start=start, end=end, text=entries[entry_index].text[start:end])
        for entry_index, intervals in enumerate(selected)
        for start, end in intervals
    )
    return EvidenceSelection(content=content, fragments=fragments, dropped=dropped)


def _chunks(entries: Sequence[EvidenceEntry], max_bytes: int) -> list[_Chunk]:
    long_span_limit = max(_MIN_SPLIT_CHARS, max_bytes - _LONG_SPAN_RESERVE)
    chunks: list[_Chunk] = []
    for entry_index, entry in enumerate(entries):
        for start, end in _sentence_spans(entry.text, long_span_limit):
            text = entry.text[start:end]
            if text.strip():
                chunks.append(_Chunk(entry_index=entry_index, start=start, end=end, text=text, stems=_stems(text)))
    return chunks


def _score_chunks(query: str, chunks: Sequence[_Chunk], entry_count: int) -> None:
    query_terms = _query_terms(query)
    if not query_terms:
        return
    weights = _term_weights(query_terms, chunks)
    query_bigrams = tuple(pairwise(query_terms))
    for chunk in chunks:
        chunk.score = _chunk_score(chunk, query_terms, query_bigrams, weights)
    _apply_document_prior(chunks, entry_count)


def _term_weights(query_terms: Sequence[str], chunks: Sequence[_Chunk]) -> dict[str, float]:
    document_frequency: Counter[str] = Counter()
    for chunk in chunks:
        document_frequency.update(set(chunk.stems))
    total = len(chunks)
    return {
        term: max(0.05, math.log(1.0 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)))
        for term in query_terms
    }


def _chunk_score(
    chunk: _Chunk,
    query_terms: Sequence[str],
    query_bigrams: Sequence[tuple[str, str]],
    weights: dict[str, float],
) -> float:
    frequencies = Counter(chunk.stems)
    chunk_bigrams = set(pairwise(chunk.stems))
    score = 0.0
    for term in query_terms:
        frequency = frequencies.get(term, 0)
        if frequency:
            score += weights[term] * (1.0 + 0.5 * math.log(frequency))
        elif any(_prefix_match(term, stem) for stem in frequencies):
            score += weights[term] * 0.6
    for pair in query_bigrams:
        if pair in chunk_bigrams:
            score += 0.5 * (weights[pair[0]] + weights[pair[1]])
    return score


def _apply_document_prior(chunks: Sequence[_Chunk], entry_count: int) -> None:
    document_scores = [0.0] * entry_count
    for chunk in chunks:
        if chunk.score > 0:
            document_scores[chunk.entry_index] += chunk.score
    strongest = max(document_scores, default=0.0)
    if strongest <= 0:
        return
    for chunk in chunks:
        if chunk.score > 0:
            chunk.score += _DOCUMENT_PRIOR * document_scores[chunk.entry_index] / strongest


def _query_terms(query: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(query.lower()):
        if token in _STOPWORDS and not token.isdigit():
            continue
        stem = _stem(token)
        if stem and stem not in seen:
            seen.add(stem)
            terms.append(stem)
    if terms:
        return tuple(terms)
    for token in _TOKEN_RE.findall(query.lower()):
        stem = _stem(token)
        if stem and stem not in seen:
            seen.add(stem)
            terms.append(stem)
    return tuple(terms)


def _stems(text: str) -> tuple[str, ...]:
    return tuple(_stem(token) for token in _TOKEN_RE.findall(text.lower()))


def _has_vowel(word: str) -> bool:
    return any(character in "aeiou" for character in word)


def _stem(word: str) -> str:
    """Fold common English inflections so claim and prose forms can match.

    This is intentionally a conservative suffix folder rather than a full
    stemming algorithm: an over-aggressive stem would merge unrelated
    scientific terms, while ``_prefix_match`` already bridges the forms this
    folder leaves apart.
    """

    if len(word) <= 3 or not word.isalpha():
        return word
    return _fold_tail(_fold_derivation(_fold_plural(word)))


def _fold_plural(stem: str) -> str:
    if stem.endswith("ies") and len(stem) > 4:
        return f"{stem[:-3]}y"
    if stem.endswith(("sses", "shes", "ches", "xes", "zes")):
        return stem[:-2]
    if stem.endswith("s") and not stem.endswith(("ss", "us", "is")):
        return stem[:-1]
    return stem


_DERIVATIONS = (
    ("ational", "ate"),
    ("tional", "tion"),
    ("ization", "ize"),
    ("ation", "ate"),
    ("ition", "ite"),
    ("fulness", "ful"),
    ("iveness", "ive"),
    ("ousness", "ous"),
    ("ingly", ""),
    ("edly", ""),
    ("ement", ""),
    ("ment", ""),
    ("ness", ""),
    ("ing", ""),
    ("ed", ""),
)


def _fold_derivation(stem: str) -> str:
    for suffix, replacement in _DERIVATIONS:
        if not stem.endswith(suffix):
            continue
        base = stem[: -len(suffix)]
        if len(base) < 3 or not _has_vowel(base):
            continue
        folded = f"{base}{replacement}"
        if suffix in {"ing", "ed"} and len(folded) > 2 and folded[-1] == folded[-2] and folded[-1] not in "lsz":
            folded = folded[:-1]
        return folded
    return stem


def _fold_tail(stem: str) -> str:
    if stem.endswith("ive") and len(stem) > 5:
        stem = stem[:-3]
    if stem.endswith("ion") and len(stem) > 6:
        stem = stem[:-3]
    if stem.endswith("ly") and len(stem) > 5:
        stem = stem[:-2]
    if stem.endswith("al") and len(stem) > 5:
        stem = stem[:-2]
    if stem.endswith("y") and len(stem) > 4:
        stem = stem[:-1]
    return stem


def _prefix_match(first: str, second: str) -> bool:
    if first == second:
        return True
    shorter, longer = (first, second) if len(first) <= len(second) else (second, first)
    return len(shorter) >= _MIN_PREFIX_MATCH and longer.startswith(shorter)


def _sentence_spans(text: str, long_span_limit: int) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character in "\r\n":
            _append_span(spans, text, start, index)
            index += 1
            start = index
            continue
        boundary = _sentence_boundary(text, index) if character in ".!?" else None
        if boundary is None:
            index += 1
            continue
        span_end, next_start = boundary
        _append_span(spans, text, start, span_end)
        start = next_start
        index = next_start
    _append_span(spans, text, start, length)
    return _split_long_spans(text, spans, long_span_limit)


def _sentence_boundary(text: str, index: int) -> tuple[int, int] | None:
    end = index
    length = len(text)
    while end < length and text[end] in ".!?":
        end += 1
    while end < length and text[end] in _CLOSING_PUNCTUATION:
        end += 1
    if end >= length:
        return end, end
    if not text[end].isspace() or (text[index] == "." and _is_abbreviation(text, index)):
        return None
    next_start = end
    while next_start < length and text[next_start].isspace():
        next_start += 1
    if next_start < length and not _starts_sentence(text[next_start]):
        return None
    return end, next_start


def _starts_sentence(character: str) -> bool:
    return character.isupper() or character.isdigit() or character in _OPENING_PUNCTUATION


def _split_long_spans(
    text: str,
    spans: Sequence[tuple[int, int]],
    long_span_limit: int,
) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for start, end in spans:
        if end - start <= long_span_limit:
            result.append((start, end))
        else:
            result.extend(_split_long_span(text, start, end, long_span_limit))
    return tuple(result)


def _append_span(spans: list[tuple[int, int]], text: str, start: int, end: int) -> None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end > start:
        spans.append((start, end))


def _split_long_span(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    while end - start > limit:
        cut = text.rfind(" ", start + limit // 2, start + limit)
        if cut <= start:
            cut = text.find(" ", start + limit)
        if cut <= start or cut >= end:
            break
        _append_span(spans, text, start, cut)
        start = cut + 1
        while start < end and text[start].isspace():
            start += 1
    _append_span(spans, text, start, end)
    return spans


def _is_abbreviation(text: str, period: int) -> bool:
    start = period - 1
    while start >= 0 and not text[start].isspace():
        start -= 1
    word = text[start + 1 : period].strip("([{'\"").lower()
    if not word:
        return False
    if word in _ABBREVIATIONS:
        return True
    return len(word) == 1 and word.isalpha()


def _joinable(text: str, end: int, start: int) -> bool:
    return start <= end or not text[end:start].strip()


def _merge_interval(
    text: str,
    intervals: Sequence[tuple[int, int]],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for left, right in sorted((*intervals, (start, end))):
        if merged and _joinable(text, merged[-1][1], left):
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _selected_with(
    entries: Sequence[EvidenceEntry],
    selected: Sequence[Sequence[tuple[int, int]]],
    chunk: _Chunk,
) -> list[list[tuple[int, int]]]:
    updated = [list(intervals) for intervals in selected]
    updated[chunk.entry_index] = _merge_interval(
        entries[chunk.entry_index].text, updated[chunk.entry_index], chunk.start, chunk.end
    )
    return updated


def _extend_selection(
    entries: Sequence[EvidenceEntry],
    chunk: _Chunk,
    selected: list[list[tuple[int, int]]],
    max_bytes: int,
) -> bool:
    candidate = _selected_with(entries, selected, chunk)
    if len(_render(entries, candidate).encode("utf-8")) > max_bytes:
        return False
    for entry_index, intervals in enumerate(candidate):
        selected[entry_index] = intervals
    return True


def _expand_neighbors(
    entries: Sequence[EvidenceEntry],
    chunks: Sequence[_Chunk],
    selected: list[list[tuple[int, int]]],
    selected_chunks: set[int],
    max_bytes: int,
    top_score: float,
) -> None:
    priorities = _neighbor_priorities(chunks, selected_chunks, top_score)
    added = 0
    for index in sorted(priorities, key=lambda item: (-priorities[item], chunks[item].entry_index, chunks[item].start)):
        if added >= _NEIGHBOR_LIMIT:
            break
        if _extend_selection(entries, chunks[index], selected, max_bytes):
            selected_chunks.add(index)
            added += 1


def _neighbor_priorities(
    chunks: Sequence[_Chunk],
    selected_chunks: set[int],
    top_score: float,
) -> dict[int, float]:
    by_entry: dict[int, list[int]] = {}
    for index, chunk in enumerate(chunks):
        by_entry.setdefault(chunk.entry_index, []).append(index)
    priorities: dict[int, float] = {}
    for index in tuple(selected_chunks):
        chunk = chunks[index]
        if chunk.score < top_score * 0.6:
            continue
        order = by_entry[chunk.entry_index]
        position = order.index(index)
        for offset in (-1, 1):
            neighbor_position = position + offset
            if not 0 <= neighbor_position < len(order):
                continue
            neighbor = order[neighbor_position]
            if neighbor not in selected_chunks:
                priorities[neighbor] = max(priorities.get(neighbor, 0.0), chunk.score * _NEIGHBOR_DAMPING)
    return priorities


def _fill_remaining(
    entries: Sequence[EvidenceEntry],
    chunks: Sequence[_Chunk],
    ranked: Sequence[int],
    selected: list[list[tuple[int, int]]],
    selected_chunks: set[int],
    max_bytes: int,
    floor: float,
) -> None:
    """Use space the primary ranking could not fill on weaker query matches."""

    for index in ranked:
        chunk = chunks[index]
        if chunk.score >= floor:
            continue
        if chunk.score <= 0:
            break
        if index in selected_chunks:
            continue
        if _extend_selection(entries, chunk, selected, max_bytes):
            selected_chunks.add(index)


def _render(entries: Sequence[EvidenceEntry], selected: Sequence[Sequence[tuple[int, int]]]) -> str:
    items: list[dict[str, object]] = []
    for entry_index, intervals in enumerate(selected):
        entry = entries[entry_index]
        citation = {
            "scope_id": entry.scope_id,
            "memory_ref": entry.memory_ref.model_dump(mode="json"),
            "entry_id": entry.entry_id,
            "entry_version_id": entry.entry_version_id,
        }
        for start, end in intervals:
            items.append({
                "citation": citation,
                "content": entry.text[start:end],
                "start": start,
                "end": end,
            })
    envelope = {"trust": "untrusted_history", "items": items}
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))
