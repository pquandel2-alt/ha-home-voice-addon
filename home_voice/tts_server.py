"""Kokoro Streaming-TTS über das Wyoming-Protokoll.

Home Assistant erwartet TTS-Engines im Wyoming-Protokoll (siehe HA-eigene
`wyoming-piper` als Referenzimplementierung). Wir synthetisieren Satz für
Satz (statt des ganzen Texts auf einmal) und senden jeden Satz sofort als
AudioChunk raus, sobald er fertig ist — das ist der "Streaming"-Effekt aus
dem Plan: die Ausgabe beginnt nach dem ersten Satz, nicht erst nach dem
letzten.

Sprachhinweis: Kokoro-82M v1.0 unterstützt offiziell nur Englisch, Japanisch,
Mandarin, Spanisch, Französisch, Hindi, Italienisch und brasilianisches
Portugiesisch — **kein Deutsch**. Es gibt eine Community-Fine-Tune für
Deutsch (Godelaune/Kokoro-82M-ONNX-German-Martin), deren Stimmen-Datei aber
ein anderes (.npz statt .bin) Format hat und eine eigene Lade-Logik bräuchte,
die wir nicht ungeprüft übernehmen wollen. Default bleibt daher das offizielle
englische Modell; deutsche Sprachausgabe ist eine bekannte Lücke (siehe DOCS.md).
"""

import asyncio
import logging
import os
import re

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize

_LOG = logging.getLogger("home_voice.tts_server")

WYOMING_PORT = 10200
TTS_CACHE = "/data/tts"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
MODEL_PATH = os.path.join(TTS_CACHE, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(TTS_CACHE, "voices-v1.0.bin")

SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2  # int16
CHANNELS = 1

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str):
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    return parts or [text.strip()]


class KokoroEngine:
    """Lazy-geladenes Kokoro-Modell + Download-Verwaltung."""

    def __init__(self):
        self._kokoro = None
        self.ready = False
        self.default_voice = "af_heart"

    async def ensure_downloaded(self, session):
        os.makedirs(TTS_CACHE, exist_ok=True)
        for url, path in ((MODEL_URL, MODEL_PATH), (VOICES_URL, VOICES_PATH)):
            if os.path.exists(path):
                continue
            _LOG.info("Lade Kokoro-Datei: %s", url)
            async with session.get(url, timeout=None) as r:
                r.raise_for_status()
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    async for chunk in r.content.iter_chunked(1 << 20):
                        f.write(chunk)
                os.rename(tmp, path)
            _LOG.info("Kokoro-Datei bereit: %s", path)

    def load(self):
        from kokoro_onnx import Kokoro
        self._kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
        self.ready = True
        _LOG.info("Kokoro-Modell geladen")

    def synthesize_sentence(self, text: str, voice: str, speed: float = 1.0):
        """Blockierender Kokoro-Call — vom Aufrufer in einen Thread auslagern."""
        samples, sample_rate = self._kokoro.create(
            text, voice=voice or self.default_voice, speed=speed, lang="en-us")
        import numpy as np
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        return pcm16, sample_rate


_WYOMING_INFO = Info(
    tts=[
        TtsProgram(
            name="kokoro",
            description="Kokoro (lokal, Streaming, Satz-für-Satz)",
            attribution=Attribution(
                name="thewh1teagle/kokoro-onnx",
                url="https://github.com/thewh1teagle/kokoro-onnx",
            ),
            installed=True,
            version="1.0",
            voices=[
                TtsVoice(
                    name="af_heart",
                    description="Englisch (US), weiblich — Kokoro-Default",
                    attribution=Attribution(
                        name="thewh1teagle/kokoro-onnx",
                        url="https://github.com/thewh1teagle/kokoro-onnx",
                    ),
                    installed=True,
                    version="1.0",
                    languages=["en-us"],
                ),
            ],
        )
    ],
)


class KokoroEventHandler(AsyncEventHandler):
    """Ein Handler pro Wyoming-Client-Verbindung (Muster: wyoming-piper)."""

    def __init__(self, engine: KokoroEngine, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.engine = engine

    async def handle_event(self, event: Event) -> bool:
        if not self.engine.ready:
            _LOG.warning("Synthesize angefragt, aber Kokoro noch nicht geladen")
            return True

        if event.type == "describe":
            await self.write_event(_WYOMING_INFO.event())
            return True

        if Synthesize.is_type(event.type):
            synth = Synthesize.from_event(event)
            await self._synthesize(synth.text, synth.voice.name if synth.voice else None)
            return True

        return True

    async def _synthesize(self, text: str, voice_name: str | None):
        sentences = _split_sentences(text)
        await self.write_event(
            AudioStart(rate=SAMPLE_RATE, width=SAMPLE_WIDTH, channels=CHANNELS).event())
        for sentence in sentences:
            pcm16, rate = await asyncio.to_thread(
                self.engine.synthesize_sentence, sentence, voice_name)
            await self.write_event(
                AudioChunk(audio=pcm16, rate=rate, width=SAMPLE_WIDTH,
                          channels=CHANNELS).event())
        await self.write_event(AudioStop().event())


async def run_wyoming_server(engine: KokoroEngine):
    server = AsyncServer.from_uri(f"tcp://0.0.0.0:{WYOMING_PORT}")
    _LOG.info("Wyoming-TTS-Server (Kokoro) auf Port %d", WYOMING_PORT)

    async def handler_factory(*args, **kwargs):
        return KokoroEventHandler(engine, *args, **kwargs)

    await server.run(handler_factory)
