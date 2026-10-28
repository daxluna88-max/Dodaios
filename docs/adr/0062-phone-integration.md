# ADR-0062 - Phone integration (iPhone and Android), block 14

Status: proposed. Everything below was prepared statically: NOTHING was run (no pytest, vitest,
Playwright, tsc, eslint, build, service or device), TypeScript was not type-checked, `dist` was not
rebuilt (the `/m/` panel is plain static JS and needs no build, but the desktop UI bundle is not
touched and not live), nothing was tested on a real iPhone or Android phone.

Base: `claude/block13` + local merge of `claude/mobile-panel` (PR #4). The merge was clean (no
conflict, no `dist` conflict). The phone panel is `dodaios/phone/`, `dodaios/ui/web/phone_routes.py`,
`dodaios/ui/web/mobile/`, `docs/mobile-panel.md`.

## State per point

### 1. Auth, pairing, revocation - DONE (with one hardening), controls not run
- The phone credential is an HttpOnly, SameSite=Strict cookie scoped to `/api/phone`. The outer guard
  (`surface_security._http_auth_exception`) lets only `/api/phone/*` through unauthenticated; every
  handler authenticates the device itself (`_device`), re-reading the row. Host check and Origin
  check run BEFORE that exception, so a foreign Host or Origin never reaches a handler.
  `/api/phone-admin/*`, `/api/stop`, `/api/control-strip` and everything else stay behind the owner
  guard (session, loopback open access, Control Key); the phone cookie is not valid there.
- Unsafe methods on `/api/phone` need `X-Phone-CSRF` and Origin == Host (`_device`); redeem needs
  Origin == Host. Pairing: 8-char one-use code, 5 min, 8 failures lock 5 min, max 20 devices.
- Revocation is re-checked at the mutation: chunk write, finish, create, message dispatch after
  attachment preparation, downloads every block. NEW: `new_chat` and `approve` re-check after their
  awaits (`_ensure_active`), and the new `stop`/`control-strip` routes authenticate like all others.
- Residual: an agent turn already dispatched before a revocation keeps running (stop it from the
  desktop). The phone can read every chat (same as the desktop owner), not only its own.
- Files: `phone_routes.py`, `phone/devices.py`. Tests (written): `test_phone_integration.py`
  (`test_revoked_device_decides_nothing`, `test_phone_cookie_opens_no_other_api`,
  `test_phone_stop_needs_csrf_and_has_no_resume`).

### 2. Attachments - DONE (provenance and isolation fixed), controls not run
- Limits and safety unchanged: 2 GiB per file, 10 GiB, 1000 files, SHA-256 verified, 4 MiB chunks,
  24 h / 14 d expiry, inline allowlist in a sandboxed response, `nosniff`, never a path download.
- Provenance (`brain/provenance.py`): in this stack the chat path never positively declares a turn
  UNRESTRICTED, so text derived from a phone file is UNAVAILABLE (fail closed for remote engines and
  remote speech). NEW: a message with phone attachments explicitly sets the turn UNAVAILABLE and the
  phone module must never call `attach_unrestricted` (guarded by a source test). Open: the chat
  service itself does not label per-attachment provenance; the turn label is what protects.
