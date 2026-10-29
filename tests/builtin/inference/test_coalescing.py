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

"""Behavior tests for coalescing embeddings and the bounded vector cache."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel

PROFILE = EmbeddingProfile(profile_id="coalescing-tests", model="offline", dimension=1)


class Embedding:
    profile = PROFILE

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(tuple(texts))
        return EmbeddingResult(
            vectors=tuple((float(len(text)),) for text in texts),
            usage=InferenceUsage(requests=1, input_tokens=len(texts), output_tokens=0),
        )


def test_late_arrival_joins_inflight_request_and_cache_off_still_shares() -> None:
    class Gated(Embedding):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            self.started.set()
            await self.release.wait()
            return EmbeddingResult(
                vectors=tuple((float(len(text)),) for text in texts), usage=InferenceUsage(requests=1)
            )

    async def scenario() -> None:
        base = Gated()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=0)
        first = asyncio.create_task(model.embed(("shared",)))
        await base.started.wait()
        late = asyncio.create_task(model.embed(("shared",)))
        await asyncio.sleep(0)
        base.release.set()
        assert (await first).vectors == ((6.0,),)
        assert (await late).vectors == ((6.0,),)
        assert base.calls == [("shared",)]
        assert (await model.embed(("shared",))).vectors == ((6.0,),)
        assert len(base.calls) == 2

    asyncio.run(scenario())


def test_partial_failure_leaves_independent_batch_and_allows_retry() -> None:
    class Selective(Embedding):
        def __init__(self) -> None:
            super().__init__()
            self.failing = True

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            await asyncio.sleep(0)
            if self.failing and texts == ("bad",):
                raise RuntimeError("temporary")
            return EmbeddingResult(vectors=tuple((1.0,) for _ in texts), usage=InferenceUsage(requests=1))

    async def scenario() -> None:
        base = Selective()
        model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)
        bad, good = await asyncio.gather(
            model.embed(("bad",)),
            model.embed(("good",)),
            return_exceptions=True,
        )
        assert isinstance(bad, RuntimeError)
        assert good.vectors == ((1.0,),)  # type: ignore[union-attr]
        base.failing = False
        assert (await model.embed(("bad",))).vectors == ((1.0,),)
        assert base.calls == [("bad",), ("good",), ("bad",)]

    asyncio.run(scenario())


def test_cancelled_waiter_leaves_shared_and_sibling_batches_running() -> None:
    class Gated(Embedding):
        def __init__(self) -> None:
            super().__init__()
            self.both_started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            if len(self.calls) == 2:
                self.both_started.set()
            await self.release.wait()
            return EmbeddingResult(
                vectors=tuple((float(len(text)),) for text in texts), usage=InferenceUsage(requests=1)
            )

    async def scenario() -> None:
        base = Gated()
        model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)
        doomed = asyncio.create_task(model.embed(("one", "two")))
        await base.both_started.wait()
        survivor = asyncio.create_task(model.embed(("one",)))
        await asyncio.sleep(0)
        doomed.cancel()
        with pytest.raises(asyncio.CancelledError):
            await doomed
        base.release.set()
        assert (await survivor).vectors == ((3.0,),)
        assert base.calls == [("one",), ("two",)]

    asyncio.run(scenario())


def test_usage_counts_batches_once_and_preserves_tokens() -> None:
    async def scenario() -> None:
        base = Embedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=0, cache_size=3)
        result = await model.embed(("a", "b", "a", "c"))
        assert result.vectors == ((1.0,), (1.0,), (1.0,), (1.0,))
        assert result.usage == InferenceUsage(requests=2, input_tokens=3, output_tokens=0)
        cached = await model.embed(("a", "a"))
        assert cached.usage == InferenceUsage(requests=0)
        assert base.calls == [("a", "b"), ("c",)]

    asyncio.run(scenario())


def test_lru_refreshes_recency_and_zero_cache_disables_storage() -> None:
    async def scenario() -> None:
        base = Embedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=2)
        await model.embed(("a",))
        await model.embed(("b",))
        await model.embed(("a",))
        await model.embed(("c",))
        await model.embed(("b",))
        assert list(base.calls) == [("a",), ("b",), ("c",), ("b",)]

        uncached = Embedding()
        disabled = CoalescingEmbeddingModel(uncached, coalesce_ms=0, cache_size=0)
        await disabled.embed(("a",))
        await disabled.embed(("a",))
        assert list(uncached.calls) == [("a",), ("a",)]

    asyncio.run(scenario())
