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

"""Compatible Delta decoding while keeping the Pack wire contract strict."""

from __future__ import annotations

import json

import pytest

from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


def _delta_payload(base: ContextPack, target: ContextPack) -> dict[str, object]:
    return json.loads(ContextPackDelta.between(base, target).to_bytes())


def _wire(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode()


def test_delta_decoder_accepts_equivalent_json_layouts_and_keeps_canonical_output() -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build((b"a", b"b", b"new", b"tail"))
    delta = ContextPackDelta.between(base, target)
    payload = _delta_payload(base, target)

    for wire in (_wire(payload), _wire(dict(reversed(list(payload.items()))))):
        decoded = ContextPackDelta.from_bytes(wire)
        assert decoded == delta
        assert decoded.to_bytes() == delta.to_bytes()
        assert apply_context_pack_delta(base, decoded).to_bytes() == target.to_bytes()

    assert delta.to_bytes() == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def test_delta_decoder_still_rejects_semantically_invalid_payloads() -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build((b"a", b"b", b"new"))
    payload = _delta_payload(base, target)

    invalid_payloads = [
        {key: value for key, value in payload.items() if key != "schema"},
        {**payload, "extra": 1},
        {**payload, "schema": "powercontext.context-pack-delta.v2"},
        {**payload, "base_digest": "sha256:not-a-digest"},
        {**payload, "target_digest": "sha256:not-a-digest"},
        {**payload, "prefix_items": True},
        {**payload, "prefix_items": 1.5},
        {**payload, "prefix_items": -1},
        {**payload, "appended": ["not base64!"]},
        {**payload, "appended": ["YQ"]},
    ]
    for invalid in invalid_payloads:
        with pytest.raises(ContextPackError):
            ContextPackDelta.from_bytes(_wire(invalid))

    delta = ContextPackDelta.between(base, target)
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(ContextPack.build((b"other",)), delta)
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, target.digest, 4, ()))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, "sha256:" + "0" * 64, 0, (b"x",)))


def test_pack_decoder_keeps_its_byte_count_and_canonical_checks() -> None:
    pack = ContextPack.build((b"alpha",))
    payload = json.loads(pack.to_bytes())
    wire = _wire(payload)

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(wire)
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(_wire({**payload, "byte_count": len(wire)}))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(pack.to_bytes().replace(pack.digest.encode(), b"sha256:" + b"0" * 64))
    with pytest.raises(ContextPackError):
        ContextPack.from_content("\ud800")
    with pytest.raises(ContextPackError):
        ContextPackDelta("\ud800", "\ud800", 0, ()).to_bytes()
