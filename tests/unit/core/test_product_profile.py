"""P4 of the 2026-10-26 work order: Work and Consumer are two independent products."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from dodaios.core import product_profile as pp


@pytest.fixture
def shared(tmp_path: Path) -> Path:
    return tmp_path / ".dodaios-data"


def test_each_product_has_its_own_directory_config_and_credential_service(shared):
    work, consumer = pp.paths_for("work", shared), pp.paths_for("consumer", shared)
    assert work.data_dir != consumer.data_dir
    assert work.config_file != consumer.config_file
    assert {work.keyring_service, consumer.keyring_service} == {"dodaios-work", "dodaios-consumer"}
    for paths in (work, consumer):
        assert shared not in paths.data_dir.parents and paths.data_dir != shared
    pp.check_no_overlap(shared)


def test_unknown_product_is_an_error_never_a_default(shared):
    for bad in (None, "", "Consumer2", "both", "../work"):
        with pytest.raises(pp.ProductError):
            pp.paths_for(bad, shared)  # type: ignore[arg-type]


def test_overlap_detection(tmp_path):
    root = tmp_path / "a"
    assert pp._overlaps(root, root)
    assert pp._overlaps(root, root / "inner")
    assert pp._overlaps(root / "inner", root)
    assert not pp._overlaps(tmp_path / "a", tmp_path / "a-work")  # a prefix is not a parent


def test_claim_is_idempotent_and_survives_an_upgrade(shared):
    paths = pp.paths_for("work", shared)
    pp.claim(paths)
    pp.claim(paths)  # a second start, or an upgraded build reusing the directory
    assert json.loads(paths.marker.read_text())["product"] == "work"


def test_a_directory_claimed_by_the_other_product_is_refused(shared):
    work = pp.paths_for("work", shared)
    pp.claim(work)
    impostor = pp.ProductPaths("consumer", work.data_dir, work.config_file, "dodaios-consumer")
    with pytest.raises(pp.ProductConflict):
        pp.claim(impostor)
    assert json.loads(work.marker.read_text())["product"] == "work"


@pytest.mark.parametrize(
    "content", ["", "not json", "[]", '{"product": "other"}', '{"product": 1}']
)
def test_a_bad_marker_is_not_adopted(shared, content):
    paths = pp.paths_for("consumer", shared)
    paths.data_dir.mkdir(parents=True)
    paths.marker.write_text(content)
    with pytest.raises(pp.ProductError):
        pp.claim(paths)
    assert paths.marker.read_text() == content


def test_racing_first_claims_have_one_winner(shared):
    outcomes: list[str] = []
    barrier = threading.Barrier(8)

    def go(product: str) -> None:
        paths = pp.paths_for(product, shared) if product == "work" else pp.ProductPaths(
            "consumer", pp.paths_for("work", shared).data_dir, Path("c.toml"), "dodaios-consumer")
        barrier.wait()
        try:
            pp.claim(paths)
            outcomes.append(product)
        except pp.ProductConflict:
            outcomes.append("refused-" + product)

    threads = [threading.Thread(target=go, args=("work" if i % 2 else "consumer",))
               for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    winners = {o for o in outcomes if not o.startswith("refused")}
    assert len(winners) == 1, outcomes


def test_same_product_cannot_launch_twice_but_both_products_can_run_together(shared):
    first, lock_first = pp.prepare_launch("work", shared)
    with pytest.raises(pp.AlreadyRunning):
        pp.prepare_launch("work", shared)
    other, lock_other = pp.prepare_launch("consumer", shared)  # the other product is unaffected
    assert other.data_dir != first.data_dir
    lock_first.release()
    pp.prepare_launch("work", shared)[1].release()  # after exit it can start again
    lock_other.release()


def test_environment_scopes_children_to_the_product(shared):
    paths = pp.paths_for("consumer", shared)
    env = pp.environment(paths, {"PATH": "/bin", "JARVIS_KEYRING_SERVICE": "dodaios-work"})
    assert env["JARVIS_PRODUCT"] == "consumer"
    assert env["JARVIS_DATA_DIR"] == str(paths.data_dir)
    assert env["JARVIS_CONFIG"] == str(paths.config_file)
    assert env["JARVIS_KEYRING_SERVICE"] == "dodaios-consumer"
    assert env["PATH"] == "/bin"


@pytest.mark.parametrize("value, expected", [("dodaios-work", "dodaios-work"),
                                              ("dodaios-consumer", "dodaios-consumer"),
                                              ("dodaios", None), ("other-app", None), ("", None)])
def test_scoped_service_accepts_only_the_two_product_services(value, expected):
    assert pp.scoped_service({"JARVIS_KEYRING_SERVICE": value}) == expected


def _branding(env_value: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "JARVIS_KEYRING_SERVICE"}
    if env_value is not None:
        env["JARVIS_KEYRING_SERVICE"] = env_value
    code = ("import json; from dodaios.core import branding as b; "
            "print(json.dumps([b.KEYRING_SERVICE_NAME, list(b.LEGACY_KEYRING_SERVICE_NAMES)]))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,  # noqa: S603
                         text=True, timeout=60, check=True)
    service, legacy = json.loads(out.stdout)
    return {"service": service, "legacy": legacy}


def test_a_product_credential_service_never_falls_back_to_the_shared_vault():
    shared_vault = _branding(None)
    assert shared_vault["service"] == "dodaios" and shared_vault["legacy"]  # unchanged default
    for service in ("dodaios-work", "dodaios-consumer"):
        scoped = _branding(service)
        assert scoped == {"service": service, "legacy": []}
    assert _branding("someone-elses-service")["service"] == "dodaios"  # arbitrary names ignored


_PROBE = (
    "import os, sys; from dodaios.ui.web import launcher as l; "
    "err, lock = l._select_product_early(sys.argv[1:]); "
    "print(repr(err)); print(os.environ.get('JARVIS_PRODUCT')); "
    "print(os.environ.get('JARVIS_DATA_DIR')); print(os.environ.get('JARVIS_KEYRING_SERVICE')); "
    "from dodaios.core import config as c; "
    "print(c.KEYRING_SERVICE, list(c.LEGACY_KEYRING_SERVICES)); "
    "sys.stdout.flush(); "
    "sys.stdin.readline()"  # keep the process (and its launch lock) alive until told to exit
)


def _launch(home: Path, *args: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("JARVIS_") and k not in ("LOCALAPPDATA",)}
    env["HOME"] = str(home)
    return subprocess.Popen([sys.executable, "-c", _PROBE, *args], env=env,  # noqa: S603
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


def _read(proc: subprocess.Popen) -> list[str]:
    return [proc.stdout.readline().strip() for _ in range(5)]


def test_launcher_flag_pins_the_product_and_blocks_a_second_launch_of_it(tmp_path):
    work = _launch(tmp_path, "--product", "work")
    other = second = None
    try:
        err, product, data_dir, service, bound = _read(work)
        assert err == "None" and product == "work" and service == "dodaios-work"
        assert bound == "dodaios-work []", "the config module binds the product vault, no fallback"
        assert data_dir.endswith("-work") and Path(data_dir, ".product").is_file()
        second = _launch(tmp_path, "--product=work")  # same product again: refused
        assert "already running" in _read(second)[0]
        other = _launch(tmp_path, "--product", "consumer")  # the other product runs alongside
        assert _read(other)[0] == "None"
    finally:
        for proc in (work, second, other):
            if proc is not None:
                proc.communicate("\n", timeout=30)


def test_launcher_rejects_an_unknown_product(tmp_path):
    proc = _launch(tmp_path, "--product", "enterprise")
    out = _read(proc)
    proc.communicate("\n", timeout=30)
    assert "unknown product" in out[0]


# -- the real public entry point: ``python -m dodaios --product ...`` (clean subprocesses) --------

_ENTRY = (
    "import sys, runpy, json, os\n"
    "sys.argv = ['dodaios', *sys.argv[1:]]\n"
    "code = None\n"
    "try:\n"
    "    held = runpy.run_module('dodaios.__main__', run_name='__main__')\n"
    "except SystemExit as exc:\n"
    "    code = exc.code\n"
    "    held = None\n"
    "cfg = sys.modules.get('dodaios.core.config')\n"
    "print('STATE ' + json.dumps({\n"
    "    'code': code, 'argv': sys.argv[1:],\n"
    "    'service': cfg.KEYRING_SERVICE if cfg else None,\n"
    "    'legacy': list(cfg.LEGACY_KEYRING_SERVICES) if cfg else None,\n"
    "    'data': os.environ.get('JARVIS_DATA_DIR'), 'product': os.environ.get('JARVIS_PRODUCT'),\n"
    "    'active': os.environ.get('JARVIS_PRODUCT_ACTIVE'),\n"
    "    'env': {k: v for k, v in os.environ.items() if k.startswith('JARVIS_')}}), flush=True)\n"
    "if os.environ.get('HOLD'):\n"
    "    sys.stdin.readline()\n"
)


def _entry_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("JARVIS_") and k != "LOCALAPPDATA"}
    env["HOME"] = str(home)
    env.update(extra or {})
    return env


def _entry(home: Path, *args: str, extra: dict[str, str] | None = None, hold: bool = False):
    env = _entry_env(home, ({"HOLD": "1"} if hold else {}) | (extra or {}))
    proc = subprocess.Popen([sys.executable, "-c", _ENTRY, *args], env=env,  # noqa: S603
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    if hold:
        line = ""
        while not line.startswith("STATE ") and proc.poll() is None:
            line = proc.stdout.readline()
        return proc, json.loads(line[6:]) if line.startswith("STATE ") else None
    out, err = proc.communicate(timeout=120)
    states = [ln for ln in out.splitlines() if ln.startswith("STATE ")]
    return json.loads(states[-1][6:]) if states else None, err


@pytest.mark.parametrize("product", ["work", "consumer"])
def test_public_entry_point_accepts_the_flag_and_scopes_credentials(tmp_path, product):
    state, err = _entry(tmp_path, "--product", product, "--version")
    assert state is not None, err[-500:]
    assert state["code"] == 0 and state["argv"] == ["--version"], "flag consumed, not re-parsed"
    assert state["service"] == f"dodaios-{product}" and state["legacy"] == []
    assert state["data"].endswith(f"-{product}") and state["data"].count(f"-{product}") == 1
    assert state["product"] == product and state["active"] == product
    assert Path(state["data"], ".product").is_file()


def test_public_entry_point_rejects_bad_selections(tmp_path):
    for args, text in [(("--product", "enterprise", "--version"), "unknown product"),
                       (("--product", "--version"), "needs a value"),
                       (("--product", "work", "--product=consumer", "--version"), "twice")]:
        state, err = _entry(tmp_path, *args)
        assert state is None or state["code"] == 2
        assert text in err, (args, err[-300:])


def test_entry_point_without_the_flag_is_unscoped_and_unchanged(tmp_path):
    state, _ = _entry(tmp_path, "--version")
    assert state["service"] == "dodaios" and state["legacy"] and state["active"] is None


def test_second_launch_of_a_product_is_refused_and_the_other_product_runs(tmp_path):
    first, state = _entry(tmp_path, "--product", "work", "--version", hold=True)
    try:
        assert state is not None
        refused, err = _entry(tmp_path, "--product", "work", "--version")
        assert refused is None or refused["code"] == 2
        assert "already running" in err
        other, _ = _entry(tmp_path, "--product", "consumer", "--version")
        assert other["code"] == 0
    finally:
        first.communicate("\n", timeout=60)


def test_a_child_inherits_the_product_without_a_second_suffix_or_lock(tmp_path):
    parent, state = _entry(tmp_path, "--product", "work", "--version", hold=True)
    try:
        inherited = state["env"]
        # detach / relaunch / headless children re-enter with the parent's environment while it
        # still holds the launch lock: they must neither compete for it nor re-derive the paths
        for args in (("--version",), ("--product", "work", "--version")):
            child, err = _entry_env_run(tmp_path, inherited, *args)
            assert child is not None and child["code"] == 0, err[-400:]
            assert child["data"] == state["data"], "the product directory is reused as is"
            assert child["service"] == "dodaios-work" and child["legacy"] == []
        clash, err = _entry_env_run(tmp_path, inherited, "--product", "consumer", "--version")
        assert (clash is None or clash["code"] == 2) and "contradicts" in err
        broken = {k: v for k, v in inherited.items() if k != "JARVIS_DATA_DIR"}
        incomplete, err = _entry_env_run(tmp_path, broken, "--version")
        assert (incomplete is None or incomplete["code"] == 2) and "incomplete" in err
    finally:
        parent.communicate("\n", timeout=60)


def _entry_env_run(home: Path, inherited: dict[str, str], *args: str):
    env = _entry_env(home, {k: v for k, v in inherited.items() if k != "HOLD"})
    out = subprocess.run([sys.executable, "-c", _ENTRY, *args], env=env,  # noqa: S603
                         capture_output=True, text=True, timeout=120, check=False)
    states = [ln for ln in out.stdout.splitlines() if ln.startswith("STATE ")]
    return (json.loads(states[-1][6:]) if states else None), out.stderr
