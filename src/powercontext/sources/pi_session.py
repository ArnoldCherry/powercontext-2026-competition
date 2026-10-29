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

# ruff: noqa: TRY003, TRY004

"""Pi v3 session branch parsing and event-level captured Sources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"
PI_SESSION_CHECKPOINT_SCHEMA = "powercontext.pi-session-history.v1"


class PiSessionInput(BaseModel):
    """One complete Pi v3 JSONL archive and an optional selected leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible Pi session event in original record form."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @field_validator("session_id", "event_id", "event_type", "role")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value:
            raise ValueError("Pi event identity fields must be non-empty")
        return value


class PiEventSource(Source):
    """Captured Pi event Source retaining the complete original record."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_identity(self) -> PiEventSource:
        expected = pi_event_source_id(self.session_id, self.event_id)
        if self.name != expected or self.source_id != expected:
            raise ValueError("Pi event source name and source_id must match the event identity")
        return self


class PiEventSourceAdapter:
    """Resolve and read event-level Pi Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one Pi event as canonical text evidence without truncation."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


@dataclass(frozen=True, slots=True)
class _PiArchive:
    session_id: str
    records: tuple[dict[str, JsonValue], ...]
    by_id: dict[str, dict[str, JsonValue]]
    order: tuple[str, ...]


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse one Pi v3 archive and return visible events on the selected branch."""

    archive = _parse_archive(value.content)
    events: list[PiEventInput] = []
    for record in _selected_chain(archive, value.leaf_id):
        event = _event_from_record(archive.session_id, record)
        if event is not None:
            pi_event_source_id(event.session_id, event.event_id)
            events.append(event)
    return tuple(events)


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return the stable, delimiter-safe identity for one Pi event."""

    if not isinstance(session_id, str) or not session_id or not isinstance(event_id, str) or not event_id:
        raise ValueError("Pi session and event ids must be non-empty strings")
    source_id = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"Pi event source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


