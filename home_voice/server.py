"""Home Voice Add-on backend.

Startet einen lokalen llama.cpp-Inferenz-Server (OpenAI-kompatibel) als
Subprozess, einen Wyoming-TTS-Server (Kokoro) und einen aiohttp-Server davor,
der:
  * `/v1/chat/completions`  Kontext-Injektion (Stufe 1 Ambient + Stufe 2
     Brain-Recall) davorschaltet, dann an llama-server weiterreicht,
  * `/v1/*` (sonst)  transparent an llama-server durchreicht,
  * `/api/status` / `/api/memory`  Infos fürs Panel liefert,
  * `/`  das Ingress-Panel ausliefert.

Alle fünf Rollout-Schritte aus dem Plan sind hier zusammengeführt: Inferenz-
Server, Kokoro-TTS (Wyoming), Ambient-Kontext-Cache, Brain-Recall + Lern-Loop.
"""

import asyncio
import json
import logging
import os
import signal

from aiohttp import ClientSession, ClientTimeout, web

from brain_client import BrainClient, is_question
from context_cache import AmbientContext
from ha_client import HAClient
from tts_server import KokoroEngine, run_wyoming_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_LOG = logging.getLogger("home_voice")

PORT = 8099                       # Ingress + LAN
LLAMA_HOST = "127.0.0.1"          # llama-server nur intern
LLAMA_PORT = 8080
LLAMA_BASE = f"http://{LLAMA_HOST}:{LLAMA_PORT}"

# Persistenter Add-on-Speicher — Modelle überleben Neustarts/Updates.
MODEL_CACHE = "/data/models"

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")

# ---------------------------------------------------------------------------
# Curated Modell-Registry (GGUF Q4_K_M, alle Repos 2026-07 verifiziert)
# ---------------------------------------------------------------------------
MODEL_REGISTRY = {
    "phi-4-mini": {
        "label": "Phi-4-mini (3,8B) — ausgewogen (Default)",
        "hf_repo": "bartowski/microsoft_Phi-4-mini-instruct-GGUF",
        "hf_file": "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
    },
    "qwen2.5-3b": {
        "label": "Qwen2.5 3B — schlauer, besseres Tool-Calling",
        "hf_repo": "bartowski/Qwen2.5-3B-Instruct-GGUF",
        "hf_file": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
    },
    "gemma3-1b": {
        "label": "Gemma 3 1B — am schnellsten",
        "hf_repo": "ggml-org/gemma-3-1b-it-GGUF",
        "hf_file": "gemma-3-1b-it-Q4_K_M.gguf",
    },
}
DEFAULT_MODEL = "phi-4-mini"


