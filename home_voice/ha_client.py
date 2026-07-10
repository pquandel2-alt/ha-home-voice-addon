"""Schlanker HA-Client (WebSocket + REST) — Muster übernommen aus dem
pq_brain_graph Add-on. Wird von context_cache.py für Live-State und von
server.py für Registry-Lookups genutzt.
"""

import asyncio
import json
import logging
import os

from aiohttp import ClientSession, WSMsgType

_LOG = logging.getLogger("home_voice.ha_client")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_WS = "ws://supervisor/core/websocket"
CORE_API = "http://supervisor/core/api"


class HAClient:
    """Persistente WebSocket-Verbindung zu HA Core, plus REST-Helfer."""

    def __init__(self, session: ClientSession):
        self.session = session
        self.ws = None
        self._id = 0
        self._futures = {}
        self._event_cbs = {}
        self._loop = None
        self.config = {}

    async def connect(self):
        self._loop = asyncio.get_event_loop()
        self.ws = await self.session.ws_connect(CORE_WS, heartbeat=30, max_msg_size=0)

        msg = await self.ws.receive_json()
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected first message: {msg}")

        await self.ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
        msg = await self.ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Auth failed: {msg}")

        asyncio.create_task(self._reader())
        _LOG.info("Connected and authenticated to HA Core")

        try:
            self.config = await self.cmd({"type": "get_config"})
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("get_config fehlgeschlagen: %s", exc)

    async def _reader(self):
        try:
            async for m in self.ws:
                if m.type != WSMsgType.TEXT:
                    continue
                data = json.loads(m.data)
                t = data.get("type")
                if t == "result":
                    fut = self._futures.pop(data["id"], None)
                    if fut and not fut.done():
                        fut.set_result(data)
                elif t == "event":
                    cb = self._event_cbs.get(data["id"])
                    if cb:
                        cb(data.get("event", {}))
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Reader loop ended: %s", exc)

    async def cmd(self, payload: dict, timeout: float = 10.0):
        self._id += 1
        i = self._id
        payload = {**payload, "id": i}
        fut = self._loop.create_future()
        self._futures[i] = fut
        await self.ws.send_json(payload)
        res = await asyncio.wait_for(fut, timeout=timeout)
        if not res.get("success", False):
            raise RuntimeError(f"Command failed: {payload.get('type')} -> {res}")
        return res["result"]

    async def subscribe(self, event_type: str, cb):
        self._id += 1
        i = self._id
        self._event_cbs[i] = cb
        fut = self._loop.create_future()
        self._futures[i] = fut
        await self.ws.send_json({"id": i, "type": "subscribe_events", "event_type": event_type})
        await fut
        _LOG.info("Subscribed to %s", event_type)

    async def rest_get(self, path: str):
        headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
        try:
            async with self.session.get(f"{CORE_API}{path}", headers=headers,
                                        timeout=10) as r:
                if r.status != 200:
                    return None
                return await r.json()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("REST GET %s failed: %s", path, exc)
            return None

    async def close(self):
        if self.ws and not self.ws.closed:
            await self.ws.close()
