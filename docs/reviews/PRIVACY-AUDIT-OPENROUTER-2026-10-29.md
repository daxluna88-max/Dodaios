# Audit privacy — cosa lascia il server verso OpenRouter (29/10/2026)

Priorità 4 del programma. Letto il codice di `dodaios/plugins/brain/openrouter.py`, `dodaios/memory/user_profile.py`, `dodaios/brain/manager.py` (fork `claude/it-complete` @ `141fa3be` e upstream `56eb2e4f`). **Nessuna chiamata di rete fatta per questo audit; nessuna impostazione dell'account OpenRouter letta.**

## Cosa viene inviato a ogni turno (verificato nel codice)
| ID | Contenuto | Fonte | Stato |
|---|---|---|---|
| P1 | Messaggi dell'utente e cronologia della chat | `BrainRequest.messages` → `stream_complete` | VERIFICATO (lettura codice) |
| P2 | Profilo utente (`USER.md`, fino a ~2000 caratteri: nome, preferenze, ultime 5 voci di cronologia) | `UserProfile.render_for_prompt` | VERIFICATO (lettura codice); non osservato in una richiesta reale |
| P3 | Riga data/ora con zona (`[Current date and time: … CET (UTC+01:00)]`) | `_current_time_label` | VERIFICATO (test + prova su iPhone) |
| P4 | Contesto memoria/wiki selezionato per il turno | `_build_turn_context` | NON VERIFICATO nel dettaglio |
| P5 | Risultati degli strumenti (es. contenuto di file letti) | loop strumenti del cervello | NON VERIFICATO nel dettaglio |
| P6 | Intestazioni `HTTP-Referer: https://github.com/Dodaios/Dodaios` e `X-Title: Dodaios` | `openrouter.py` | VERIFICATO (lettura codice) |

## Politica sui dati
| ID | Controllo | Esito |
|---|---|---|
| P7 | Il codice imposta preferenze di instradamento per la privacy (`provider.data_collection = "deny"`, ZDR)? | **NO**: nessuna occorrenza nel codice; valgono le impostazioni dell'account OpenRouter |
| P8 | Il modello `nvidia/nemotron-3-ultra-550b-a55b:free` è un endpoint gratuito: i fornitori di endpoint gratuiti possono registrare i prompt e usarli per addestrare, secondo la politica del fornitore e le impostazioni dell'account | **OSSERVATO (account)**: «Allow free endpoints that train on request data» è ACCESO; «paid endpoints that train», «free endpoints that publish prompts» e «1% data discount» sono spenti (screenshot di Dax 29/10 22:49). Quindi con un modello `:free` i fornitori possono conservare e addestrare sui prompt e le risposte; non risulta pubblicazione in dataset pubblici. La scheda del modello `:free` non è stata vista |
| P8b | Account OpenRouter → Data Policies → Zero Data Retention (screenshot di Dax, 29/10 22:47): **spento** per «All other models», Anthropic, OpenAI, Google, SpaceXAI. Nessun vincolo di instradamento verso endpoint senza conservazione. Parte bassa della pagina (registrazione/addestramento) e scheda del modello `:free` **non ancora vista** | OSSERVATO (parziale) |
| P9 | Esiste un passo di consenso prima del primo invio a un fornitore cloud? | **NON TROVATO** nel codice letto (le occorrenze di `consent`/`privacy` sono in altri contesti: permessi, dettatura, screen context, onboarding); non ho aperto tutti i file |
| P10 | «Dimentica» elimina dal profilo (da `82c3cdf2`/`9c596ccf`), ma ciò che è già stato inviato non si richiama | Limite di principio, da dichiarare all'utente |
| P11 | Cronologia chat e flight recorder sul VPS conservano ancora le frasi dopo un «dimentica» | VERIFICATO (grep del 29/10 sul VPS) |

## Cosa significa per Dax
- Ogni messaggio e il tuo profilo escono dal VPS verso OpenRouter e verso il fornitore del modello scelto. Con un modello `:free` il rischio che i prompt vengano conservati è più alto, e oggi non ho una prova su quale politica si applichi al tuo account.
- La voce spenta e il blocco di Codex non cambiano questo flusso.

## Decisioni che servono a Dax
1. **Controllare** l'account OpenRouter → Settings → Privacy: cosa è permesso per endpoint gratuiti e a pagamento (registrazione dei prompt, addestramento). Incollare qui cosa vede, senza chiavi.
2. **Politica:** (A) lasciare l'impostazione dell'account e mostrare un avviso chiaro nell'interfaccia; (B) aggiungere in Impostazioni un interruttore «Non permettere ai fornitori di conservare i miei dati» che invia `provider.data_collection = "deny"` (attenzione: molti modelli `:free` non risponderebbero più con quell'opzione); (C) una modalità «privata» solo locale per i dati sensibili (richiede un modello locale, oggi non installato).
3. **Consenso:** vuoi un avviso al primo invio verso un cloud («Questo testo e il tuo profilo vanno a OpenRouter»)?

