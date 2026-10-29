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

import json

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedContextBuilder,
    PreparedMemoryCandidates,
)

MEMORY_REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)
SCOPE = "scope:current"


def _hit(entry_id: str, text: str, *, memory_ref: ArtifactRef = MEMORY_REF, version: str = "v1") -> MemoryHit:
    return MemoryHit(
        memory_ref=memory_ref,
        entry_id=entry_id,
        entry_version_id=f"{entry_id}-{version}",
        text=text,
        score=1.0,
        matched_by=("fts",),
    )


def _build(
    query: str, entries: dict[str, str], *, max_bytes: int = 1800, scope_id: str = SCOPE
) -> PreparedContextBuild:
    hits = tuple(_hit(entry_id, text) for entry_id, text in entries.items())
    request = PrepareContextRequest(query=query, max_bytes=max_bytes, evidence_selection=True)
    return PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id=scope_id,
        memory_candidates=(PreparedMemoryCandidates(scope_id=scope_id, memory_ref=MEMORY_REF, hits=hits),),
    )


def _items(build: PreparedContextBuild) -> list[dict[str, object]]:
    assert build.context.content is not None
    envelope = json.loads(build.context.content.splitlines()[-2])
    assert envelope["trust"] == "untrusted_history"
    return envelope["items"]


def test_query_change_selects_different_late_sentences() -> None:
    prefix = "General administrative notes. " * 70
    entries = {
        "alpha": prefix + "The cobalt sensor is calibrated at 37 degrees.",
        "beta": prefix + "The amber valve opens at 19 degrees.",
    }
    cobalt = _build("cobalt sensor calibrated", entries, max_bytes=1200)
    amber = _build("amber valve opens", entries, max_bytes=1200)

    assert cobalt.fragments
    assert all(fragment.address.entry_id == "alpha" for fragment in cobalt.fragments)
    assert any("37" in fragment.text for fragment in cobalt.fragments)
    assert amber.fragments
    assert all(fragment.address.entry_id == "beta" for fragment in amber.fragments)
    assert any("19" in fragment.text for fragment in amber.fragments)
    for build in (cobalt, amber):
        assert build.context.content is not None
        assert build.context.content_bytes == len(build.context.content.encode("utf-8")) <= 1200


def test_late_document_evidence_can_produce_multiple_fragments_from_one_entry() -> None:
    text = (
        "Opening context without the target terms. "
        + "Unrelated background statement. " * 30
        + "The cobalt titration marker binds receptor Z. "
        + "Neutral bridge one. Neutral bridge two. Neutral bridge three. "
        + "Cobalt titration reaches 42 units."
    )
    build = _build("cobalt titration 42", {"one": text}, max_bytes=2000)

    assert len(build.fragments) >= 2
    starts = [fragment.start for fragment in build.fragments]
    assert starts == sorted(starts)
    assert all(fragment.address == build.fragments[0].address for fragment in build.fragments)
    assert build.fragments[0].start > 500
    for fragment in build.fragments:
        assert fragment.text == text[fragment.start : fragment.end]
    assert any("marker" in fragment.text for fragment in build.fragments)
    assert any("42" in fragment.text for fragment in build.fragments)
    assert len(build.origins) == len(build.fragments) == len(_items(build))


def test_unicode_budget_is_measured_on_the_complete_output() -> None:
    sentence = "\u6d77\u6d0b\u89c2\u6d4b station records temperatures. "
    text = sentence * 40
    build = _build("\u6d77\u6d0b\u89c2\u6d4b", {"one": text}, max_bytes=1200)

    assert build.fragments
    assert build.context.content is not None
    assert build.context.content_bytes == len(build.context.content.encode("utf-8")) <= 1200
    assert "\u6d77\u6d0b\u89c2\u6d4b" in build.context.content
    for fragment in build.fragments:
        assert type(fragment.start) is int and type(fragment.end) is int
        assert 0 <= fragment.start < fragment.end <= len(text)
        assert fragment.text == text[fragment.start : fragment.end]


def test_json_escaping_keeps_literal_text_and_byte_count() -> None:
    text = 'Intro. The "cobalt" sensor\nrecords 37\u00b0C and a \\ marker. Tail.'
    build = _build("cobalt sensor 37", {"one": text}, max_bytes=1800)

    assert build.fragments
    assert build.context.content is not None
    assert build.context.content_bytes == len(build.context.content.encode("utf-8")) <= 1800
    items = _items(build)
    for fragment, item in zip(build.fragments, items, strict=True):
        assert fragment.text == text[fragment.start : fragment.end]
        assert item["content"] == fragment.text


def test_unmatched_query_returns_empty_context() -> None:
    text = "A plain note about office supplies and nothing else."
    build = _build("zebra quantum", {"one": text}, max_bytes=1800)

    assert build.context.status == "empty"
    assert build.fragments == ()
    assert build.origins == ()


