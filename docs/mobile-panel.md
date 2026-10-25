# Phone panel (`/m/`)

A small installable web app for the phone: write or dictate to Jarvis, see what a chat is
doing, approve or deny an action, stop a turn, attach photos, videos and files. It is plain
static files (`dodaios/ui/web/mobile/`, no build step) over the routes the desktop UI already
uses (`/api/agent-chat/*`); it adds no server code except the `/m` mount.

## Security model
- The files carry no secret. Every call sends the Control Key as `Authorization: Bearer`.
- The key is stored only in the phone browser's `localStorage` and is cleared by **Esci** or by
  a 401/403 answer.
- Non-loopback callers still need the key for every `/api` call; `/m/` itself is public like the
  rest of the static UI.
- Do not expose the raw port. Reach it over a private network (Tailscale) or an SSH tunnel.

## Reach it from an iPhone (Tailscale)
Safari only offers install-to-home-screen, the service worker and dictation on **HTTPS**, so use
Tailscale's TLS front rather than the plain `100.x` address:

```bash
tailscale serve --bg https / http://127.0.0.1:47821
# then, so the Host/Origin of that name is accepted:
export JARVIS_TRUSTED_HOSTS=<machine>.<tailnet>.ts.net
```

Open `https://<machine>.<tailnet>.ts.net/m/`, paste the Control Key (Settings → API Keys →
Control Key), then Share → Add to Home Screen.

## What is and is not verified
- Verified: the files are served, the API stays locked for a remote peer, a Bearer key passes
  (`tests/unit/ui/web/test_mobile_panel.py`); in headless Chromium at an iPhone 13 viewport with
  a mocked API: login (bad and good key), chat list, approve, send with an attachment, stop.
- NOT verified: a real iPhone/Safari, Tailscale serve with the trusted-host setting above,
  dictation (`webkitSpeechRecognition`, it-IT, depends on iOS), the live agent behind the routes.
- Not built yet: live push (it polls every 1.5 s while a turn runs), notifications, answering
  agent questions and credential requests, downloading files Jarvis produces, creating the very
  first chat (use the desktop once).
