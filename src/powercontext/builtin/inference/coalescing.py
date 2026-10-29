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

"""Cross-call embedding request coalescing with a bounded success cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


class _Batch:
    """One underlying request shared by every caller waiting on its texts."""

    __slots__ = ("future", "texts")

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.future: asyncio.Future[EmbeddingResult] = asyncio.get_running_loop().create_future()


class CoalescingEmbeddingModel:
    """Share in-flight embedding work across calls and cache successful vectors.

    Identical texts inside the coalescing window, or while an underlying request
    is still running, are sent to the model once. Results are returned in each
    caller's original order, including duplicate positions.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms")
        if cache_size < 0:
            raise ValueError("cache_size")
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._open_batches: list[_Batch] = []
        self._claims: dict[str, _Batch] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._pump_task: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one ordered vector per text, sharing work across concurrent calls."""

        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique_texts = tuple(dict.fromkeys(texts))
        vectors, missing = self._cached_vectors(unique_texts)
        batches = self._waiting_batches(missing)
        if not batches:
            return EmbeddingResult(
                vectors=tuple(vectors[text] for text in texts),
                usage=InferenceUsage(requests=0),
            )

        outcomes = await asyncio.gather(
            *(asyncio.shield(batch.future) for batch in batches),
            return_exceptions=True,
        )
        results: list[EmbeddingResult] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
            results.append(outcome)
        usage = _combined_usage(result.usage for result in results)
        for batch, result in zip(batches, results, strict=True):
            for text, vector in zip(batch.texts, result.vectors, strict=True):
                vectors[text] = vector
        return EmbeddingResult(vectors=tuple(vectors[text] for text in texts), usage=usage)

    def _cached_vectors(self, unique_texts: tuple[str, ...]) -> tuple[dict[str, EmbeddingVector], list[str]]:
        vectors: dict[str, EmbeddingVector] = {}
        missing: list[str] = []
        for text in unique_texts:
            cached = self._cache_lookup(text)
            if cached is None:
                missing.append(text)
            else:
                vectors[text] = cached
        return vectors, missing

    def _waiting_batches(self, missing: list[str]) -> list[_Batch]:
        batches: list[_Batch] = []
        for text in missing:
            batch = self._claims.get(text)
            if batch is None:
                batch = self._claim(text)
            if all(batch is not pending for pending in batches):
                batches.append(batch)
        return batches

    def _claim(self, text: str) -> _Batch:
        """Attach one text to the newest batch that still has room, or open a batch."""

        if self._open_batches and len(self._open_batches[-1].texts) < self._max_batch_size:
            batch = self._open_batches[-1]
        else:
            batch = _Batch()
            self._open_batches.append(batch)
        batch.texts.append(text)
        self._claims[text] = batch
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._dispatch())
        return batch

    async def _dispatch(self) -> None:
        """Wait out the coalescing window, then run every open batch independently."""

        await asyncio.sleep(self._coalesce_seconds)
        batches = self._open_batches
        self._open_batches = []
        for batch in batches:
            task = asyncio.create_task(self._execute(batch))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _execute(self, batch: _Batch) -> None:
        """Run one batch, cache only valid success, and release its text claims."""

        try:
            result = await self._model.embed(tuple(batch.texts))
            if len(result.vectors) != len(batch.texts):
                self._fail(batch, ValueError("vector-count"))
            else:
                for text, vector in zip(batch.texts, result.vectors, strict=True):
                    self._cache_store(text, vector)
                if not batch.future.done():
                    batch.future.set_result(result)
        except (Exception, asyncio.CancelledError) as error:
            self._fail(batch, error)
        finally:
            for text in batch.texts:
                if self._claims.get(text) is batch:
                    del self._claims[text]

    @staticmethod
    def _fail(batch: _Batch, error: BaseException) -> None:
        if not batch.future.done():
            batch.future.set_exception(error)

    def _cache_lookup(self, text: str) -> EmbeddingVector | None:
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_store(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _combined_usage(usages: Iterable[InferenceUsage]) -> InferenceUsage:
    """Sum the distinct underlying batches one caller waited on."""

    requests = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    for usage in usages:
        requests += usage.requests
        input_tokens = _add_optional(input_tokens, usage.input_tokens)
        output_tokens = _add_optional(output_tokens, usage.output_tokens)
    return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)


def _add_optional(total: int | None, value: int | None) -> int | None:
    if value is None:
        return total
    return value if total is None else total + value


__all__ = ["CoalescingEmbeddingModel"]
