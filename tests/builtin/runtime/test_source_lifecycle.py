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

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.service import MemoryService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


class _InjectedMemoryFailure(RuntimeError):
    pass


def _config(tmp_path: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}"))


def _event(source: str, sequence: int, available: bool, *, revision: int = 3, reason: str = "controlled"):
    return SourceLifecycleEvent(
        node_id=source,
        revision=revision,
        sequence=sequence,
        available=available,
        reason=reason,
    )


async def _scope(runtime, key: str) -> str:
    created = await runtime.scopes.create(ScopeDraft(title="Lifecycle", summary="Evidence", idempotency_key=key))
    return created.scope_id


async def _remember(runtime, scope: str, text: str):
    return await runtime.memory.for_scope(scope).remember(
        RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=text),))
    )


async def _capture(runtime, scope: str, source_id: str):
    receipt = await runtime.sources.for_scope(scope).capture(
        CaptureSource(source_id=source_id, content=f"Origin {source_id}", metadata={})
    )
    return receipt.source_ref


async def _states(runtime, scope: str) -> dict[str, str]:
    page = await runtime.memory.for_scope(scope).list(include_inactive=True)
    return {record.entry.text.split()[0]: record.state for record in page.entries}


def test_scope_isolation_and_registration_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path)
        async with open_builtin_runtime(config) as runtime:
            first = await _scope(runtime, "first")
            second = await _scope(runtime, "second")
            citations = {}
            for scope in (first, second):
                lifecycle = runtime.lifecycle.for_scope(scope)
                reference = await _capture(runtime, scope, "root")
                await lifecycle.register_source("root", reference, revision=3)
                for node in ("kept", "dropped"):
                    created = await _remember(runtime, scope, f"{node} evidence")
                    await lifecycle.register_memory(
                        node,
                        created.entry.citation,
                        parents=("root",),
                        policy="all",
                    )
                    citations[(scope, node)] = created.entry.citation

            await runtime.lifecycle.for_scope(first).apply(_event("root", 1, False))

            assert await _states(runtime, first) == {"kept": "inactive", "dropped": "inactive"}
            assert await _states(runtime, second) == {"kept": "active", "dropped": "active"}
            with pytest.raises(SourceLifecycleError):
                await runtime.lifecycle.for_scope(first).resolve("kept", purpose="generation")
            assert (
                await runtime.lifecycle.for_scope(first).resolve("kept", purpose="audit") == citations[(first, "kept")]
            )
            assert (
                await runtime.lifecycle.for_scope(second).resolve("kept", purpose="generation")
                == citations[(second, "kept")]
            )

            lifecycle = runtime.lifecycle.for_scope(second)
            reference = await _capture(runtime, second, "root")
            await lifecycle.register_source("root", reference, revision=3)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_source("root", reference, revision=4)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory(
                    "kept",
                    citations[(second, "dropped")],
                    parents=("root",),
                    policy="all",
                )

    asyncio.run(scenario())


def test_partial_memory_failure_retries_same_event_without_extra_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope = await _scope(runtime, "retry")
            lifecycle = runtime.lifecycle.for_scope(scope)
            reference = await _capture(runtime, scope, "root")
            await lifecycle.register_source("root", reference, revision=3)
            for node in ("alpha", "beta"):
                created = await _remember(runtime, scope, f"{node} evidence")
                await lifecycle.register_memory(node, created.entry.citation, parents=("root",), policy="all")

            original = MemoryService.forget
            calls = 0

            async def flaky(self, memory, *, entries, reason=None):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise _InjectedMemoryFailure
                return await original(self, memory, entries=entries, reason=reason)

            monkeypatch.setattr(MemoryService, "forget", flaky)
            with pytest.raises(RuntimeError):
                await lifecycle.apply(_event("root", 1, False))
            monkeypatch.setattr(MemoryService, "forget", original)
            assert calls == 2
            assert (await _states(runtime, scope)) == {"alpha": "inactive", "beta": "active"}

            active = await lifecycle.apply(_event("root", 1, False))
            assert active == []
            assert (await _states(runtime, scope)) == {"alpha": "inactive", "beta": "inactive"}
            page = await runtime.memory.for_scope(scope).search(SearchMemoryRequest(query="alpha", mode="fts"))
            assert page.hits == ()

            settled = (await runtime.memory.for_scope(scope).list()).memory_ref
            await lifecycle.apply(_event("root", 1, False))
            assert (await runtime.memory.for_scope(scope).list()).memory_ref == settled

            await lifecycle.apply(_event("root", 2, True))
            assert (await _states(runtime, scope)) == {"alpha": "active", "beta": "active"}

    asyncio.run(scenario())


