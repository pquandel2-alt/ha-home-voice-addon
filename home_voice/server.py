"""Home Voice Add-on backend.

Startet einen lokalen llama.cpp-Inferenz-Server (OpenAI-kompatibel) als
Subprozess und stellt einen aiohttp-Server davor, der:
  * `/v1/*`  an llama-server durchreicht (später mit Kontext-Injektion),
  * `/api/status`  Modell-/Health-Infos für das Panel liefert,
  * `/`  das Ingress-Panel ausliefert.

Schritt 1 (Milestone "per Text mit dem lokalen Modell reden"): reiner Proxy +
Modell-Download. Kontext-Cache (Stufe 1), Brain-Recall (Stufe 2) und TTS folgen.
"""

import asyncio
import json
import logging
import os

from aiohttp import ClientSession, ClientTimeout, web

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
THREADS = int(_OPTIONS.get("threads", 6))
TEMPERATURE = float(_OPTIONS.get("temperature", 0.7))


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
    return web.json_response({
        "model_key": MODEL_KEY,
        "model_label": MODEL["label"],
        "context_size": CONTEXT_SIZE,
        "threads": THREADS,
        "ready": llama.ready,
        "available_models": [
            {"key": k, "label": v["label"]} for k, v in MODEL_REGISTRY.items()
        ],
    })


async def handle_v1_proxy(request):
    """Reicht /v1/* transparent an llama-server durch (Streaming-fähig).

    Späterer Erweiterungspunkt: vor dem Weiterreichen den Ambient-Kontext
    (Stufe 1) + Brain-Recall (Stufe 2) in `messages` injizieren.
    """
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


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def on_startup(app):
    session = ClientSession()
    app["session"] = session
    llama = LlamaServer()
    app["llama"] = llama
    await llama.start()
    # Health-Wait im Hintergrund — Panel/Proxy sind sofort erreichbar,
    # /v1 liefert 502 bis das Modell geladen ist.
    asyncio.create_task(llama.wait_healthy(session))


async def on_cleanup(app):
    llama = app.get("llama")
    if llama:
        await llama.stop()
    session = app.get("session")
    if session:
        await session.close()


def main():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status)
    app.router.add_route("*", "/v1/{tail:.*}", handle_v1_proxy)
    app.router.add_static("/", "/www")
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    _LOG.info("Home Voice server listening on 0.0.0.0:%d", PORT)
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