def test_budget_boundary_counts_envelope_and_citations() -> None:
    text = "The single sentence holds the only evidence about larch needles."
    generous = _build("larch needles evidence", {"one": text}, max_bytes=1800)
    assert generous.fragments
    exact = generous.context.content_bytes

    at_boundary = _build("larch needles evidence", {"one": text}, max_bytes=exact)
    assert at_boundary.fragments
    assert at_boundary.context.content_bytes == exact

    below_boundary = _build("larch needles evidence", {"one": text}, max_bytes=exact - 1)
    assert below_boundary.context.status == "empty"
    assert below_boundary.context.content is None
    assert below_boundary.fragments == ()
    assert below_boundary.origins == ()


def test_identical_text_in_different_scopes_keeps_each_address() -> None:
    text = "The shared sentence mentions the peregrine falcon and its prey."
    request = PrepareContextRequest(query="peregrine falcon prey", max_bytes=3000, evidence_selection=True)
    build = PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id="scope:a",
        memory_candidates=(
            PreparedMemoryCandidates(scope_id="scope:a", memory_ref=MEMORY_REF, hits=(_hit("shared", text),)),
            PreparedMemoryCandidates(scope_id="scope:b", memory_ref=MEMORY_REF, hits=(_hit("shared", text),)),
        ),
    )

    assert {fragment.address.memory.scope_id for fragment in build.fragments} == {"scope:a", "scope:b"}
    items = _items(build)
    for fragment, origin, item in zip(build.fragments, build.origins, items, strict=True):
        assert isinstance(origin, MemoryEntryAddress)
        assert origin == fragment.address
        assert fragment.text == text[fragment.start : fragment.end]
        assert item["citation"] == {
            "scope_id": fragment.address.memory.scope_id,
            "memory_ref": MEMORY_REF.model_dump(mode="json"),
            "entry_id": "shared",
            "entry_version_id": "shared-v1",
        }
        assert item["content"] == fragment.text
        assert item["start"] == fragment.start
        assert item["end"] == fragment.end


def test_default_off_preserves_ordinary_prepare() -> None:
    text = "A station records temperature."
    request = PrepareContextRequest(query="temperature", max_bytes=1200)
    assert request.evidence_selection is False
    build = PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id=SCOPE,
        memory_candidates=(PreparedMemoryCandidates(scope_id=SCOPE, memory_ref=MEMORY_REF, hits=(_hit("one", text),)),),
    )

    assert build.fragments == ()
    assert build.context.status == "ready"
    assert build.context.content is not None
    assert text in build.context.content
    items = _items(build)
    assert items[0]["citation"] == {
        "memory_ref": MEMORY_REF.model_dump(mode="json"),
        "entry_id": "one",
        "entry_version_id": "one-v1",
    }

    explicit_off = PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query="temperature", max_bytes=1200, evidence_selection=False),
        current_scope_id=SCOPE,
        memory_candidates=(PreparedMemoryCandidates(scope_id=SCOPE, memory_ref=MEMORY_REF, hits=(_hit("one", text),)),),
    )
    assert explicit_off == build


def test_empty_context_when_no_fragment_fits() -> None:
    build = _build("anything", {"one": "word " * 400}, max_bytes=512)

    assert build.context.status == "empty"
    assert build.context.content is None
    assert build.context.content_bytes == 0
    assert build.fragments == ()
    assert build.origins == ()


def test_no_candidates_returns_empty_context() -> None:
    build = PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query="anything", max_bytes=1200, evidence_selection=True),
        current_scope_id=SCOPE,
    )

    assert build.context.status == "empty"
    assert build.fragments == ()
    assert build.origins == ()


def test_short_cjk_query_matches_contiguous_text_without_spaces() -> None:
    text = "\u8bc1\u636e" * 1000
    build = _build("\u8bc1\u636e", {"one": text}, max_bytes=16384)

    assert build.context.content is not None
    assert build.fragments
    assert "\u8bc1\u636e" in build.context.content
    for fragment in build.fragments:
        assert fragment.text == text[fragment.start : fragment.end]


def test_long_cjk_span_is_split_to_fit_byte_budget() -> None:
    text = "\u8bc1\u636e" * 1000
    build = _build("\u8bc1\u636e\u8bc1\u636e\u8bc1\u636e", {"one": text}, max_bytes=4096)

    assert build.context.content is not None
    assert build.fragments
    assert build.context.content_bytes == len(build.context.content.encode("utf-8")) <= 4096
    assert any("\u8bc1\u636e" in fragment.text for fragment in build.fragments)
    for fragment in build.fragments:
        assert 0 <= fragment.start < fragment.end <= len(text)
        assert fragment.text == text[fragment.start : fragment.end]


def test_single_character_cjk_query_is_searchable() -> None:
    text = "\u8bc1\u636e\u8bc1\u636e\u8bc1\u636e\u8bc1\u636e"
    build = _build("\u8bc1", {"one": text}, max_bytes=16384)

    assert build.fragments
    assert any("\u8bc1" in fragment.text for fragment in build.fragments)

