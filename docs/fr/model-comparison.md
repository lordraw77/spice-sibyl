# Model comparison

**What it does.** Sends the same prompt to 2–4 models simultaneously and streams the responses in side-by-side columns, each with its own telemetry (latency, tokens, cost). Useful for picking the right model for a use case or comparing quality/speed/cost.

![Compare models page](screenshots/compare.png)

**How to use it.**
1. Go to the **Compare** page.
2. Select the models in the dropdowns (up to 4 with **+ Aggiungi modello**).
3. Type the prompt in the text area and press **Confronta** (Compare).
4. Responses stream in parallel, each in its own column; latency, token counts and estimated cost appear at the bottom of each.

**Notes.**
- Requests really do run in parallel: the displayed timings are comparable with each other.
- Every column gets the exact same prompt, without the chat's system prompt: it is a "cold" comparison.