## Per GPT (revisione)
Verificare P4, P5, P9 sul codice completo; criteri di accettazione per il consenso; test offline che nessun invio parta senza consenso quando l'interruttore è attivo.

Nessun segreto in questo file.

## Addendum 2026-10-30 (Claude): what changed since, and what GPT is asked to check

This addendum was written after the audit above; the audit text is unchanged.

| ID | State on 2026-10-30 |
|---|---|
| P4 | The per-turn blocks are gated by `[brain] personal_context_to_cloud` (`dodaios/brain/manager.py`, `_personal_context_allowed`): profile, learned notebook, people, contacts, identity card, core memory, per-turn wiki snippets, dated plans. Covered by `tests/unit/brain/test_personal_context_gate.py` and `tests/integration/test_personal_context_in_prompt.py`. |
| P5 | With the switch off, the tools whose result is the person's stored data are withdrawn from the turn: `whoami`, `wiki-list`, `wiki-page-read`, `wiki-recall`, `contact-lookup` (`_PERSONAL_READ_TOOLS`, patch `it-privacy-25`, commit `3173a3fda`). Results of other tools (a file read with `RunCommand`, web search) still go to the provider by design. |
| P9 | Consent at the first cloud send: NOT implemented, NOT chosen by Dax (options A/B/C/D listed in `docs/STATO-CONSEGNA-2026-10-30.md`). |
| Copies | Chat history, the flight recorder and the wiki keep the text after a "forget" (stated to the person by the forget reply). The history of the current chat is part of every request. |

Device evidence (Dax, iPhone, `docs/COLLAUDO-REGISTRO-2026-10-30.md`): with the switch OFF Jarvis did not know a fact it had been told earlier (IP-9, weak signal; the request body was not inspected, so NOT PROVEN).

**Request for GPT (IP-9, offline):**
1. With a fake provider that records the exact `BrainRequest` (system prompt, messages, tool list), build a `BrainManager` whose profile, notebook, contact and wiki contain sentinel strings, with `personal_context_to_cloud = false`.
2. Assert that no sentinel appears anywhere in the recorded request, and that the five tools above are absent from the tool list.
3. Repeat with the switch on: sentinels present, tools present.
4. Say which paths still carry personal text with the switch off (chat history, tool results) and whether the wording shown to the person in Settings → General → Privacy states them honestly.

Acceptance: the offline test passes in both directions and the list in 4 is complete. Nothing is closed without it; a device run does not replace it.

No secret in this file.

## Addendum 2026-10-31 — IP-9 offline (Claude; GPT review requested)

Test: `tests/unit/brain/test_personal_context_request_capture.py`. A fake provider records the exact `BrainRequest` of a real `BrainManager.generate()` turn (real dispatcher, an executor present so tools are offered; the test fails if no tool reaches the provider, so the tool assertions cannot be vacuous). Sentinels: profile, learned notebook, contacts, per-turn wiki text, external turn memory (ADR-0045).

- Switch OFF: no sentinel anywhere in the serialized request; none of `whoami`, `wiki-list`, `wiki-page-read`, `wiki-recall`, `contact-lookup` in the tool list; `search_web` still offered.
- Switch ON: all five sentinels and the five tools present.
- Result: 2 tests pass; with the 8 existing gate tests, 10 pass. Ran offline, no network, no cost.

Paths that still carry personal text with the switch OFF:
1. The chat history of the current chat (what the person typed, earlier replies, tool results already in that history). NOT gated.
2. The result of any other tool (file read, `RunCommand`, web search). NOT gated, by design.
3. The realtime voice path builds its prompt with `_learned_block()` in `dodaios/realtime/session.py` without reading the switch. NOT FIXED. The pilot does not use realtime (paid, excluded by Dax).
4. NOT EXAMINED: the learning/curator review loop (sends conversation excerpts to a model), worker and replay paths. The test covers `generate()` only.
5. Data already sent to a provider cannot be recalled or deleted.

Settings wording (`settings_view.personal_context_description`) said "they stay on this machine": too broad because of 1-2. Corrected in `it.json` and `en.json`; de/es/pt/zh still carry the old text (no native review, not touched). The bundle `dist` is NOT rebuilt, so the new wording is NOT live.

P9 (consent at the first cloud send) stays an open decision of Dax. "Dimentica" scope: profile and notebook only; history, flight recorder, wiki and backups keep the text (the forget reply says so).