def test_any_policy_survives_one_withdrawal_and_all_policy_recovers(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope = await _scope(runtime, "policies")
            lifecycle = runtime.lifecycle.for_scope(scope)
            for source in ("left", "right"):
                reference = await _capture(runtime, scope, source)
                await lifecycle.register_source(source, reference, revision=3)
            for node, policy in (("either", "any"), ("both", "all")):
                created = await _remember(runtime, scope, f"{node} evidence")
                await lifecycle.register_memory(
                    node,
                    created.entry.citation,
                    parents=("left", "right"),
                    policy=policy,
                )
            created = await _remember(runtime, scope, "downstream evidence")
            await lifecycle.register_memory("downstream", created.entry.citation, parents=("both",), policy="all")

            await lifecycle.apply(_event("left", 1, False))
            assert (await _states(runtime, scope)) == {
                "either": "active",
                "both": "inactive",
                "downstream": "inactive",
            }

            await lifecycle.apply(_event("left", 2, True))
            assert (await _states(runtime, scope)) == {
                "either": "active",
                "both": "active",
                "downstream": "active",
            }

            await lifecycle.apply(_event("right", 1, False))
            assert (await _states(runtime, scope)) == {
                "either": "active",
                "both": "inactive",
                "downstream": "inactive",
            }
            assert await lifecycle.resolve("either", purpose="generation")

    asyncio.run(scenario())


def test_event_sequence_and_resolution_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope = await _scope(runtime, "events")
            lifecycle = runtime.lifecycle.for_scope(scope)
            reference = await _capture(runtime, scope, "root")
            await lifecycle.register_source("root", reference, revision=7)

            assert await lifecycle.apply(_event("root", 5, False, revision=7)) == []
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root", 4, True, revision=7))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root", 5, True, revision=7))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root", 6, True, revision=3))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("missing", 1, True, revision=7))
            await lifecycle.apply(_event("root", 5, False, revision=7))
            assert await lifecycle.apply(_event("root", 6, True, revision=7)) == ["root"]

            created = await _remember(runtime, scope, "loop evidence")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("unknown", purpose="audit")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("root", purpose="other")  # type: ignore[arg-type]
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("loop", created.entry.citation, parents=("loop",), policy="all")

    asyncio.run(scenario())


def test_restart_preserves_graph_and_accepts_new_descendants(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path)
        async with open_builtin_runtime(config) as runtime:
            scope = await _scope(runtime, "restart")
            lifecycle = runtime.lifecycle.for_scope(scope)
            reference = await _capture(runtime, scope, "root")
            await lifecycle.register_source("root", reference, revision=3)
            created = await _remember(runtime, scope, "original evidence")
            await lifecycle.register_memory("original", created.entry.citation, parents=("root",), policy="all")
            await lifecycle.apply(_event("root", 1, False))

        async with open_builtin_runtime(config) as runtime:
            lifecycle = runtime.lifecycle.for_scope(scope)
            assert await lifecycle.resolve("root", purpose="audit") == reference
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("original", purpose="generation")
            created = await _remember(runtime, scope, "late evidence")
            await lifecycle.register_memory("late", created.entry.citation, parents=("root",), policy="all")
            assert (await _states(runtime, scope)) == {"original": "inactive", "late": "inactive"}
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("late", purpose="generation")

            restored = await lifecycle.apply(_event("root", 2, True))
            assert restored == ["late", "original", "root"]
            assert (await _states(runtime, scope)) == {"original": "active", "late": "active"}

    asyncio.run(scenario())


def test_unregistered_memory_is_not_touched_by_withdrawal(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope = await _scope(runtime, "independent")
            lifecycle = runtime.lifecycle.for_scope(scope)
            reference = await _capture(runtime, scope, "root")
            await lifecycle.register_source("root", reference, revision=3)
            created = await _remember(runtime, scope, "bound evidence")
            await lifecycle.register_memory("bound", created.entry.citation, parents=("root",), policy="all")
            await _remember(runtime, scope, "standalone evidence")

            await lifecycle.apply(_event("root", 1, False))
            assert (await _states(runtime, scope)) == {"bound": "inactive", "standalone": "active"}
            page = await runtime.memory.for_scope(scope).search(SearchMemoryRequest(query="standalone", mode="fts"))
            assert [hit.text for hit in page.hits] == ["standalone evidence"]

    asyncio.run(scenario())
