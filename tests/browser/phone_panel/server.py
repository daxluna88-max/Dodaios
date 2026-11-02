"""A real Dodaios web server with a seeded chat service, for the phone panel browser tests.

``python server.py <port>`` prints ``READY <data dir>`` once it listens. The Control Key is fixed
(``KEY``) so the browser script can mint pairing codes the way the owner's page does.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

KEY = "browser-test-control-key-1234"

port = int(sys.argv[1])
data = tempfile.mkdtemp(prefix="phone-browser-")
os.environ["JARVIS_CONTROL_API_KEY"] = KEY
os.environ["JARVIS_DATA_DIR"] = data

import uvicorn  # noqa: E402

from dodaios.agent_chat.service import AgentChatService  # noqa: E402
from dodaios.agent_chat.store import AgentChatStore  # noqa: E402
from dodaios.core.bus import EventBus  # noqa: E402
from dodaios.core.config import JarvisConfig  # noqa: E402
from dodaios.ui.web.server import WebServer  # noqa: E402

cfg = JarvisConfig()
cfg.memory.data_dir = os.path.join(data, "data")
cfg.ui.dev_mode = True
server = WebServer(cfg, bus=EventBus())
server.app.state.config = cfg
store = AgentChatStore(":memory:")
for title, text in (("Chat A", "segreto-di-A"), ("Chat B", "segreto-di-B")):
    session = store.create_session(
        provider="claude-code", title=title, surface="agent", model="", effort="", cwd=data
    )
    store.append_event(session.session_id, {"kind": "user_message", "payload": {"text": "ciao"}})
    store.append_event(
        session.session_id, {"kind": "assistant_text", "payload": {"text": text}}
    )
server.app.state.agent_chat = AgentChatService(store)
print("READY", data, flush=True)
uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="warning")
