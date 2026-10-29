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

"""Propagate Source withdrawal and restoration through evidence dependencies.

A Scope owns one dependency DAG whose roots are Sources and whose descendants
are registered Memory entries or Topic Memory artifacts. ``all`` nodes need
every parent available; ``any`` nodes need one. Availability is persisted with
the graph so a restarted Runtime answers the same way, and native Memory entry
states are reconciled against the derived bitmap so search, Context, and list
projections stay consistent with it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest, RetireMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchHit
    from powercontext.builtin.runtime.application import BuiltinRuntime

NodeKind = Literal["source", "memory", "topic"]
Policy = Literal["all", "any"]

_STATE_VERSION = 1
_MAX_REASON_LENGTH = 512
_PURPOSES = frozenset({"generation", "audit"})


class SourceLifecycleError(ValueError):
    """Reject an invalid lifecycle registration, event, or resolution."""


class SourceLifecycleEvent(BaseModel):
    """One validated availability transition for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: Annotated[int, Field(strict=True, gt=0)]
    sequence: Annotated[int, Field(strict=True, gt=0)]
    available: Annotated[bool, Field(strict=True)]
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")  # noqa: TRY003
        return value


def _empty_state() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "next_order": 0, "nodes": {}}


def _decode_state(value: str) -> dict[str, Any]:
    try:
        state = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise SourceLifecycleError("lifecycle-state") from error
    if (
        not isinstance(state, dict)
        or state.get("version") != _STATE_VERSION
        or not isinstance(state.get("nodes"), dict)
        or not isinstance(state.get("next_order"), int)
    ):
        raise SourceLifecycleError("lifecycle-state")
    return state


