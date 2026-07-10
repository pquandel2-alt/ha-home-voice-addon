# Home Voice

Ein vollständig **lokaler** Sprachassistent für Home Assistant. Das Add-on startet
einen llama.cpp-Inferenz-Server mit einem kleinen, schnellen LLM und stellt einen
**OpenAI-kompatiblen Endpunkt** bereit, den Home Assistant als Conversation-Agent
(Assist) nutzen kann.

Keine Cloud, keine API-Keys, keine laufenden Kosten.

## Konfiguration

| Option | Default | Bedeutung |
|--------|---------|-----------|
| `model` | `phi-4-mini` | Modell: `phi-4-mini` (ausgewogen), `qwen2.5-3b` (schlauer, besseres Tool-Calling), `gemma3-1b` (am schnellsten) |
| `context_size` | `4096` | Kontextfenster in Token |
| `threads` | `6` | CPU-Threads (auf die zugewiesenen vCPUs abstimmen) |
| `temperature` | `0.7` | Sampling-Temperatur |
| `brain_enabled` | `false` | Gedächtnis über Brain aktivieren (spätere Ausbaustufe) |
| `brain_url` | `""` | URL der Brain-Instanz (spätere Ausbaustufe) |

Beim **ersten Start** lädt das Add-on das gewählte Modell (~1–2,5 GB) nach
`/data/models` herunter. Das kann einige Minuten dauern — der Status im Panel zeigt
„lädt Modell …", bis das Modell bereit ist. Downloads bleiben über Neustarts/Updates
erhalten.

## Als Assist-Agent in Home Assistant einbinden

1. Add-on starten, im Panel warten bis **Bereit = ja**.
2. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „OpenAI Conversation"**
   (oder eine kompatible lokale LLM-Integration).
3. Als **Base-URL** eintragen: `http://<HA-IP>:8099/v1`
   (API-Key beliebig, z. B. `local` — wird nicht geprüft).
4. Unter **Einstellungen → Sprachassistenten** eine Pipeline anlegen und den neuen
   Conversation-Agent auswählen. Diese Pipeline kann jeder Voice-Satellite
   (z. B. Nabu/DashVoice) nutzen.

## Panel

- **Status:** gewähltes Modell, Bereitschaft, Kontextgröße, Threads.
- **Test-Konsole:** direkt per Text mit dem Modell reden (streamt die Antwort).

## Endpunkte

| Pfad | Zweck |
|------|-------|
| `/v1/chat/completions` | OpenAI-kompatibel (Streaming unterstützt) |
| `/v1/models` | Modell-Liste (von llama.cpp) |
| `/api/status` | Add-on-Status fürs Panel |
