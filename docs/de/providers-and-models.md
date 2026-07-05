# Anbieter und Modelle

## Anbieter-Seite

**Was es macht.** Ein Dashboard aller unterstützten Anbieter: Konfigurationsstatus, Anzahl katalogisierter Modelle, aggregierte Fähigkeiten (chat, vision, tools, json…), Ein/Aus-Schalter, Verbindungstest und API-Schlüssel-Verwaltung.

![Anbieter-Verwaltung](screenshots/providers.png)

**So wird es benutzt.**
- **Schlüssel hinzufügen / Schlüssel aktualisieren**: speichert oder aktualisiert den API-Schlüssel des Anbieters. Der Schlüssel landet im **verschlüsselten Tresor** (siehe unten), nicht in einer Konfigurationsdatei.
- **Test**: `POST /providers/{id}/test` führt eine echte minimale Completion-Anfrage gegen den Cloud-Anbieter aus (nicht nur eine Schlüsselprüfung) und meldet Ergebnis/Latenz.
- **Schalter**: aktiviert/deaktiviert den Anbieter **global**, ohne den Schlüssel zu entfernen.
- **N Modelle**: klappt den Modellkatalog des Anbieters auf, mit den Sichtbarkeits-Steuerungen (siehe unten).

Der Kasten oben rechts fasst zusammen, wie viele Anbieter konfiguriert sind und wie viele Modelle insgesamt verfügbar sind.

## Modell-Sichtbarkeit in der Modellauswahl

**Was es macht.** Manche Anbieter bieten Dutzende oder Hunderte Modelle an, was das Modellmenü des Chats endlos macht. Hier kannst du pro Anbieter **kuratieren, welche Modelle** im Auswahlmenü erscheinen.

**So wird es benutzt.** Klappe einen Anbieter auf (**N Modelle**): jedes Modell hat ein **Augen**-Symbol:
- 👁 **sichtbar** → erscheint im Chat-Menü; klicken zum Ausblenden.
- 👁‍🗨 **durchgestrichen** → ausgeblendet (gedimmte Zeile); klicken, um es wieder anzuzeigen.

Oben in der Liste: ein Zähler **„N sichtbar · M ausgeblendet"** und die Schaltflächen **Alle anzeigen / Alle ausblenden**, um den ganzen Anbieter auf einmal zu behandeln. Hat ein Anbieter ausgeblendete Modelle, zeigt die Karte ein stets sichtbares Badge **„N ausgeblendet"** (auch bei eingeklappter Liste). Die Wahl wird **persistiert** (`hiddenModels`-Präferenz) und ausgeblendete Modelle verschwinden in Echtzeit aus dem Chat-Menü.

> **Zwei verschiedene Filter.** Dies ist ein **Pro-Modell**-Filter. In der Chat-Seitenleiste unter **Modell** gibt es dagegen den Filter der **sichtbaren Anbieter**, der auf einen ganzen Anbieter wirkt. Beide kombinieren sich: erst ganze Anbieter ausschließen, dann einzelne Modelle verfeinern. Beide sind persönlich und berühren die globale Aktivierung des Anbieters nicht.

## API-Schlüssel-Tresor

**Was es macht.** Schlüssel werden mit Fernet (AES-128-CBC + HMAC-SHA256) verschlüsselt und in SQLite gespeichert, mit In-Memory-Cache. Alle Anbieter fallen zurück Tresor → Umgebungsvariable: liegt der Schlüssel nicht im Tresor, wird der aus `.env` verwendet.

**Konfiguration.** Setze in Produktion einen starken `VAULT_SECRET_KEY`: beim Start wird eine Sicherheitswarnung geloggt, wenn er noch der Standard-Platzhalter ist. API: `PUT /providers/{id}/key`, `DELETE /providers/{id}/key`.

## Modell-Discovery

**Was es macht.** Ruft den Modellkatalog live von der API jedes Anbieters ab (Cloudflare, OpenRouter, Gemini, Groq, Cerebras, Mistral, NVIDIA, Ollama, Agent) und speichert ihn in den internen Katalog — die im Chat wählbare Modellliste bleibt ohne manuelle Pflege aktuell.

![Modell-Discovery](screenshots/discovery.png)

**So wird es benutzt.** Seite **Entdeckung** → Anbieter in der Tab-Leiste wählen → **Discovery ausführen**. Die entdeckten Modelle werden gelistet und im Katalog gespeichert.

## Präfix-basiertes Routing

Das Gateway routet jede Anfrage anhand des Modellnamen-Präfixes:

| Präfix | Anbieter |
|--------|----------|
| `ollama/…`, `groq/…`, `mistral/…`, `together_ai/…`, `fireworks_ai/…`, `huggingface/…` | LiteLLM |
| `gemini/…` | dedizierter Google-Generative-AI-Adapter |
| `openrouter/…` | OpenRouter |
| `cloudflare/…` | Cloudflare Workers AI |
| `cerebras/…` | Cerebras (direktes HTTP) |
| `agent/…` | Multi-MCP-Orchestrator (siehe [MCP und Agenten](mcp-and-agents.md)) |

## Automatischer Anbieter-Fallback

**Was es macht.** Fällt ein Anbieter aus oder läuft in einen Timeout, **bevor** der erste Token gesendet wurde, versucht das Gateway transparent den nächsten Anbieter der `CHAT_FALLBACK_CHAIN` (Format `provider:model,provider:model,...`). Der Wechsel wird mit einem SSE-Frame `provider_switch` signalisiert und in der UI als Hinweis angezeigt. Sobald Tokens streamen, wird der Fehler stattdessen weitergereicht (keine doppelte Ausgabe).

**Konfiguration.** In `backend/.env`:

```env
CHAT_FALLBACK_CHAIN=groq:llama-3.3-70b-versatile,ollama:qwen2.5:7b-instruct
```

Analoge Ketten existieren für Bilder (`IMAGE_GENERATION_CHAIN`) und Embeddings (`EMBEDDING_CHAIN`).