def _encode_state(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def _reference_value(node: dict[str, Any]) -> SourceRef | MemoryCitation | ArtifactRef:
    reference = node["reference"]
    if node["kind"] == "source":
        return SourceRef.model_validate(reference)
    if node["kind"] == "memory":
        return MemoryCitation.model_validate(reference)
    return ArtifactRef.model_validate(reference)


def _evaluate(state: dict[str, Any], parents: Sequence[str], policy: Policy) -> bool:
    flags = [bool(state["nodes"][parent]["available"]) for parent in parents]
    return all(flags) if policy == "all" else any(flags)


def _binding_is_same(
    node: dict[str, Any],
    *,
    kind: NodeKind,
    reference: dict[str, Any],
    parents: tuple[str, ...],
    policy: Policy | None,
    revision: int | None,
) -> bool:
    return (
        node["kind"] == kind
        and node["reference"] == reference
        and tuple(node["parents"]) == parents
        and node["policy"] == policy
        and node.get("revision") == revision
    )


def _validate_node_id(node_id: object) -> str:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node-id")
    return node_id


def _validate_revision(revision: object) -> int:
    if type(revision) is not int or revision <= 0:
        raise SourceLifecycleError("revision")
    return revision


def _validate_policy(policy: object) -> Policy:
    if policy not in {"all", "any"}:
        raise SourceLifecycleError("policy")
    return policy


class SourceLifecycleApplication:
    """Select the source lifecycle service for one Scope."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, self._database, scope_id)


class ScopedSourceLifecycle:
    """Register one Scope's evidence DAG and apply Source lifecycle events."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)

    # ----------------------------------------------------------------- registrations

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        """Register one readable Source as a graph root at a lifecycle revision."""

        database = self._require_database()
        node = _validate_node_id(node_id)
        revision = _validate_revision(revision)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-reference")
        reference = source_ref.model_dump(mode="json")
        state = await self._load(database)
        existing = state["nodes"].get(node)
        if existing is not None:
            if _binding_is_same(
                existing,
                kind="source",
                reference=reference,
                parents=(),
                policy=None,
                revision=revision,
            ):
                return
            raise SourceLifecycleError("node-conflict")
        await self._require_source(source_ref)
        self._add_node(
            state,
            node,
            kind="source",
            reference=reference,
            parents=(),
            policy=None,
            available=True,
            revision=revision,
        )
        await self._save(database, state)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Iterable[str],
        policy: Policy,
    ) -> None:
        """Register one readable Memory entry derived from earlier nodes."""

        database = self._require_database()
        node = _validate_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory-reference")
        reference = citation.model_dump(mode="json")
        validated_policy = _validate_policy(policy)
        state = await self._load(database)
        existing = state["nodes"].get(node)
        if existing is not None:
            if _binding_is_same(
                existing,
                kind="memory",
                reference=reference,
                parents=self._parent_values(parents),
                policy=validated_policy,
                revision=None,
            ):
                await self._sync_memory_nodes(state, (node,), "source lifecycle registration")
                return
            raise SourceLifecycleError("node-conflict")
        resolved_parents = self._require_parents(state, node, parents)
        await self._require_memory(citation)
        reference = citation.model_dump(mode="json")
        available = _evaluate(state, resolved_parents, validated_policy)
        self._add_node(
            state,
            node,
            kind="memory",
            reference=reference,
            parents=resolved_parents,
            policy=validated_policy,
            available=available,
            revision=None,
        )
        await self._save(database, state)
        await self._sync_memory_nodes(state, (node,), "source lifecycle registration")

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: Iterable[str],
        policy: Policy,
    ) -> None:
        """Register one readable Topic Memory revision derived from earlier nodes."""

        database = self._require_database()
        node = _validate_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("topic-reference")
        reference = artifact_ref.model_dump(mode="json")
        validated_policy = _validate_policy(policy)
        state = await self._load(database)
        existing = state["nodes"].get(node)
        if existing is not None:
            if _binding_is_same(
                existing,
                kind="topic",
                reference=reference,
                parents=self._parent_values(parents),
                policy=validated_policy,
                revision=None,
            ):
                return
            raise SourceLifecycleError("node-conflict")
        resolved_parents = self._require_parents(state, node, parents)
        await self._require_topic(artifact_ref)
        available = _evaluate(state, resolved_parents, validated_policy)
        self._add_node(
            state,
            node,
            kind="topic",
            reference=reference,
            parents=resolved_parents,
            policy=validated_policy,
            available=available,
            revision=None,
        )
        await self._save(database, state)

    # ------------------------------------------------------------------------ events

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source event, persist the derived bitmap, and return active ids."""

        database = self._require_database()
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event")
        state = await self._load(database)
        node = state["nodes"].get(event.node_id)
        if node is None or node["kind"] != "source":
            raise SourceLifecycleError("source-unknown")
        if event.revision != node.get("revision"):
            raise SourceLifecycleError("revision")
        last_sequence = node.get("last_sequence")
        if last_sequence is not None:
            if event.sequence < last_sequence:
                raise SourceLifecycleError("sequence")
            if event.sequence == last_sequence:
                if node.get("last_event") != event.model_dump(mode="json"):
                    raise SourceLifecycleError("sequence")
                await self._sync_memory_nodes(state, self._memory_node_ids(state), event.reason)
                return self._available_ids(state)
        node["available"] = event.available
        node["last_sequence"] = event.sequence
        node["last_event"] = event.model_dump(mode="json")
        self._propagate(state)
        await self._save(database, state)
        await self._sync_memory_nodes(state, self._memory_node_ids(state), event.reason)
        return self._available_ids(state)

    # -------------------------------------------------------------------- resolution

    async def resolve(self, node_id: str, /, *, purpose: Literal["generation", "audit"]) -> Any:
        """Return the original reference when the requested use is permitted."""

        database = self._require_database()
        if purpose not in _PURPOSES:
            raise SourceLifecycleError("purpose")
        state = await self._load(database)
        node = state["nodes"].get(_validate_node_id(node_id))
        if node is None:
            raise SourceLifecycleError("node-unknown")
        if purpose == "generation" and not node["available"]:
            raise SourceLifecycleError("node-unavailable")
        return _reference_value(node)

    async def filter_topic_hits(
        self,
        hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[TopicMemorySearchHit, ...]:
        """Drop hits whose registered Topic revision lost all support."""

        state = await self._load(self._database)
        unavailable = {
            (node["reference"]["family"], node["reference"]["artifact_id"], node["reference"]["revision"])
            for node in state["nodes"].values()
            if node["kind"] == "topic" and not node["available"]
        }
        if not unavailable:
            return tuple(hits)
        return tuple(
            hit
            for hit in hits
            if (hit.artifact_ref.family, hit.artifact_ref.artifact_id, hit.artifact_ref.revision) not in unavailable
        )

    # ------------------------------------------------------------------------ storage

    def _require_database(self) -> AsyncDatabase:
        if self._database is None:
            raise SourceLifecycleError("lifecycle-unavailable")
        return self._database

    async def _load(self, database: AsyncDatabase | None) -> dict[str, Any]:
        if database is None:
            return _empty_state()
        async with database.transaction() as connection:
            row = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_TABLE.c.payload).where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id)
                )
            ).first()
        return _empty_state() if row is None else _decode_state(row[0])

    async def _save(self, database: AsyncDatabase, state: dict[str, Any]) -> None:
        payload = _encode_state(state)
        async with database.transaction() as connection:
            existing = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_TABLE.c.scope_id).where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id)
                )
            ).first()
            if existing is None:
                await connection.execute(insert(SOURCE_LIFECYCLE_TABLE).values(scope_id=self.scope_id, payload=payload))
            else:
                await connection.execute(
                    update(SOURCE_LIFECYCLE_TABLE)
                    .where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id)
                    .values(payload=payload)
                )

    # ---------------------------------------------------------------------- graph ops

    def _add_node(
        self,
        state: dict[str, Any],
        node_id: str,
        *,
        kind: NodeKind,
        reference: dict[str, Any],
        parents: tuple[str, ...],
        policy: Policy | None,
        available: bool,
        revision: int | None,
    ) -> None:
        state["nodes"][node_id] = {
            "kind": kind,
            "reference": reference,
            "parents": list(parents),
            "policy": policy,
            "order": state["next_order"],
            "available": available,
            "revision": revision,
            "last_sequence": None,
            "last_event": None,
        }
        state["next_order"] += 1

    def _require_parents(self, state: dict[str, Any], node_id: str, parents: Iterable[str]) -> tuple[str, ...]:
        values = self._parent_values(parents)
        if not values:
            raise SourceLifecycleError("parents")
        if len(set(values)) != len(values):
            raise SourceLifecycleError("parents")
        if node_id in values:
            raise SourceLifecycleError("cycle")
        for parent in values:
            if parent not in state["nodes"]:
                raise SourceLifecycleError("parent-unknown")
        return values

    @staticmethod
    def _parent_values(parents: Iterable[str]) -> tuple[str, ...]:
        try:
            values = tuple(parents)
        except TypeError as error:
            raise SourceLifecycleError("parents") from error
        if any(not isinstance(parent, str) or not parent.strip() for parent in values):
            raise SourceLifecycleError("parents")
        return values

    @staticmethod
    def _propagate(state: dict[str, Any]) -> None:
        ordered = sorted(state["nodes"].values(), key=lambda item: item["order"])
        for node in ordered:
            if node["kind"] == "source":
                continue
            node["available"] = _evaluate(state, node["parents"], node["policy"])

    @staticmethod
    def _available_ids(state: dict[str, Any]) -> list[str]:
        return sorted(node_id for node_id, node in state["nodes"].items() if node["available"])

    @staticmethod
    def _memory_node_ids(state: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            sorted(
                (node_id for node_id, node in state["nodes"].items() if node["kind"] == "memory"),
                key=lambda node_id: state["nodes"][node_id]["order"],
            )
        )

    # --------------------------------------------------------------------- references

    async def _require_source(self, reference: SourceRef) -> None:
        stored = None
        try:
            async with self._runtime._context(self.scope_id) as context:
                catalog = context.sources.catalog
                stored = next(
                    (source for source in await context.sources.list() if catalog.as_ref(source) == reference),
                    None,
                )
                if stored is not None:
                    await context.sources.read(stored)
        except Exception as error:
            raise SourceLifecycleError("source-reference") from error
        if stored is None:
            raise SourceLifecycleError("source-reference")

    async def _require_memory(self, citation: MemoryCitation) -> None:
        try:
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except Exception as error:
            raise SourceLifecycleError("memory-reference") from error

    async def _require_topic(self, reference: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                reference.family,
                reference.artifact_id,
                reference.revision,
            )
        except Exception as error:
            raise SourceLifecycleError("topic-reference") from error

    # ------------------------------------------------------------------ native memory

    async def _sync_memory_nodes(self, state: dict[str, Any], node_ids: Iterable[str], reason: str) -> None:
        selected = [
            state["nodes"][node_id] for node_id in node_ids if state["nodes"].get(node_id, {}).get("kind") == "memory"
        ]
        if not selected:
            return
        page = await self._runtime.memory.for_scope(self.scope_id).list(include_inactive=True)
        states = {record.entry.entry_id: record.state for record in page.entries}
        for node in sorted(selected, key=lambda item: item["order"]):
            entry_id = node["reference"]["entry_id"]
            desired = bool(node["available"])
            current = states.get(entry_id)
            if current is None or (current == "active") == desired:
                continue
            await self._set_memory_state(entry_id, desired, reason)
            states[entry_id] = "active" if desired else "inactive"

    async def _set_memory_state(self, entry_id: str, desired: bool, reason: str) -> None:
        normalized_reason = reason[:_MAX_REASON_LENGTH]
        if not desired:
            page = await self._runtime.memory.for_scope(self.scope_id).list(include_inactive=True)
            record = next((entry for entry in page.entries if entry.entry.entry_id == entry_id), None)
            if record is None:
                raise SourceLifecycleError("memory-entry")
            await self._runtime.memory.for_scope(self.scope_id).retire(
                RetireMemoryEntryRequest(citation=record.citation, reason=normalized_reason)
            )
            return
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            head = await service.head(context.artifacts.memory_artifact_id)
            entries = await service.entries(head)
            target = next((entry for entry in entries if entry.entry_id == entry_id), None)
            if target is None:
                raise SourceLifecycleError("memory-entry")
            await service.reactivate(head, entries=(target,), reason=normalized_reason)
