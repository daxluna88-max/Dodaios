# Collaudo registry (2026-10-30)

Format: ID | product | device | commit | expected | observed | status | evidence | cost.

| ID | Product | Device | Commit (frontend dist) | Expected | Observed | Status | Evidence | Cost |
|---|---|---|---|---|---|---|---|---|
| IP-1 | Jarvis pilot (VPS) | iPhone, Safari via Termius tunnel | `504305405` (bundle `index-D6EYJn6o.js`) | Chat open after 30 s screen lock; long answer complete; no stuck "In lavorazione" | Reported by Dax: ok | PASS | Dax's verbal report, 2026-10-30 ~16:30 CET; no screenshot | one short answer on the paid model |
| IP-5a | Jarvis pilot (VPS) | iPhone, Safari via Termius tunnel | frontend `504305405`, backend before `it-partial-27` | Stop mid-answer then reload: the words already written stay, marked "Interrotto" | Text written before Stop vanished after reload (screenshot 16:35, "Interrotto 37s", no text) | FAIL | Screenshot from Dax 2026-10-30 16:35 | one story on the paid model |
| IP-5b | Jarvis pilot (VPS) | iPhone, Safari via Termius tunnel | backend `it-partial-27` (VPS report sha `267e06daa`, 3 PASS) | Same as IP-5a | Screenshot 16:41 shows a partial story ending "…ognuno per sé, I" with "Interrotto 6.9s". Whether the page was reloaded after Stop is NOT confirmed | PASS | Screenshots 16:41 and 16:43; Dax confirmed the page was reloaded after Stop. After "Riprendi" the model continued the story from the kept text (asked for by the person) | one story on the paid model |
