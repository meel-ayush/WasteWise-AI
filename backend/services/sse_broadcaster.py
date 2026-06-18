import asyncio
from collections import defaultdict
from typing import Set

_queues: dict[str, Set[asyncio.Queue]] = defaultdict(set)

def publish_refresh(restaurant_id: str, message: str = "refresh"):
    if restaurant_id in _queues:
        for q in list(_queues[restaurant_id]):
            try:
                q.put_nowait(message)
            except Exception:
                pass

async def sse_generator(restaurant_id: str):
    q = asyncio.Queue()
    _queues[restaurant_id].add(q)
    try:
        yield "data: ping\n\n"
        while True:
            msg = await q.get()
            yield f"data: {msg}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        _queues[restaurant_id].discard(q)
        if not _queues[restaurant_id]:
            del _queues[restaurant_id]