- Product isolation: the phone store used `config.memory.data_dir` or a RELATIVE `data` fallback.
  NEW (`_phone_root`): no relative fallback (503 without a configured data dir); under an active
  product launch (`JARVIS_PRODUCT_ACTIVE`) the root passes `check_product_root` (not a link, no
  overlap with the shared or the other product's directory). Data lives in `<product data>/phone/`.
- Residual: files are not encrypted at rest; ingest copies the phone file into the chat's working
  folder (the same as a desktop drop), where an agent can read it.

### 3. Approvals - DONE for the chat-card path; the mission path is not exposed to the phone
- What the phone approves is the chat's approval card (`AgentChatService.resolve_approval`); the
  decision reaches `ToolExecutor` only through the chat approval bridge, which publishes
  `ActionApproved` echoing the executor ticket's own `approval_id`, tool name and fingerprint. The
  phone adds no other path and does not touch `MissionToolApprovalCoordinator` (missions stay on
  the desktop).
- NEW: the phone approval body requires `request_id` (the card id, must equal the URL), `tool_name`,
  `call_fingerprint` and `generation` (all required, extra fields forbidden, only `allow` or `deny`;
  "always allow" is refused). The fingerprint is `safety.effects.call_fingerprint(name, input)` of
  the card as SHOWN; `GET /api/phone/chats/{id}` serves it (and `request_id`, `stop_generation`,
  `door_open`). A card whose tool or arguments differ is refused 409 `identity_mismatch` and nothing
  is consumed; a wrong generation is 409 `stale_generation`.
- After STOP: `allow` is refused 409 `stopped` (door closed); `deny` is still accepted.
- Residual/open: this fingerprint binds the phone's decision to the card text; the executor-side
  binding is the bridge's, unchanged. A STOP landing between the check and the resolve is only
  caught by the executor/bridge (not proven here). The desktop chat route itself still takes only
  `decision` (not changed: out of scope).

### 4. STOP from the phone - DONE, ordering ported
- NEW `POST /api/phone/stop?client_seq=N` calls `control_strip_routes.post_stop` (StopController
  door first, parts each with `requested/confirmed/still_active/unknown`, `stopped` only if all
  confirmed). `GET /api/phone/control-strip?client_seq=N` serves the same snapshot. No phone
  resume: reopening the door is the owner's act on the computer.
- Panel: a STOP button in the header, visible whenever paired, independent of polling, of a chat
  being open and of any stale data (`index.html #stopall`); it also ends the open chat's turn. The
  reply is shown honestly (full "tutto fermato" or "STOP parziale" with each non-confirmed part);
  a failed request says "STOP non confermato".
- Ordering: `mobile/order.js` is a 1:1 port of the desktop `StripOrderer` (boot_id, seq, generation,
  barrier, epoch floor, current-write). STOP uses it; the phone does NOT poll the strip, it takes the
  generation and door from each chat poll. Gap: the chat poll itself is not ordered by it (the
  panel's per-view generation guard drops stale replies instead).
- Residual: `/api/stop` parts for playback/realtime/missions may be `unknown` or `still_active`;
  the phone shows that, it does not fix it.

### 5. Secure remote access - DESIGN ONLY (nothing implemented, no port opened)
Goal: reach `/m/` from outside the home network without exposing Dodaios.
- Acceptable (authenticated, owner's explicit choice): (a) a tailnet/VPN (Tailscale, WireGuard) with
  Dodaios kept on loopback, fronted by the VPN's own HTTPS (`tailscale serve`, see
  `docs/mobile-panel.md`); (b) an authenticated tunnel the owner explicitly chooses, where the
  tunnel itself requires a login before any request reaches Dodaios (identity-aware proxy / zero
  trust access policy), never relying on the pairing cookie alone.
- HTTPS is required: the pairing cookie is `Secure` only when the request is https (or
  `X-Forwarded-Proto: https`); iOS Safari also needs HTTPS for the service worker, install and
  dictation. Terminate TLS at the VPN/tunnel front, forward to `127.0.0.1`.
- Trusted hosts: set `JARVIS_TRUSTED_HOSTS=<machine>.<tailnet>.ts.net` (comma separated). Without
  it the Host check rejects the name. Add only names the owner controls; never `*`.
- NOT acceptable: a public port or router port-forward; an unauthenticated tunnel (anyone with the
  URL reaches the pairing page and the 8-code lock is the only defence); binding Dodaios to
  `0.0.0.0` instead of loopback; adding a wildcard to trusted hosts; sharing the Control Key to
  "make it work" on the phone.
- LAN exposure risks: binding to the LAN address exposes the app to every device on that Wi-Fi
  (guests, IoT) over plain HTTP; the cookie then cannot be `Secure`, and the code/credential cross
  the network in clear. Prefer the VPN front even at home.
- How to verify (controls prepared, NOT RUN): from outside the VPN the host must not resolve or
  connect; with the VPN `curl -I https://<host>/m/` answers and `curl -I https://<host>/api/phone-admin/devices`
  answers 401; a request with a foreign `Host` is refused; the redeem response sets `Secure`; a
  revoked device gets 401 at once; `ss -ltnp` shows Dodaios on 127.0.0.1 only.

### 6. Limits of phone control - STATED
- The panel controls the Dodaios session on the computer (chat, approvals, STOP, files). It does NOT
  control other apps on the phone, the phone's own settings, or the computer's screen.
- Not built: native iOS/Android app, Share Extension / Android share target, background push and
  background uploads (keep the page open), live push (it polls 1.5 s / 4 s), the very first chat,
  answering agent questions and credential requests, mission approvals.
- Known from code, NOT tested on any device: a service worker (shell cache only, needs HTTPS and a
  supporting browser); dictation uses `SpeechRecognition || webkitSpeechRecognition` (it-IT) and
  exists only where the browser provides it, typing always works; the file picker is a plain
  `<input type=file multiple>` (no `capture` attribute): the OS offers camera/library/files in its
  own way, which differs between iOS Safari and Android Chrome; install is "Add to Home Screen" on
  Safari and an install prompt/menu on Chrome (manifest `display: standalone`, apple meta tags for
  iOS). Behaviour of hashing a multi-GiB file in a mobile browser is unknown.

## Controls prepared, NOT RUN
F03 pairing (bad/good code, lock, expiry, `Secure` flag over HTTPS), F04 upload/resume (drop
connection, resume, hash mismatch, quota), F05 revoke (mid-upload, mid-download, pending approval,
STOP), F06 remote access (verification list in point 5). Written tests: `tests/unit/ui/web/
test_phone_integration.py` (new), `test_phone_routes.py` (approval body updated), existing
`tests/unit/phone`, `tests/browser/phone_panel` (service worker cache name v3 -> v4).

## Files changed
`dodaios/ui/web/phone_routes.py`, `dodaios/ui/web/mobile/{app.js,order.js,index.html,sw.js}`,
`docs/mobile-panel.md` (pointer), tests above. i18n, safety, memory, setup, speech, install untouched.

## Block 17: GPT round 4 (H1)
**State: fixed in code (statically); NOT executed.** Finding H1 (HIGH): the phone route ran
`attachments.ingest` BEFORE labelling the turn, so a vision engine could already hold the image; no label
reached `DropAnalysis`, `MessageBody` or `send_or_queue`, and the request's ContextVar is not a durable
label of a queued turn.

Choice: the label is data on the record, decided before analysis, and checked at the destination.
- Classify first. `phone_routes.send_message` passes `provenance=UNAVAILABLE` to `ingest` (a phone upload is
  never unrestricted by default). `attachments.ingest/_analyze` and `drop_analysis.analyze` take a REQUIRED
  `provenance` keyword (no default). The desktop callers (`agent_chat_routes` attach, `prompt_attachments`,
  `agentic_ide_routes`) state `UNRESTRICTED` explicitly.
- Destination before bytes. `drop_analysis._must_stay_local` (Work always; Consumer unless positively
  UNRESTRICTED) makes `_resolve_vision_brain(local_only=True)` / `resolver.resolve_vision_brain(local_only=)`
  skip non-loopback engines (`engine_policy.destination_of`), and `_analyze_image` re-checks any resolved or
  pinned brain (`_brain_is_local`; an unnamed engine is remote) BEFORE `_describe_image`. If only a remote
  engine can see, the image is attached undescribed and the note says so: no silent downgrade.
- Label on the record. `DropAnalysis.provenance` (wire, `from_dict`: missing/unknown = UNAVAILABLE) is stamped
  on every analysis, so extracted text/description keep it; the stored `user_message` attachments list it.
  `MessageBody.provenance` (None = no claim; only tightens) -> `send_or_queue(provenance=)` -> `_Queued.provenance`
  (default and old/missing rows = UNAVAILABLE, `to_dict/from_dict`) -> `AgentChatService.send(provenance=)`.
- Dispatch. `service.send` computes `attachments.turn_label(attached, provenance)` from the record and applies it
  with `set_turn_provenance` inside a COPIED context handed to the turn task (`create_task(context=)`), not the
  request's or the queue drain's context. The label can only tighten; it never calls `attach_unrestricted`.
- Downstream: labelled UNAVAILABLE, the existing fail-closed rules (`remote_speech_blocked`, `storage_label`,
  `remote_replayable`, curator/history gating) apply unchanged.

Residual limits: the send queue is in-memory (ADR note on `send_queue`), so "persisted with the queue" means the
label is part of the record while it lives; a restart drops the queue as before. Locality is whatever
`destination_of` proves (loopback only). Brain-manager `begin_turn` resets the per-turn state to UNAVAILABLE at
its own start (fail closed); a Consumer turn that truly needs a declared-clean attachment turn is not supported.
Documents/PDF text is extracted locally (no model) but still labelled UNAVAILABLE. Other `svc.send` callers
(voice, control) make no claim. An image attached by an in-workspace path from the phone is not possible (uploads only).

Controls prepared, NOT RUN: `tests/unit/test_block17_phone_attachment_provenance.py` (new: remote vision engine not
called for UNAVAILABLE/RESTRICTED; local engine called; Work needs verified-local; resolver asked `local_only`;
required argument; ingest passes the label; wire/queue round-trip and old rows = UNAVAILABLE; queued message
dispatched with its record's label; `turn_label` only tightens; copied-context turn), `test_phone_integration.py`
(behaviour test replaces the source-text test), `test_phone_routes.py` (ingest label and body label), existing
fakes updated for the `provenance` keyword. Nothing was run: no pytest, no CI, no build, no app, no model call.
