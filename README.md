# Home Voice Add-ons

Home-Assistant-Add-on-Repository für **pq_home_voice** — ein vollständig **lokaler**
Sprachassistent mit eigenem LLM (Phi-4-mini via llama.cpp), Streaming-TTS (Kokoro) und
einem Gedächtnis über den Brain-Knowledge-Graph.

## Installation

1. In Home Assistant: **Einstellungen → Add-ons → Add-on-Store**
2. Oben rechts **⋮ → Repositories** → diese URL hinzufügen:
   `https://github.com/pquandel2-alt/ha-home-voice-addon`
3. Add-on **Home Voice** installieren und starten.

## Enthaltene Add-ons

| Add-on | Beschreibung |
|--------|--------------|
| **Home Voice** (`home_voice`) | Lokaler LLM-Inferenz-Server (OpenAI-kompatibel) + Ingress-Panel |

Details siehe [`home_voice/DOCS.md`](home_voice/DOCS.md).