class PiSessionConnector:
    """Import the selected Pi branch incrementally with durable checkpoints."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession) -> ConnectorRunCompletion:
        archive = _parse_archive(self._path.read_bytes())
        imported = _load_checkpoint(session.checkpoint)
        records_by_identity: dict[str, dict[str, JsonValue]] = {}
        for event_id, record in archive.by_id.items():
            try:
                identity = pi_event_source_id(archive.session_id, event_id)
            except ValueError:
                continue
            records_by_identity[identity] = record
        for identity, digest in imported.items():
            record = records_by_identity.get(identity)
            if record is None:
                raise ValueError("an imported Pi event is missing")
            if _record_digest(record) != digest:
                raise ValueError("an imported Pi event was modified")
        all_events = tuple(
            event for record in archive.records if (event := _event_from_record(archive.session_id, record)) is not None
        )
        selected = {
            pi_event_source_id(event.session_id, event.event_id)
            for record in _selected_chain(archive, self._leaf_id)
            if (event := _event_from_record(archive.session_id, record)) is not None
        }
        unsafe = False
        for event in all_events:
            identity = pi_event_source_id(event.session_id, event.event_id)
            if identity not in selected or identity in imported:
                continue
            outcome = await session.submit(identity, PI_EVENT_SOURCE_NAME, event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                unsafe = True
                continue
            imported[identity] = _record_digest(event.record)
        checkpoint = {"schema": PI_SESSION_CHECKPOINT_SCHEMA, "events": imported}
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.INCOMPLETE if unsafe else ConnectorRunStatus.COMPLETE,
            checkpoint=checkpoint,
        )


def _parse_archive(content: bytes) -> _PiArchive:
    records = _decode_records(content)
    session_id = _session_id(records[0])
    by_id, order = _index_records(records[1:])
    return _PiArchive(session_id=session_id, records=tuple(records[1:]), by_id=by_id, order=order)


def _reject_json_constant(value: str) -> JsonValue:
    raise ValueError(f"Pi session JSON constant is not allowed: {value}")


def _decode_records(content: bytes) -> list[dict[str, JsonValue]]:
    if not isinstance(content, bytes):
        raise ValueError("Pi session content must be bytes")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error
    records: list[dict[str, JsonValue]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session lines must be valid JSON") from error
        except ValueError as error:
            raise ValueError("Pi session lines must not contain NaN or Infinity") from error
        if not isinstance(value, dict):
            raise ValueError("Pi session lines must be JSON objects")
        records.append(value)
    if not records:
        raise ValueError("Pi session archive cannot be empty")
    return records


def _session_id(header: dict[str, JsonValue]) -> str:
    if header.get("type") != "session":
        raise ValueError("the first Pi record must be a session header")
    if type(header.get("version")) is not int or header.get("version") != 3:
        raise ValueError("Pi session version must be 3")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session id must be a non-empty string")
    return session_id


def _index_records(records: list[dict[str, JsonValue]]) -> tuple[dict[str, dict[str, JsonValue]], tuple[str, ...]]:
    by_id: dict[str, dict[str, JsonValue]] = {}
    order: list[str] = []
    for record in records:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Pi records must have a non-empty string id")
        if event_id in by_id:
            raise ValueError("Pi record ids must be unique")
        parent_id = record.get("parentId")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in by_id):
            raise ValueError("Pi record parents must reference an earlier record")
        by_id[event_id] = record
        order.append(event_id)
    return by_id, tuple(order)


def _selected_chain(archive: _PiArchive, leaf_id: str | None) -> tuple[dict[str, JsonValue], ...]:
    if leaf_id is None:
        if not archive.order:
            raise ValueError("Pi session has no selectable leaf")
        leaf = archive.order[-1]
    else:
        if not isinstance(leaf_id, str) or leaf_id not in archive.by_id:
            raise ValueError("Pi session leaf was not found")
        leaf = leaf_id
    chain: list[dict[str, JsonValue]] = []
    visited: set[str] = set()
    current: str | None = leaf
    while current is not None:
        if current in visited:
            raise ValueError("Pi session graph contains a cycle")
        visited.add(current)
        record = archive.by_id[current]
        chain.append(record)
        parent_id = record.get("parentId")
        current = parent_id if isinstance(parent_id, str) else None
    chain.reverse()
    return tuple(chain)


def _event_from_record(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    record_type = record.get("type")
    parent_id = record.get("parentId")
    if record_type == "message":
        return _message_event(session_id, record, parent_id)
    if record_type in {"compaction", "branch_summary"}:
        return _summary_event(session_id, record, str(record_type), parent_id)
    if record_type == "custom_message":
        text = _text_blocks(record.get("content"))
        if not text:
            return None
        return _make_event(session_id, record, "custom", f"custom: {text}", parent_id)
    return None


def _message_event(
    session_id: str,
    record: dict[str, JsonValue],
    parent_id: JsonValue,
) -> PiEventInput | None:
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or role == "system":
        return None
    if role == "toolResult":
        text = _text_blocks(message.get("content"))
        body = (
            f"[tool_result {_string_value(message.get('toolName'))} {_string_value(message.get('toolCallId'))}] {text}"
        )
        return _make_event(session_id, record, role, f"{role}: {body}", parent_id)
    if role == "bashExecution":
        command = _string_value(message.get("command"))
        output = message.get("output")
        body_text = output if isinstance(output, str) else _text_blocks(output)
        return _make_event(session_id, record, role, f"{role}: $ {command}\n{body_text}", parent_id)
    parts = _message_parts(message.get("content"))
    if not parts:
        return None
    body = "\n".join(parts)
    return _make_event(session_id, record, role, f"{role}: {body}", parent_id)


def _summary_event(
    session_id: str,
    record: dict[str, JsonValue],
    record_type: str,
    parent_id: JsonValue,
) -> PiEventInput | None:
    summary = record.get("summary")
    text = summary if isinstance(summary, str) else _text_blocks(summary)
    if not text:
        return None
    return _make_event(session_id, record, record_type, f"{record_type}: {text}", parent_id)


def _make_event(
    session_id: str,
    record: dict[str, JsonValue],
    role: str,
    content: str,
    parent_id: JsonValue,
) -> PiEventInput:
    event_id = record.get("id")
    if not isinstance(event_id, str):
        raise ValueError("Pi event id must be a string")
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id if isinstance(parent_id, str) else None,
        event_type=str(record.get("type")),
        role=role,
        content=content,
        record=record,
    )


def _message_parts(content: JsonValue) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif block_type == "toolCall":
            arguments = block.get("arguments")
            parts.append(
                f"[tool_call {_string_value(block.get('name'))} {_string_value(block.get('id'))}] "
                f"{_compact_json(arguments)}"
            )
    return parts


def _text_blocks(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts)


def _compact_json(value: JsonValue) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("Pi tool arguments must be valid JSON") from error


def _string_value(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_checkpoint(value: JsonValue | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) != {"schema", "events"}:
        raise ValueError("Pi connector checkpoint has an unsupported shape")
    if value.get("schema") != PI_SESSION_CHECKPOINT_SCHEMA:
        raise ValueError("Pi connector checkpoint schema is not supported")
    events = value.get("events")
    if not isinstance(events, dict):
        raise ValueError("Pi connector checkpoint events must be an object")
    loaded: dict[str, str] = {}
    for source_id, digest in events.items():
        if not isinstance(source_id, str) or not source_id or not isinstance(digest, str) or not digest:
            raise ValueError("Pi connector checkpoint events must map ids to digests")
        loaded[source_id] = digest
    return loaded


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_SOURCE_VERSION",
    "PI_SESSION_CHECKPOINT_SCHEMA",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
