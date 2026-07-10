"""Stufe-2-Gedächtnis: semantischer Brain-Recall + Lern-Loop.

Nur für Wissensfragen aufgerufen (siehe `is_question`), mit knappem Timeout
(`recall_timeout`), damit ein nicht erreichbares/langsames Brain die
Antwortzeit nie stärker als geplant verzögert. Schreibt Korrekturen/neue
Fakten per REST zurück, damit sie beim nächsten Ambient-Refresh sichtbar sind.
"""

import logging
import re

from aiohttp import ClientSession, ClientTimeout

_LOG = logging.getLogger("home_voice.brain_client")

_QUESTION_WORDS = (
    "was", "wer", "wie", "wo", "wann", "warum", "wieso", "weshalb",
    "welche", "welcher", "welches", "kennst du", "weißt du", "erinnerst",
)


def is_question(text: str) -> bool:
    """Heuristik: lohnt sich ein Stufe-2-Recall für diese Eingabe?"""
    t = text.strip().lower()
    if not t:
        return False
    if "?" in t:
        return True
    return any(t.startswith(w) or f" {w} " in t for w in _QUESTION_WORDS)


class BrainClient:
    """Dünner HTTP-Client für Brains REST-API (siehe globale CLAUDE.md)."""

    def __init__(self, session: ClientSession, base_url: str, recall_timeout: float = 1.2):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.recall_timeout = recall_timeout

    async def recall(self, query: str, budget: int = 800):
        """Bounded-Timeout Stufe-2-Recall. Gibt bei Fehler/Timeout '' zurück
        statt die Anfrage zu blockieren — Brain darf die Antwort nie verzögern
        über den gesetzten Timeout hinaus."""
        if not self.base_url:
            return ""
        url = f"{self.base_url}/api/recall"
        try:
            async with self.session.get(
                url, params={"q": query, "budget": str(budget)},
                timeout=ClientTimeout(total=self.recall_timeout),
            ) as r:
                if r.status != 200:
                    return ""
                data = await r.json()
                return data.get("text") or data.get("result") or ""
        except Exception as exc:  # noqa: BLE001
            _LOG.info("Brain-Recall übersprungen (%s)", exc)
            return ""

    async def write_fact(self, label: str, content: str, tags=None):
        """Lern-Loop: Korrektur/neuer Fakt zurück nach Brain. Best-effort —
        Fehler werden geloggt, nie an den Aufrufer weitergereicht."""
        if not self.base_url:
            return False
        url = f"{self.base_url}/api/nodes"
        payload = {"label": label, "type": "note", "content": content,
                   "tags": tags or ["home_voice"]}
        try:
            async with self.session.post(
                url, json=payload, timeout=ClientTimeout(total=5),
            ) as r:
                return r.status in (200, 201)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Brain write_fact fehlgeschlagen: %s", exc)
            return False

    async def health(self):
        if not self.base_url:
            return False
        try:
            async with self.session.get(
                f"{self.base_url}/api/health", timeout=ClientTimeout(total=3),
            ) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False
