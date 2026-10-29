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

# ruff: noqa: TRY003

"""Versioned Context Pack wire format with prefix-based incremental deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CONTEXT_CHUNK_BYTES = 512

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(Exception):
    """Raised when a Context Pack or Delta violates the wire contract."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A complete context represented as ordered byte chunks and their digest."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: Iterable[bytes]) -> ContextPack:
        normalized = _normalize_items(items)
        digest = _digest(_unsigned_payload(normalized))
        return cls(items=normalized, digest=digest, byte_count=len(_pack_bytes(normalized, digest)))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CONTEXT_CHUNK_BYTES) -> ContextPack:
        return cls.build(_chunk_text(content, chunk_bytes))

    def to_bytes(self) -> bytes:
        return _pack_bytes(self.items, self.digest)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        payload = _load_json(data, "Context Pack")
        if set(payload) != _PACK_FIELDS:
            raise ContextPackError("Context Pack must contain exactly schema, items, digest and byte_count")
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("Context Pack schema is not supported")
        items = _decode_items(payload["items"], "Context Pack items")
        digest = payload["digest"]
        if not _is_digest(digest):
            raise ContextPackError("Context Pack digest must be sha256:<lowercase hex>")
        expected = _digest(_unsigned_payload(items))
        if digest != expected:
            raise ContextPackError("Context Pack digest does not match its items")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(data):
            raise ContextPackError("Context Pack byte_count does not match its serialized length")
        if _pack_bytes(items, expected) != data:
            raise ContextPackError("Context Pack JSON is not canonical")
        return cls(items=items, digest=expected, byte_count=byte_count)


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A base prefix plus a target suffix sufficient to rebuild one Context Pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta inputs must be Context Packs")
        limit = min(len(base.items), len(target.items))
        prefix_items = 0
        while prefix_items < limit and base.items[prefix_items] == target.items[prefix_items]:
            prefix_items += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_items,
            appended=target.items[prefix_items:],
        )

    def to_bytes(self) -> bytes:
        return _canonical_json({
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_item(item) for item in self.appended],
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        payload = _load_json(data, "Context Pack delta")
        if set(payload) != _DELTA_FIELDS:
            raise ContextPackError(
                "Context Pack delta must contain exactly schema, base_digest, target_digest, prefix_items and appended"
            )
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("Context Pack delta schema is not supported")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not _is_digest(base_digest) or not _is_digest(target_digest):
            raise ContextPackError("delta digests must be sha256:<lowercase hex>")
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")
        appended = _decode_items(payload["appended"], "Context Pack delta appended")
        delta = cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=appended,
        )
        if delta.to_bytes() != data:
            raise ContextPackError("Context Pack delta JSON is not canonical")
        return delta


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate one delta against its base and return the complete target pack."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta application requires a Context Pack and a Context Pack delta")
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base_digest does not match the supplied base pack")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items is outside the base pack")
    target = ContextPack.build(base.items[: delta.prefix_items] + tuple(delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target_digest does not match the rebuilt target pack")
    return target


def _normalize_items(items: Iterable[bytes]) -> tuple[bytes, ...]:
    if isinstance(items, (bytes, bytearray, str)):
        raise ContextPackError("Context Pack items must be an iterable of bytes")
    try:
        normalized = tuple(items)
    except TypeError as error:
        raise ContextPackError("Context Pack items must be an iterable of bytes") from error
    for item in normalized:
        if not isinstance(item, bytes):
            raise ContextPackError("every Context Pack item must be bytes")
    return normalized


def _chunk_text(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if not isinstance(content, str):
        raise ContextPackError("context content must be a string")
    if type(chunk_bytes) is not int or chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")
    if not content:
        return ()
    chunks: list[bytes] = []
    current: list[str] = []
    current_size = 0
    for character in content:
        try:
            encoded = character.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ContextPackError("context content must be valid Unicode") from error
        size = len(encoded)
        if size > chunk_bytes:
            raise ContextPackError("chunk_bytes cannot form a valid UTF-8 boundary")
        if current_size + size > chunk_bytes:
            chunks.append("".join(current).encode("utf-8"))
            current = [character]
            current_size = size
        else:
            current.append(character)
            current_size += size
    if current:
        chunks.append("".join(current).encode("utf-8"))
    return tuple(chunks)


def _unsigned_payload(items: tuple[bytes, ...]) -> dict[str, object]:
    return {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": [_encode_item(item) for item in items],
    }


def _pack_bytes(items: tuple[bytes, ...], digest: str) -> bytes:
    unsigned = _unsigned_payload(items)
    byte_count = 0
    for _ in range(64):
        data = _canonical_json({**unsigned, "digest": digest, "byte_count": byte_count})
        if len(data) == byte_count:
            return data
        byte_count = len(data)
    raise ContextPackError("Context Pack byte_count did not converge")


def _digest(unsigned: dict[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as error:
        raise ContextPackError("Context Pack JSON must be valid Unicode") from error


def _load_json(data: bytes, label: str) -> dict[str, object]:
    if not isinstance(data, bytes):
        raise ContextPackError(f"{label} wire data must be bytes")
    try:
        payload = json.loads(data)
    except (ValueError, TypeError) as error:
        raise ContextPackError(f"{label} must be valid JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError(f"{label} must be a JSON object")
    return payload


def _decode_items(value: object, label: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{label} must be a JSON array")
    items: list[bytes] = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError(f"{label} must contain Base64 strings")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError) as error:
            raise ContextPackError(f"{label} contains invalid Base64") from error
        if _encode_item(raw) != encoded:
            raise ContextPackError(f"{label} must use canonical Base64")
        items.append(raw)
    return tuple(items)


def _encode_item(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _is_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CONTEXT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
