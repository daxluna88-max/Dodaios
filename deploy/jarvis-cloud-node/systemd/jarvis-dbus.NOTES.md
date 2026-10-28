# Session D-Bus notes (DEFINITION - NOT RUN)

Why a session bus at all: `cu/direct.py` (computer use) and AT-SPI accessibility
trees need a session bus; Chromium logs warnings and some portals fail without
one (ADR-0065 D8).

Chosen: `jarvis-dbus.service`, a system unit that runs
`dbus-daemon --session --address=unix:path=/run/jarvis/bus` as `jarvis`.
Every other jarvis-* unit sets `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/jarvis/bus`
via `/etc/jarvis/node.env`. `/run/jarvis` is 0700 jarvis:jarvis, so nothing
else (caddy, sshd users) can talk to the bus. The unit is `IPAddressDeny=any`
and `AF_UNIX` only.

Alternative (not chosen): `loginctl enable-linger jarvis` + `systemd --user`
units with `dbus-user-session`. Pros: standard desktop layout, `systemd --user`
owns the bus. Cons: needs a PAM/logind session for `jarvis`, user units cannot
use several system-only sandboxing directives (PrivateDevices/ProtectSystem are
available but CPUQuota/MemoryMax need cgroup delegation), and the bus path
depends on `/run/user/<uid>` which is created by logind.

AT-SPI: `at-spi2-core` is installed; `at-spi-bus-launcher` is started on demand
by the toolkit through this bus (activation needs the `.service` files from
`/usr/share/dbus-1/services`, which ARE read by a session dbus-daemon started
without `--config-file`; TODO(verify) with `dbus-daemon --session --print-address`
on a test image - never done).

The secret-service API is NOT provided on this bus on purpose (no
gnome-keyring): `core/config.py::_ensure_keyring_backend` falls back to the file
backend, and a Secret Service the agent could unlock would be a second secret
path readable by the agent.
