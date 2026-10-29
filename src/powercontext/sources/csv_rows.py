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

"""Row-level CSV Sources with stable identities and exact text projections."""

from __future__ import annotations

import csv
import io
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document to validate and split into rows."""

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _validate_document_id(value)
        return value

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        _validate_primary_key(value)
        return value


class CSVRowInput(BaseModel):
    """One validated CSV row in original header order."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self) -> CSVRowInput:
        _validate_document_id(self.document_id)
        _validate_primary_key(self.primary_key)
        if not self.primary_key_value.strip():
            raise ValueError("primary_key_value must not be blank")
        if not self.columns or len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same non-zero width")
        if any(not column.strip() for column in self.columns):
            raise ValueError("columns must not contain blank names")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must be unique")
        return self


class CSVRowSource(Source):
    """Captured row Source carrying the complete original record."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> CSVRowSource:
        expected = csv_row_source_id(self.document_id, self.primary_key_value)
        if self.source_id != expected or self.name != expected:
            raise ValueError("CSV row source name and source_id must match the row identity")
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same width")
        return self


class CSVRowSourceAdapter:
    """Resolve and read row-level CSV Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one CSV row as canonical text evidence without changing its values."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the stable, delimiter-safe identity for one CSV row."""

    _validate_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must not be blank")
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and fully validate one CSV document before returning any row."""

    _validate_document_id(value.document_id)
    _validate_primary_key(value.primary_key)
    if not isinstance(value.content, bytes):
        raise ValueError("CSV content must be bytes")
    try:
        text = value.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("CSV content is not valid UTF-8 CSV") from error
    header, primary_key_index = _parse_header(rows, value.primary_key)
    return _parse_rows(rows, header, primary_key_index, value)


def _parse_header(rows: list[list[str]], primary_key: str) -> tuple[tuple[str, ...], int]:
    if not rows or not rows[0]:
        raise ValueError("CSV header must not be empty")
    header = rows[0]
    if any(not column.strip() for column in header):
        raise ValueError("CSV header fields must not be blank")
    if len(set(header)) != len(header):
        raise ValueError("CSV header fields must be unique")
    try:
        primary_key_index = header.index(primary_key)
    except ValueError as error:
        raise ValueError("primary_key must exactly match a CSV header field") from error
    return tuple(header), primary_key_index


def _parse_rows(
    rows: list[list[str]],
    header: tuple[str, ...],
    primary_key_index: int,
    value: CSVDocumentInput,
) -> tuple[CSVRowInput, ...]:
    parsed: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for row in rows[1:]:
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError("CSV data rows must have the same width as the header")
        primary_key_value = row[primary_key_index]
        if not primary_key_value.strip():
            raise ValueError("CSV primary key values must not be blank")
        if primary_key_value in seen_keys:
            raise ValueError("CSV primary key values must be unique within a document")
        seen_keys.add(primary_key_value)
        parsed.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=primary_key_value,
                columns=header,
                values=tuple(row),
            )
        )
    return tuple(parsed)


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty trimmed string")


def _validate_primary_key(primary_key: str) -> None:
    if not isinstance(primary_key, str) or not primary_key.strip():
        raise ValueError("primary_key must be a non-empty string")


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
