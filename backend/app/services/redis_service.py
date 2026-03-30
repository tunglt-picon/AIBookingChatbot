"""
Redis service – used for:
  1. Caching session metadata (rate-limiting, quick reads)
  2. Storing image base64 temporarily while the graph processes it
     (avoids re-reading from disk on every node invocation)
"""

import json
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import settings

_redis_client: Optional[aioredis.Redis] = None


def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def set_json(key: str, value: Any, ttl_seconds: int = 3600) -> None:
    client = get_redis()
    await client.setex(key, ttl_seconds, json.dumps(value))


async def get_json(key: str) -> Optional[Any]:
    client = get_redis()
    data = await client.get(key)
    if data is None:
        return None
    return json.loads(data)


async def delete_key(key: str) -> None:
    client = get_redis()
    await client.delete(key)


# ── Image cache helpers ────────────────────────────
IMAGE_TTL = 60 * 30  # 30 minutes


def image_cache_key(session_id: int) -> str:
    return f"img_cache:session:{session_id}"


async def cache_image(session_id: int, base64_data: str, mime_type: str) -> None:
    await set_json(
        image_cache_key(session_id),
        {"base64": base64_data, "mime_type": mime_type},
        ttl_seconds=IMAGE_TTL,
    )


async def get_cached_image(session_id: int) -> Optional[dict]:
    return await get_json(image_cache_key(session_id))


async def clear_cached_image(session_id: int) -> None:
    await delete_key(image_cache_key(session_id))


# ── Rate limiting helper ───────────────────────────
async def check_rate_limit(user_id: int, window_seconds: int = 60, max_requests: int = 30) -> bool:
    """Returns True if the request is allowed (not rate-limited)."""
    client = get_redis()
    key = f"rate_limit:{user_id}"
    count = await client.incr(key)
    if count == 1:
        await client.expire(key, window_seconds)
    return count <= max_requests
