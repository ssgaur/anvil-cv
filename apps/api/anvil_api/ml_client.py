"""Thin async client to the Anvil ML service.

Stateless to play well with FastAPI BackgroundTasks (each background task
runs in a fresh asyncio loop, so we can't share an AsyncClient).
"""
from __future__ import annotations

from typing import Any

import httpx

from anvil_api.config import settings


class MLClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base = (base_url or settings.ml_service_url).rstrip("/")

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base, timeout=120.0) as c:
            r = await c.get("/health")
            r.raise_for_status()
            return r.json()

    async def embed(self, image_path: str, embedding_path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base, timeout=300.0) as c:
            r = await c.post(
                "/embed",
                json={"image_path": image_path, "embedding_path": embedding_path},
            )
            r.raise_for_status()
            return r.json()

    async def predict(
        self,
        image_path: str,
        embedding_path: str | None,
        points: list[list[float]] | None = None,
        point_labels: list[int] | None = None,
        box: list[float] | None = None,
        adapter_path: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base, timeout=120.0) as c:
            r = await c.post(
                "/predict",
                json={
                    "image_path": image_path,
                    "embedding_path": embedding_path,
                    "points": points,
                    "point_labels": point_labels,
                    "box": box,
                    "adapter_path": adapter_path,
                },
            )
            r.raise_for_status()
            return r.json()

    async def train(
        self,
        items: list[dict[str, Any]],
        adapter_path: str,
        epochs: int = 20,
        lr: float = 1e-4,
        rank: int = 8,
        alpha: int = 16,
        eval_split: float = 0.2,
        device: str = "cpu",
    ) -> dict[str, Any]:
        # No timeout — training can take many minutes.
        async with httpx.AsyncClient(base_url=self.base, timeout=None) as c:
            r = await c.post(
                "/train",
                json={
                    "items": items,
                    "adapter_path": adapter_path,
                    "epochs": epochs,
                    "lr": lr,
                    "rank": rank,
                    "alpha": alpha,
                    "eval_split": eval_split,
                    "device": device,
                },
            )
            r.raise_for_status()
            return r.json()


    async def score(
        self,
        image_path: str,
        embedding_path: str | None,
        adapter_path: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base, timeout=120.0) as c:
            r = await c.post(
                "/score",
                json={
                    "image_path": image_path,
                    "embedding_path": embedding_path,
                    "adapter_path": adapter_path,
                },
            )
            r.raise_for_status()
            return r.json()


ml_client = MLClient()
