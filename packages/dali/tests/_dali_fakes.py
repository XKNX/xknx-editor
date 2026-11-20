"""A fake Programmer for testing the DALI protocol without a bus.

Reads slice stored tables by element size and request bounds. Queued responses override
stored tables for polling and malformed-response tests. Writes are recorded.
"""

from __future__ import annotations

from xknxeditor.dali.properties import ELEMENT_SIZE


class FakeProgrammer:
    def __init__(self) -> None:
        self.max_payload_bytes = 250
        self.writes: list[dict[str, object]] = []
        self._reads: dict[int, bytes] = {}
        self._queues: dict[int, list[bytes]] = {}
        self.element_sizes: dict[int, int] = dict(ELEMENT_SIZE)
        self.element_sizes[213] = 1
        self.reads: list[tuple[int, int, int]] = []

    def property_chunk_size(self, element_size: int) -> int:
        return min(15, self.max_payload_bytes // element_size)

    def set_read(self, property_id: int, data: bytes) -> None:
        self._reads[property_id] = data

    def queue_read(self, property_id: int, *chunks: bytes) -> None:
        bucket = self._queues.setdefault(property_id, [])
        assert isinstance(bucket, list)
        bucket.extend(chunks)

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes:
        self.reads.append((property_id, start_index, count))
        queued = self._queues.get(property_id)
        if queued:
            return queued.pop(0)
        r = self._reads.get(property_id)
        if isinstance(r, (bytes, bytearray)):
            size = self.element_sizes.get(property_id)
            if start_index > 0 and size is not None and len(r) % size == 0:
                return bytes(
                    r[(start_index - 1) * size : (start_index - 1 + count) * size]
                )
            return bytes(r)
        return b""

    async def write_property(
        self,
        object_index: int,
        property_id: int,
        data: bytes,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes:
        self.writes.append(
            {
                "object_index": object_index,
                "property_id": property_id,
                "data": bytes(data),
                "count": count,
                "start_index": start_index,
            }
        )
        return bytes(data)

    def writes_for(self, property_id: int) -> list[dict[str, object]]:
        return [w for w in self.writes if w["property_id"] == property_id]
