# Changelog

## 0.2.0

- **Absturz-Fix:** Beim ersten Test überlastete das Add-on CPU/RAM und brachte den
  HA-Host zum Absturz. Ursachen behoben:
  - llama.cpp-Build auf 2 parallele Jobs begrenzt (`-j2`) statt unbegrenzt.
  - Default-Threads von 6 auf 2 gesenkt.
  - LLM-Start läuft nicht mehr parallel zu einem zweiten Modell-Ladevorgang.
- **TTS jetzt über Piper:** Kokoro (kein offizielles Deutsch) entfernt. Für deutsche
  Sprachausgabe das separate Piper-Add-on nutzen. Spart zusätzlich schwere ML-Pakete
  und einen zweiten Modell-Download beim Start.
- **Gedächtnis:** Stufe-1-Ambient-Kontext (Anwesenheit/Wetter/To-Dos, im RAM
  vorgewärmt) und optionaler Stufe-2-Brain-Recall bei Wissensfragen.
- **Panel:** Neue Tabs „Gedächtnis" und „Einstellungen".

## 0.1.0

- Erstes Release: lokaler llama.cpp-Inferenz-Server (Phi-4-mini), OpenAI-kompatibler
  Endpunkt für HA Assist, Ingress-Panel mit Status und Test-Konsole.
