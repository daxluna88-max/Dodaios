# Phone companion (`/m/`)

An installable web app for the phone: chat with Jarvis (type or dictate), see what it is doing,
approve or deny actions, stop a turn, and exchange files in both directions (photos, videos, PDFs,
audio, anything). Plain static files in `dodaios/ui/web/mobile/` (no build step); server side in
`dodaios/phone/` (devices, file store) and `dodaios/ui/web/phone_routes.py`.

## Pairing and security model
1. On the computer open `/m/pair.html` (the computer's own browser; a remote browser must give the
   Control Key, kept in that page's memory only). It shows a code valid for 5 minutes, one use.
   A new code invalidates the previous one; 8 wrong codes lock redeeming for 5 minutes.
2. On the phone open `/m/` and enter the code. The server answers with a random credential set as an
   **HttpOnly, SameSite=Strict** cookie scoped to `/api/phone` (also `Secure` behind HTTPS), plus a
   CSRF token the page keeps in memory. Only a hash of the credential is stored.
3. Every `/api/phone/*` request re-reads the device row, and the long operations re-check
   membership at the moment they mutate: before a chunk is written, before a file is marked ready
   (after its hash), when a transfer is created, and before a chat message is dispatched after its
   attachments were prepared. A **revoked device therefore cannot start anything new**: sessions,
   uploads, downloads in flight (checked every 4 MiB block) and new chat dispatches stop at once.
   An agent turn that was already dispatched before the revocation is not cancelled (that is a
   separate policy; stop it from the desktop). Unsafe methods also need `X-Phone-CSRF` and an
   Origin equal to the Host.
4. The phone credential opens only `/api/phone/*`. `/api/phone-admin/*` (pairing, device list and
   revocation, publishing a file) stays behind the normal owner guard (session, loopback, Control
   Key); the phone cookie does not open it, nor the rest of the app.
5. Chat calls reuse the desktop chat routes' own functions, so **controller approvals, cancel and
   queuing behave as on the desktop**. Sending a file only attaches it; it never approves an action.

## Files
- Resumable and chunked (4 MiB): the phone hashes the file (SHA-256, in slices), creates the
  transfer, uploads sequential chunks and finishes; the server verifies the digest before the file is
  downloadable. After a lost response or connection the client asks for the durable offset and
  continues; reselecting the same file continues an earlier attempt after the digest is re-checked.
- Limits: 2 GiB per file, 10 GiB total, 1000 files. Unfinished uploads expire after 24 h, finished
  files after 14 days; a sweep also removes orphan blobs. Unfinished uploads of a revoked device are
  deleted.
- Storage: `<data dir>/phone/files/`, opaque names, mode 0700/0600, owner kept separate from device
  membership. Files are **not encrypted at rest**: keep the data directory on an encrypted volume.
- Downloads are `attachment` + `nosniff` + `Range` support; only a short allowlist of images, video
  and audio may open inline, in a sandboxed response (`CSP: sandbox`). Never a path download.
- An upload (while hashing or sending) can be cancelled from the File tab or by tapping its chip in
  a chat: it stops and the server copy is deleted.
- Phone to Jarvis: upload in the **File** tab, or the **+** in a chat (the file goes through the
  existing chat attachment pipeline when you send). Jarvis to phone: publish from the pairing page
  ("Invia un file al telefono") or `POST /api/phone-admin/outbox {"path": ...}`; it appears under
  "Da Jarvis". Files are data, never commands.

## Client behaviour
Every asynchronous step is bound to the view and the pairing it started under. A response that
arrives after the person went elsewhere, signed out or re-paired is dropped, never rendered, and a
chat's draft and staged files are kept per chat, so finishing a send never touches another chat's
composer. Signing out aborts in-flight requests and uploads and forgets drafts and files.
The service worker handles only same-origin GETs under `/m/` and deletes only caches named
`jarvis-panel-*`.

## Reach it from an iPhone (Tailscale)
Safari offers install-to-home-screen, the service worker and dictation only on **HTTPS**, so use
Tailscale's TLS front rather than the plain `100.x` address, and keep Jarvis itself on loopback:

```bash
tailscale serve --bg https / http://127.0.0.1:47821
export JARVIS_TRUSTED_HOSTS=<machine>.<tailnet>.ts.net
```

Then `https://<machine>.<tailnet>.ts.net/m/pair.html` on the computer (it shows the phone URL) and
`/m/` on the phone. Nothing is exposed to the internet; no router or firewall change.

## What is and is not verified
- Verified (Linux): store, devices and routes unit tests (resume after restart, hash mismatch, quota,
  offset conflict, cross-owner denial, crash-tail repair, concurrent writers, expiry/orphans, a long
  hash not blocking other transfers, anonymous/unpaired/CSRF/foreign-origin refusals, revocation
  mid-stream, range, inline allowlist, chat reuse); surface-security suites still pass.
- Browser tests in the repo (`tests/browser/phone_panel`, run by `tests/unit/ui/web/test_phone_panel_browser.py`
  when Node + Playwright exist, otherwise skipped) against a real server, with deliberately held
  responses: a poll answered after sign-out, a send finishing in another chat, cancelling an active
  upload, an upload outliving a sign-out and re-pair, and the service worker's cache ownership. All
  five fail against the previous client and pass now.
- Verified end to end in headless Chromium at an iPhone 13 viewport against a REAL running server:
  pairing (bad then good code), cookie flags, session surviving a reload, chat list and approval
  route, a 9 MiB upload surviving a dropped connection, download byte-for-byte, owner-published
  file, chat attachment staging, revocation returning the phone to the pairing screen.
- NOT verified: a real iPhone/Safari; Tailscale serve and the trusted-host setting; dictation
  (`webkitSpeechRecognition`, it-IT); a live agent turn with an attached file (the ingest step is
  covered with a fake); large-file throughput on a phone network; power-loss durability.
- Not built: native iOS app, Share Extension, push notifications and background transfers (a web
  page cannot transfer in the background: keep it open while uploading); live push for chat (it polls);
  answering agent questions and credential requests; creating the very first chat (use the desktop).

## Integration with the control stack (block 14)
See `docs/adr/0062-phone-integration.md`: approvals now echo request id, tool, fingerprint and STOP generation; the panel has an always-visible STOP using `POST /api/phone/stop` (the desktop `/api/stop` semantics); remote access design and limits are there. Nothing in that block was run or tested on a device.
