"""Stufe-1-Gedächtnis: Ambient-Kontext.

Hält einen ständig aktuellen, RAM-gecachten Textblock (Gerätenamen,
Anwesenheit, Uhrzeit/Wochentag, Wetter, letzte interessante Events, To-Dos),
den server.py jeder LLM-Anfrage voranstellen kann — ohne Latenz-Kosten zur
Anfragezeit, weil alles im Hintergrund vorgewärmt wird (siehe Plan:
„Latenz-Architektur").
"""

import asyncio
import logging
from collections import deque
from datetime import datetime

from ha_client import HAClient

_LOG = logging.getLogger("home_voice.context_cache")

REFRESH_INTERVAL_S = 60
EVENT_LOG_MAXLEN = 20
# Domains deren state_changed-Events zu häufig/uninteressant fürs Gedächtnis sind.
_NOISY_DOMAINS = {"sensor", "update", "automation", "zone", "sun"}


class AmbientContext:
    """Stufe-1: alle 60s vorgewärmter Kontext + live nachgeführtes Event-Log."""

    def __init__(self, ha: HAClient):
        self.ha = ha
        self.persons = []
        self.weather = None
        self.todos = []
        self.event_log = deque(maxlen=EVENT_LOG_MAXLEN)
        self._task = None

    async def start(self):
        await self.ha.subscribe("state_changed", self._on_state_changed)
        await self._refresh()
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    def _on_state_changed(self, event):
        data = event.get("data", {})
        entity_id = data.get("entity_id", "")
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if domain in _NOISY_DOMAINS:
            return
        new_state = data.get("new_state") or {}
        state = new_state.get("state")
        name = new_state.get("attributes", {}).get("friendly_name", entity_id)
        if state is None:
            return
        self.event_log.append(f"{name} -> {state}")

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(REFRESH_INTERVAL_S)
            try:
                await self._refresh()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("Ambient-Refresh fehlgeschlagen: %s", exc)

    async def _refresh(self):
        states = await self.ha.rest_get("/states")
        if states is None:
            return

        self.persons = [
            {
                "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                "state": s["state"],
            }
            for s in states
            if s["entity_id"].startswith("person.")
        ]

        weather_states = [s for s in states if s["entity_id"].startswith("weather.")]
        if weather_states:
            w = weather_states[0]
            self.weather = {
                "state": w["state"],
                "temperature": w.get("attributes", {}).get("temperature"),
            }

        try:
            result = await self.ha.cmd({"type": "todo/item/list",
                                        "entity_id": "todo.aufgaben"}, timeout=5)
            self.todos = [i["summary"] for i in result.get("items", [])
                          if i.get("status") == "needs_action"][:5]
        except Exception:  # noqa: BLE001 — To-Do-Liste evtl. nicht vorhanden
            self.todos = []

    def as_prompt_text(self):
        """Kompakter Text-Block fürs System-Prompt (Stufe 1, ~0ms Kosten)."""
        now = datetime.now()
        lines = [f"Aktuell: {now.strftime('%A, %d.%m.%Y %H:%M')}"]

        if self.persons:
            home = [p["name"] for p in self.persons if p["state"] == "home"]
            away = [p["name"] for p in self.persons if p["state"] != "home"]
            if home:
                lines.append(f"Zuhause: {', '.join(home)}")
            if away:
                lines.append(f"Unterwegs: {', '.join(away)}")

        if self.weather:
            t = self.weather.get("temperature")
            lines.append(f"Wetter: {self.weather['state']}"
                         + (f", {t}°C" if t is not None else ""))

        if self.todos:
            lines.append(f"Offene To-Dos: {', '.join(self.todos)}")

        if self.event_log:
            lines.append("Zuletzt geändert: " + "; ".join(list(self.event_log)[-5:]))

        return "\n".join(lines)

    def as_dict(self):
        """Strukturierte Sicht fürs Panel (Memory-Tab)."""
        return {
            "persons": self.persons,
            "weather": self.weather,
            "todos": self.todos,
            "recent_events": list(self.event_log),
            "prompt_preview": self.as_prompt_text(),
        }
