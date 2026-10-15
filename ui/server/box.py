"""Read-only view of the box: manifest and health (spec 2.6). Never writes."""

from __future__ import annotations

from typing import Any

import httpx


async def _get_json(base_url: str, path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(f"{base_url}{path}")
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        data = response.json()
        return (data if isinstance(data, dict) else None), None
    except (httpx.HTTPError, ValueError) as exc:
        return None, type(exc).__name__


async def box_status(base_url: str | None, local_digest: str) -> dict[str, Any]:
    if not base_url:
        return {"configured": False}
    manifest, manifest_error = await _get_json(base_url, "/aiab/manifest")
    health, health_error = await _get_json(base_url, "/aiab/health")
    box_plan = (manifest or {}).get("plan_digest")
    return {
        "configured": True,
        "url": base_url,
        "manifest": manifest,
        "manifest_error": manifest_error,
        "health": health,
        "health_error": health_error,
        "image_digest": (manifest or {}).get("image_digest"),
        "plan_digest": box_plan,
        "plan_matches": (box_plan == local_digest) if box_plan else None,
    }
