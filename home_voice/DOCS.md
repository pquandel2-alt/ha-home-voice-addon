# Home Voice

Ein vollständig **lokaler** Sprachassistent für Home Assistant. Das Add-on startet
einen llama.cpp-Inferenz-Server mit einem kleinen, schnellen LLM und stellt einen
**OpenAI-kompatiblen Endpunkt** bereit, den Home Assistant als Conversation-Agent
(Assist) nutzen kann. Dazu kommt ein zweistufiges **Gedächtnis** (Ambient-Kontext +
optionaler Brain-Recall). Die **Sprachausgabe** übernimmt das separate
**Piper-Add-on** (deutsche Stimmen, gleiches Wyoming-Protokoll).

Keine Cloud, keine API-Keys, keine laufenden Kosten.

## Ressourcen-Hinweis (wichtig)

Der Docker-Build kompiliert llama.cpp aus dem Quellcode und lädt danach ein
GGUF-Modell (~1–2,5 GB) herunter. Das ist auf schwacher Hardware spürbar
CPU-/RAM-intensiv. Da Add-on-Build und -Betrieb auf derselben Maschine wie Home
Assistant selbst laufen, kann eine zu aggressive Konfiguration den gesamten
Host (inkl. HA Core) instabil machen.

- `threads` **klein starten** (Default `2`) und erst hochsetzen, wenn geprüft
  wurde, wie viele vCPUs die HA-VM tatsächlich hat und wie viel davon übrig ist.
- Der Compile-Schritt begrenzt sich selbst auf 2 parallele Jobs (`-j2`), um
  RAM-Spitzen beim ersten Add-on-Build zu vermeiden.
- TTS läuft in einem separaten Add-on (Piper), nicht in diesem — dadurch
  entfallen schwere ML-Pakete und ein zweiter Modell-Download beim Start.
- Falls das Add-on beim Start weiterhin CPU/RAM in die Höhe treibt: Add-on
  stoppen, `threads` und/oder `context_size` reduzieren und ein kleineres Modell
  (`gemma3-1b`) testen.

## Konfiguration

| Option | Default | Bedeutung |
|--------|---------|-----------|
| `model` | `phi-4-mini` | Modell: `phi-4-mini` (ausgewogen), `qwen2.5-3b` (schlauer, besseres Tool-Calling), `gemma3-1b` (am schnellsten) |
| `context_size` | `4096` | Kontextfenster in Token |
| `threads` | `6` | CPU-Threads (auf die zugewiesenen vCPUs abstimmen) |
| `temperature` | `0.7` | Sampling-Temperatur |
| `brain_enabled` | `false` | Gedächtnis-Stufe 2 (semantischer Recall über Brain) aktivieren |
| `brain_url` | `""` | Basis-URL der Brain-Instanz, z. B. `http://<brain-host>:3000` |

Beim **ersten Start** lädt das Add-on das gewählte LLM (~1–2,5 GB) nach
`/data/models` herunter. Das kann einige Minuten dauern; der Status im Panel zeigt
„lädt Modell …“, bis es bereit ist. Downloads bleiben über Neustarts/Updates erhalten.

## Als Assist-Agent in Home Assistant einbinden

1. Add-on starten, im Panel (Status-Tab) warten bis **Bereit = ja**.
2. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „OpenAI Conversation"**
   (oder eine kompatible lokale LLM-Integration).
3. Als **Base-URL** eintragen: `http://<HA-IP>:8098/v1`
   (falls du den Host-Port im Add-on unter „Netzwerk" umgelegt hast, entsprechend anpassen)
   (API-Key beliebig, z. B. `local` — wird nicht geprüft).
4. Unter **Einstellungen → Sprachassistenten** eine Pipeline anlegen und den neuen
   Conversation-Agent auswählen. Diese Pipeline kann jeder Voice-Satellite
   (z. B. Nabu/DashVoice) nutzen.

## Sprachausgabe (Piper) einbinden

Dieses Add-on liefert bewusst keine eigene TTS mit. Für deutsche Sprachausgabe
das offizielle **Piper**-Add-on nutzen (deutsche Stimmen, gleiches Wyoming-
Protokoll, ausgereift und ressourcenschonend):

1. **Einstellungen → Add-ons → Add-on-Store → „Piper"** installieren und starten.
2. Piper meldet sich automatisch als Wyoming-TTS-Dienst; ggf. über
   **Geräte & Dienste → „Wyoming Protocol"** hinzufügen.
3. Deutsche Stimme wählen, z. B. `de_DE-thorsten`.
4. In der Assist-Pipeline (siehe oben) Piper als **Text-zu-Sprache**-Engine setzen.

So bilden dieses LLM-Add-on (Sprachverständnis/Antwort) und Piper (Sprachausgabe)
gemeinsam eine vollständige, komplett lokale und deutschsprachige Assist-Pipeline.

## Gedächtnis

- **Stufe 1 (Ambient-Kontext):** immer aktiv, kostenlos. Alle 60s im Hintergrund
  aktualisiert (Anwesenheit, Wetter, To-Dos) plus live nachgeführtes Ereignis-Log.
  Wird jeder Anfrage automatisch als Kontext vorangestellt.
- **Stufe 2 (Brain-Recall):** nur aktiv wenn `brain_enabled: true` und `brain_url`
  gesetzt sind. Wird nur bei erkennbaren Wissensfragen abgerufen, mit einem
  Timeout von 1,2s — bei Nichterreichbarkeit wird die Anfrage nicht blockiert,
  sondern einfach ohne Recall beantwortet.
- **Ehrlicher Hinweis zur Latenz:** Das Add-on wirkt als Conversation-Agent, den
  HA *nach* der Spracherkennung (STT) aufruft — es gibt in dieser Position keinen
  eigenen STT-Schritt, hinter dem sich der Brain-Recall komplett verstecken ließe.
  Bei Wissensfragen kommt daher der reale (kleine, bounded) Timeout als zusätzliche
  Latenz oben drauf. Bei allen anderen Befehlen (Ambient-Kontext reicht, oder HA's
  eingebaute Intents greifen direkt) entstehen keine zusätzlichen Kosten.
- **Lern-Loop:** Korrekturen/neue Fakten werden aktuell nicht automatisch erkannt
  und zurückgeschrieben — `brain_client.write_fact()` steht als Baustein bereit,
  ist aber noch nicht an eine automatische Erkennung "das war eine Korrektur"
  angebunden. Das ist ein bewusst offen gelassener nächster Schritt.

## Panel

- **Status:** gewähltes Modell, Bereitschaft, Kontextgröße, Threads, HA-/TTS-/Brain-Status.
- **Gedächtnis:** Live-Ansicht des Ambient-Kontexts + Test-Feld für Brain-Recall.
- **Test-Konsole:** direkt per Text mit dem Modell reden (streamt die Antwort).
- **Einstellungen:** Modell-Übersicht, TTS-/Brain-Hinweise.

## Endpunkte

| Pfad | Zweck |
|------|-------|
| `/v1/chat/completions` | OpenAI-kompatibel, mit Kontext-Injektion (Streaming unterstützt) |
| `/v1/models` | Modell-Liste (von llama.cpp) |
| `/api/status` | Add-on-Status fürs Panel |
| `/api/memory` | Ambient-Kontext (Stufe 1) als JSON |
| `/api/brain_test?q=...` | Manueller Test des Brain-Recalls (Stufe 2) |