def _load_options():
    try:
        with open("/data/options.json", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        _LOG.warning("Could not read /data/options.json (%s) — using defaults", exc)
        return {}


def _resolve_model(options):
    key = options.get("model", DEFAULT_MODEL)
    if key not in MODEL_REGISTRY:
        _LOG.warning("Unknown model '%s' — falling back to %s", key, DEFAULT_MODEL)
        key = DEFAULT_MODEL
    return key, MODEL_REGISTRY[key]


_OPTIONS = _load_options()
MODEL_KEY, MODEL = _resolve_model(_OPTIONS)
CONTEXT_SIZE = int(_OPTIONS.get("context_size", 4096))
THREADS = int(_OPTIONS.get("threads", 2))
TEMPERATURE = float(_OPTIONS.get("temperature", 0.7))
BRAIN_ENABLED = bool(_OPTIONS.get("brain_enabled", False))
BRAIN_URL = _OPTIONS.get("brain_url", "") or ""
TTS_ENABLED = bool(_OPTIONS.get("tts_enabled", True))


# ---------------------------------------------------------------------------
# llama-server Subprozess-Verwaltung
# ---------------------------------------------------------------------------
class LlamaServer:
    """Startet und überwacht das llama.cpp `llama-server`-Binary."""

    def __init__(self):
        self.proc = None
        self.ready = False

    def _build_args(self):
        # --hf-repo/--hf-file lädt das GGUF beim ersten Start nach LLAMA_CACHE
        # (via libcurl) und nutzt danach den Cache. --jinja aktiviert das
        # native Chat-Template (nötig für sauberes Tool-Calling).
        return [
            "llama-server",
            "--host", LLAMA_HOST,
            "--port", str(LLAMA_PORT),
            "--hf-repo", MODEL["hf_repo"],
            "--hf-file", MODEL["hf_file"],
            "--ctx-size", str(CONTEXT_SIZE),
            "--threads", str(THREADS),
            "--jinja",
        ]

    async def start(self):
        os.makedirs(MODEL_CACHE, exist_ok=True)
        env = {**os.environ, "LLAMA_CACHE": MODEL_CACHE}
        args = self._build_args()
        _LOG.info("Launching llama-server: %s", " ".join(args))
        _LOG.info("Model '%s' → %s/%s (Download beim ersten Start kann dauern)",
                  MODEL_KEY, MODEL["hf_repo"], MODEL["hf_file"])
        self.proc = await asyncio.create_subprocess_exec(
            *args, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        asyncio.create_task(self._pipe_logs())

    async def _pipe_logs(self):
        assert self.proc and self.proc.stdout
        async for line in self.proc.stdout:
            _LOG.info("[llama] %s", line.decode(errors="replace").rstrip())

    async def wait_healthy(self, session: ClientSession, timeout_s=1800):
        """Pollt /health bis das Modell geladen ist (Download inbegriffen)."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if self.proc and self.proc.returncode is not None:
                _LOG.error("llama-server beendet mit Code %s", self.proc.returncode)
                return False
            try:
                async with session.get(f"{LLAMA_BASE}/health",
                                       timeout=ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        self.ready = True
                        _LOG.info("llama-server ist bereit")
                        return True
            except Exception:  # noqa: BLE001 — Server noch nicht oben / lädt Modell
                pass
            await asyncio.sleep(3)
        _LOG.error("llama-server wurde nicht rechtzeitig gesund")
        return False

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.proc.kill()


# ---------------------------------------------------------------------------
# HTTP Routes
# ---------------------------------------------------------------------------
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


async def handle_index(request):
    return web.FileResponse("/www/index.html")


async def handle_status(request):
    """Panel-Status: gewähltes Modell, Health, verfügbare Modelle."""
    llama: LlamaServer = request.app["llama"]
    ha: HAClient = request.app.get("ha")
    tts: KokoroEngine = request.app.get("tts")
    brain: BrainClient = request.app.get("brain")
    return web.json_response({
        "model_key": MODEL_KEY,
        "model_label": MODEL["label"],
        "context_size": CONTEXT_SIZE,
        "threads": THREADS,
        "ready": llama.ready,
        "ha_connected": bool(ha and ha.ws and not ha.ws.closed),
        "tts_enabled": TTS_ENABLED,
        "tts_ready": bool(tts and tts.ready),
        "brain_enabled": BRAIN_ENABLED,
        "brain_reachable": await brain.health() if brain else False,
        "available_models": [
            {"key": k, "label": v["label"]} for k, v in MODEL_REGISTRY.items()
        ],
    })


async def handle_memory(request):
    """Live-Ansicht des Stufe-1 Ambient-Kontexts fürs Panel (Memory-Tab)."""
    ctx: AmbientContext = request.app.get("context")
    if not ctx:
        return web.json_response({"available": False})
    data = ctx.as_dict()
    data["available"] = True
    data["brain_enabled"] = BRAIN_ENABLED
    return web.json_response(data)


def _build_system_message(ambient_text: str, recall_text: str) -> str:
    parts = ["Du bist ein hilfreicher, lokaler Sprachassistent für Home Assistant. "
              "Antworte kurz und natürlich, wie in einem gesprochenen Gespräch."]
    if ambient_text:
        parts.append("Aktueller Haus-Kontext:\n" + ambient_text)
    if recall_text:
        parts.append("Relevantes Wissen aus dem Gedächtnis:\n" + recall_text)
    return "\n\n".join(parts)


async def _inject_context(request, payload: dict):
    """Stufe 1 (immer, ~0ms, vorgewärmt) + Stufe 2 (nur bei Wissensfragen,
    bounded-timeout) werden als System-Message vor die Konversation gesetzt."""
    ctx: AmbientContext = request.app.get("context")
    brain: BrainClient = request.app.get("brain")

    ambient_text = ctx.as_prompt_text() if ctx else ""

    recall_text = ""
    messages = payload.get("messages", [])
    last_user = next((m["content"] for m in reversed(messages)
                      if m.get("role") == "user"), "")
    if brain and BRAIN_ENABLED and last_user and is_question(last_user):
        recall_text = await brain.recall(last_user)

    system_content = _build_system_message(ambient_text, recall_text)
    new_messages = [{"role": "system", "content": system_content}]
    new_messages += [m for m in messages if m.get("role") != "system"]
    payload["messages"] = new_messages
    return payload


async def handle_chat_completions(request):
    """`/v1/chat/completions` mit vorgeschalteter Kontext-Injektion."""
    session: ClientSession = request.app["session"]
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": {"message": "invalid JSON"}}, status=400)

    payload = await _inject_context(request, payload)
    url = f"{LLAMA_BASE}/v1/chat/completions"

    try:
        upstream = await session.post(
            url, json=payload, timeout=ClientTimeout(total=None),
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error("Proxy zu llama-server fehlgeschlagen: %s", exc)
        return web.json_response({"error": {"message": str(exc)}}, status=502)

    resp = web.StreamResponse(status=upstream.status)
    for k, v in upstream.headers.items():
        if k.lower() not in _HOP_BY_HOP:
            resp.headers[k] = v
    await resp.prepare(request)
    async for chunk in upstream.content.iter_any():
        await resp.write(chunk)
    await resp.write_eof()
    return resp


async def handle_v1_proxy(request):
    """Reicht alle sonstigen /v1/* transparent an llama-server durch."""
    session: ClientSession = request.app["session"]
    tail = request.match_info["tail"]
    url = f"{LLAMA_BASE}/v1/{tail}"
    body = await request.read()
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _HOP_BY_HOP}

    try:
        upstream = await session.request(
            request.method, url, data=body, headers=fwd_headers,
            timeout=ClientTimeout(total=None),
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error("Proxy zu llama-server fehlgeschlagen: %s", exc)
        return web.json_response({"error": {"message": str(exc)}}, status=502)

    resp = web.StreamResponse(status=upstream.status)
    for k, v in upstream.headers.items():
        if k.lower() not in _HOP_BY_HOP:
            resp.headers[k] = v
    await resp.prepare(request)
    async for chunk in upstream.content.iter_any():
        await resp.write(chunk)
    await resp.write_eof()
    return resp


async def handle_brain_test(request):
    """Kleiner Diagnose-Endpunkt fürs Panel: schickt eine Test-Query an Brain."""
    brain: BrainClient = request.app.get("brain")
    if not brain or not BRAIN_ENABLED:
        return web.json_response({"ok": False, "reason": "brain_disabled"})
    q = request.query.get("q", "Was weißt du über dieses Zuhause?")
    text = await brain.recall(q)
    return web.json_response({"ok": bool(text), "query": q, "result": text})


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def async_main():
    app = web.Application()
    session = ClientSession()
    app["session"] = session

    llama = LlamaServer()
    app["llama"] = llama
    await llama.start()

    ha = HAClient(session)
    app["ha"] = ha
    try:
        await ha.connect()
        ctx = AmbientContext(ha)
        await ctx.start()
        app["context"] = ctx
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("HA-Verbindung/Ambient-Kontext nicht verfügbar: %s", exc)

    if BRAIN_ENABLED and BRAIN_URL:
        app["brain"] = BrainClient(session, BRAIN_URL)
        _LOG.info("Brain-Anbindung aktiv: %s", BRAIN_URL)
    else:
        app["brain"] = None

    tts = KokoroEngine()
    app["tts"] = tts
    wyoming_task = None

    async def _boot_sequence():
        """Bewusst SEQUENZIELL statt parallel: LLM-Download/Ladevorgang und
        Kokoro-Download/Ladevorgang gleichzeitig laufen zu lassen hat auf einer
        ressourcen-knappen VM CPU/RAM in die Höhe getrieben und den Host
        instabil gemacht. Daher startet TTS erst NACHDEM llama-server bereit ist."""
        healthy = await llama.wait_healthy(session)
        if not healthy or not TTS_ENABLED:
            return
        try:
            await tts.ensure_downloaded(session)
            tts.load()
            await run_wyoming_server(tts)
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Kokoro-TTS konnte nicht gestartet werden: %s", exc)

    wyoming_task = asyncio.create_task(_boot_sequence())

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/memory", handle_memory)
    app.router.add_get("/api/brain_test", handle_brain_test)
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_route("*", "/v1/{tail:.*}", handle_v1_proxy)
    app.router.add_static("/", "/www")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    _LOG.info("Home Voice server listening on 0.0.0.0:%d", PORT)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    _LOG.info("Shutting down …")

    if wyoming_task:
        wyoming_task.cancel()
    ctx = app.get("context")
    if ctx:
        await ctx.stop()
    await ha.close()
    await llama.stop()
    await runner.cleanup()
    await session.close()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
