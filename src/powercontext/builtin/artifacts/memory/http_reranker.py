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

"""HTTP cross-encoder reranking over one coarse Memory candidate pool."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score every candidate through a batched HTTP endpoint and select globally.

    The endpoint receives one POST per batch. ``rank`` is always the candidate's
    one-based position in the complete input pool, so batches never renumber
    candidates. Scores from all batches participate in one global selection.
    """

    policy_id: str

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if not policy_id:
            raise ValueError("policy_id must be non-empty")
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return original global ranks ordered by descending service score."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            batch_ranks = range(start + 1, start + 1 + len(batch))
            expected_ranks = frozenset(batch_ranks)
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text} for rank, candidate in zip(batch_ranks, batch, strict=True)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            for rank, score in _parse_scores(response, expected_ranks):
                scores[rank] = score
            requests += 1
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _ in ordered[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, expected_ranks: frozenset[int]) -> list[tuple[int, float]]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("reranker response must be valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain exactly the scores field")
    raw_scores = payload["scores"]
    if not isinstance(raw_scores, list):
        raise ValueError("reranker scores must be a list")
    parsed = [_parse_score_item(item, expected_ranks) for item in raw_scores]
    observed_ranks = {rank for rank, _ in parsed}
    if len(parsed) != len(expected_ranks) or observed_ranks != expected_ranks:
        raise ValueError("reranker scores must cover every batch rank exactly once")
    return parsed


def _parse_score_item(item: object, expected_ranks: frozenset[int]) -> tuple[int, float]:
    if not isinstance(item, dict) or set(item) != {"rank", "score"}:
        raise ValueError("each reranker score must contain exactly rank and score")
    rank = item["rank"]
    raw_score = item["score"]
    if type(rank) is not int or rank not in expected_ranks:
        raise ValueError(f"reranker returned a rank outside the current batch: {rank!r}")
    if type(raw_score) not in (int, float) or isinstance(raw_score, bool):
        raise ValueError("reranker score must be a finite number")
    try:
        score = float(raw_score)
    except (OverflowError, ValueError) as exc:
        raise ValueError("reranker score must be a finite number") from exc
    if not math.isfinite(score):
        raise ValueError("reranker score must be a finite number")
    return rank, score


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")
    if type(limit) is not int or limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")


__all__ = ["HTTPMemoryReranker"]
