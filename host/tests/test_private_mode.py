"""Private mode: Piblo Core makes no internet requests when it is on.

The proof test monkeypatches the socket entry points so any non-local
connect/DNS attempt fails the test, then boots the app and exercises every main
path. The static test rejects direct HTTP clients, unguarded WebSocket and
subprocess calls, and raw sockets outside the documented local allowlist.
"""
from __future__ import annotations

import asyncio
import ast
import concurrent.futures
import ipaddress
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from mazhost import private_mode
from mazhost.app import create_app
from mazhost.config import Settings
from mazhost.core import MazCore
from mazhost.errors import ErrorCode, RouteError
from mazhost.live_brain import LiveBrain
from mazhost.llm import Models
from mazhost.nudge import NudgeClient
from mazhost.selfupdate import SelfUpdater
from mazhost.telemetry import SystemTelemetry
from mazhost.agent_runner import AgentRunner
from mazhost.authority import AuthorityBroker
from mazhost.executor import ElevatedExecutor
from mazhost.live_voice import LiveBridge
from mazhost import remote
from mazhost.bridge import BridgeWorker
from mazhost.look import LookError, LookService

TOKEN = "test-token-that-is-not-default"


@pytest.fixture(autouse=True)
def _reset_private_mode():
    """Private mode is module-global state; never let one test leak it into another."""
    private_mode.set_enabled(False)
    yield
    private_mode.set_enabled(False)


# ---------------------------------------------------------------- is_local_url

@pytest.mark.parametrize(
    "url,expected",
    [
        # loopback
        ("127.0.0.1", True),
        ("http://127.0.0.1:8080/api/tags", True),
        ("localhost", True),
        ("http://localhost:8787", True),
        # RFC1918
        ("192.168.1.50", True),
        ("http://192.168.1.50", True),
        ("10.0.0.1", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        # link-local
        ("169.254.1.1", True),
        # mDNS
        ("foo.local", True),
        ("http://foo.local:11434", True),
        # IPv6 loopback + ULA
        ("::1", True),
        ("[::1]", True),
        ("http://[::1]:8080", True),
        ("fd00::1", True),
        ("http://[fd00::1]", True),
        # public / non-local
        ("8.8.8.8", False),
        ("http://8.8.8.8", False),
        ("172.32.0.1", False),  # just outside RFC1918
        ("example.com", False),
        ("http://example.com", False),
        ("https://api.groq.com", False),
    ],
)
def test_is_local_url(url, expected):
    assert private_mode.is_local_url(url) is expected


# ------------------------------------------------------------- static choke test


# Explicit local-only subprocess allowlist. Entries name a function, never a module.
LOCAL_PROCESSES = {
    ("beam.py", "BeamStore._copy_to_clipboard"),  # fixed Set-Clipboard script; text is stdin
    ("look.py", "LookService._screenshot"),  # fixed local screen capture script
    ("telemetry.py", "SystemTelemetry._gpu"),  # nvidia-smi hardware query
    ("teach_capture.py", "TeachCapture.audio_devices"),  # ffmpeg lists local DirectShow devices
    ("teach_capture.py", "TeachCapture.start"),  # ffmpeg captures desktop/audio to a local file
    ("teach_capture.py", "TeachCapture._extract_scene_frames"),  # local recorded file -> images
    ("teach_capture.py", "TeachCapture._transcribe"),  # local recording -> WAV
    ("subtitles.py", "SubtitleJobs._extract_audio"),  # validated local video -> WAV
    ("craft_agents.py", "_claude_agents"),  # fixed argv: cswap list --json (local usage cache)
    ("subtitles.py", "open_in_vlc"),  # validated local video/subtitle paths
}
LOCAL_SOCKETS = {
    ("core.py", "MazCore.status"),  # gethostname only; no DNS or connection
    ("core.py", "MazCore.context_for_prompt"),  # gethostname only
    ("discovery.py", "DiscoveryResponder.start"),  # binds inbound UDP LAN discovery
    ("discovery.py", "DiscoveryResponder._serve"),  # replies only after is_lan_ip(peer)
}


def _network_bypasses(source: str, filename: str) -> list[str]:
    """Resolve import aliases, then reject network/process calls without a guard."""
    tree = ast.parse(source)
    aliases, parents = {}, {}
    issues = []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
        if isinstance(node, ast.Import):
            for entry in node.names:
                aliases[entry.asname or entry.name.split(".")[0]] = entry.name if entry.asname else entry.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for entry in node.names:
                aliases[entry.asname or entry.name] = f"{node.module}.{entry.name}" if node.module else entry.name
            if node.module == "httpx":
                issues.append(f"{node.lineno}: from httpx import bypasses the client guard")

    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name(node.value) + "." + node.attr
        return ""

    def scope(node):
        parts, function = [], None
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parts.append(node.name)
                if function is None and not isinstance(node, ast.ClassDef):
                    function = node
        return ".".join(reversed(parts)), function

    def guards_before(node):
        # Only preceding unconditional statements in the same executable scope
        # can guard a call; a dead branch or an unused nested function cannot.
        guards = set()
        while node in parents:
            parent = parents[node]
            for _, value in ast.iter_fields(parent):
                if isinstance(value, list) and node in value:
                    for previous in value[:value.index(node)]:
                        if isinstance(previous, ast.Expr) and isinstance(previous.value, ast.Call):
                            call = name(previous.value.func)
                            if call.startswith(("private_mode.", "mazhost.private_mode.")):
                                guards.add(call.rsplit(".", 1)[-1])
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                break
            node = parent
        return guards

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = name(node.func)
        qualified, function = scope(node)
        key = (filename, qualified)
        guards = guards_before(node)
        bad = False
        if call.startswith("httpx."):
            bad = call.split(".")[-1] in {"Client", "AsyncClient", "get", "post", "request", "stream", "delete", "put", "patch", "head", "options"}
        elif call.startswith(("urllib.request.", "http.client.")):
            bad = True
        elif call.startswith("socket."):
            bad = key not in LOCAL_SOCKETS
        elif call.startswith("websockets.") and call.endswith(".connect"):
            bad = "guard" not in guards
        elif call.startswith(("subprocess.", "asyncio.create_subprocess", "os.system", "os.popen", "os.spawn")):
            if call.split(".")[-1] not in {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput", "create_subprocess_exec", "create_subprocess_shell", "system", "popen", "spawnl", "spawnv"}:
                continue
            bad = key not in LOCAL_PROCESSES and "guard_subprocess" not in guards
        if bad:
            issues.append(f"{node.lineno}: {call} in {qualified}")
    return issues


def test_no_unguarded_network_or_subprocess_calls():
    mazhost = Path(__file__).resolve().parents[1] / "mazhost"
    offenders = {str(path.relative_to(mazhost)): issues
                 for path in sorted(mazhost.rglob("*.py")) if path.name != "private_mode.py"
                 if (issues := _network_bypasses(path.read_text(encoding="utf-8"), path.name))}
    assert not offenders, f"Network/private-mode guard bypasses: {offenders}"


@pytest.mark.parametrize("source", [
    "import httpx as h\nh.get('https://example.com')",
    "from httpx import Client as C\nC()",
    "from websockets.asyncio.client import connect as dial\ndial('wss://example.com')",
    "import urllib.request as r\nr.urlopen('https://example.com')",
    "from urllib.request import urlopen\nurlopen('https://example.com')",
    "import socket as s\ns.create_connection(('example.com', 443))",
    "from socket import socket\nsocket()",
    "import subprocess as s\ns.run(['gh', 'pr', 'list'])",
    "from subprocess import Popen\nPopen(['claude'])",
    "import asyncio\nasyncio.create_subprocess_exec('gh')",
    "import subprocess\nfrom . import private_mode\ndef f():\n if False: private_mode.guard_subprocess('x')\n subprocess.run(['gh'])",
    "from websockets.asyncio.client import connect\nfrom . import private_mode\ndef f():\n def unused(): private_mode.guard('wss://example.com')\n connect('wss://example.com')",
])
def test_static_check_catches_bypasses(source):
    assert _network_bypasses(source, "new_module.py")


# ------------------------------------------------------------------- helpers


class _FakeStt:
    def available(self) -> bool:
        return True

    def transcribe(self, _path) -> str:
        return "hi"

    def transcribe_prompted(self, _path, _prompt) -> str:
        return "hi"

    def transcribe_turn(self, _path) -> str:
        return "hi"


class _FakePC:
    available = True

    def perform(self, action: str):
        return SimpleNamespace(action=action, label=f"did {action}")


def _local_llm(request: httpx.Request) -> httpx.Response:
    if "/health" in str(request.url):
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(
        200, json={"model": "lfm3", "choices": [{"message": {"content": "local says hi"}}]}
    )


def _benign(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


def _forbidden(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected outbound request: {request.url}")


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        token=TOKEN,
        private_mode=True,
        data_dir=str(tmp_path),
        _env_file=None,
        local_engine="llamacpp",
        llamacpp_url="http://127.0.0.1:8080",
        llamacpp_model="lfm3",
        llamacpp_backup_model="",
        discovery_port=0,
        remote_detect=False,
    )
    base.update(overrides)
    return Settings(**base)


def _block_nonlocal(monkeypatch) -> None:
    """Fail the test the moment any code resolves/connects to a non-local address."""
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def is_local(host) -> bool:
        h = str(host).split("%")[0]
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            low = h.lower().rstrip(".")
            return low == "localhost" or low.endswith(".local")
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        return bool(ip.is_loopback or ip.is_private or ip.is_link_local)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        if not is_local(host):
            raise AssertionError(f"non-local getaddrinfo: {host}")
        return real_getaddrinfo(host, port, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if not is_local(host):
            raise AssertionError(f"non-local connect: {host}")
        return real_connect(self, address, *args, **kwargs)

    def guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if not is_local(host):
            raise AssertionError(f"non-local create_connection: {host}")
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


# ------------------------------------------------------------------ proof test


def test_private_mode_blocks_every_outbound_path(monkeypatch, tmp_path):
    _block_nonlocal(monkeypatch)
    cfg = _settings(tmp_path, bridge_enabled=True, bridge_repo="test/repo", cloud_key="test-key")
    private_mode.init(cfg)  # enabled from settings.private_mode=True

    models = Models(cfg, client=private_mode.client(transport=httpx.MockTransport(_local_llm)))
    core = MazCore(cfg, client=private_mode.client(transport=httpx.MockTransport(_benign)))
    telemetry = SystemTelemetry(cfg, client=private_mode.client(transport=httpx.MockTransport(_benign)))
    nudge = NudgeClient(cfg, client=private_mode.client(transport=httpx.MockTransport(_benign)))
    updater = SelfUpdater(enabled=True, http=private_mode.client(transport=httpx.MockTransport(_forbidden)))
    bridge = BridgeWorker(cfg, core)

    app = create_app(
        cfg,
        stt=_FakeStt(),
        models=models,
        core=core,
        telemetry=telemetry,
        nudge=nudge,
        pc=_FakePC(),
        updater=updater,
        bridge=bridge,
    )

    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {TOKEN}"}

        # health: the device/status poll payload carries the flag
        health = c.get("/health", headers=h)
        assert health.status_code == 200
        assert health.json()["private_mode"] is True

        # chat/ask via a fake local engine (MockTransport), local-only routing
        sid = c.post("/session/start", headers=h).json()["session_id"]
        turn = c.post("/turn/text", headers=h, json={"session_id": sid, "route": "auto", "text": "hi"})
        assert turn.status_code == 200
        assert turn.json()["provider"] == "local:lfm3"
        assert turn.json()["reply"] == "local says hi"

        # an explicit cloud route is refused with a clear code, never dialled
        blocked = c.post(
            "/turn/text", headers=h,
            json={"session_id": sid, "route": "cloud", "text": "hi"},
        )
        assert blocked.status_code == 403
        assert blocked.json()["error"] == ErrorCode.PRIVATE_MODE.value

        # needs queue (its `gh pr list` must skip, not dial GitHub)
        assert c.get("/needs", headers=h).status_code == 200

        # Even a bearer caller with a launch request cannot start a cloud CLI.
        agent = c.post("/agents/run", headers=h, json={
            "provider": "claude", "prompt": "hi", "project": "test", "grant_token": "x" * 30,
        })
        assert agent.status_code == 403
        assert agent.json()["error"] == "PRIVATE_MODE"
        shell = c.post("/authority/execute", headers=h, json={
            "command": "curl https://example.com", "grant_token": "x" * 30,
        })
        assert shell.status_code == 403
        assert shell.json()["error"] == "PRIVATE_MODE"

        # Look's router is not installed by create_app; exercise its real service.
        with pytest.raises(LookError, match="private_mode"):
            LookService(cfg).look("What is here?")

        # Library saves URLs as references; it must not fetch the linked page.
        saved = c.post("/ingest", headers=h, json={"kind": "url", "url": "https://example.com/private-proof"})
        assert saved.status_code == 200
        assert c.get("/library/search", headers=h, params={"q": "example.com"}).status_code == 200
        assert c.get("/library/stats", headers=h).status_code == 200

        # Exercise the real WebSocket endpoint with a non-local model URL.
        cfg.llamacpp_url = "https://example.com"
        with c.websocket_connect("/voice/live", headers=h) as ws:
            assert ws.receive_json()["type"] == "error"
        cfg.llamacpp_url = "http://127.0.0.1:8080"

        assert bridge.poll_once() == 0

    # self-update tick: skipped cleanly, no GitHub call
    result = updater.check()
    assert result["last_result"] == "skipped_private_mode"

    # telemetry tick: local-only probes (mocked here), no crash
    assert telemetry.snapshot()["ok"] is True

    # live brain model list: OpenRouter fetch is skipped in private mode
    assert asyncio.run(LiveBrain(cfg)._models()) == []


def test_private_mode_off_keeps_cloud_route(monkeypatch, tmp_path):
    """When private mode is off, an explicit cloud route still works (guard is a no-op)."""
    cfg = _settings(tmp_path, private_mode=False)
    private_mode.init(cfg)

    def cloud(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "cloud-model", "choices": [{"message": {"content": "cloud says hi"}}]})

    models = Models(
        Settings(
            token=TOKEN, private_mode=False, _env_file=None,
            cloud_key="k", cloud_url="https://api.example.com/v1", cloud_model="cloud-model",
        ),
        client=httpx.Client(transport=httpx.MockTransport(cloud)),
    )
    text, provider = models.chat([{"role": "user", "content": "hi"}], "cloud")
    assert text == "cloud says hi"
    assert provider == "cloud"


def test_no_local_engine_is_a_clear_error():
    cfg = _settings(Path("unused"), llamacpp_model="", llamacpp_backup_model="")
    private_mode.set_enabled(True)
    m = Models(cfg)
    with pytest.raises(RouteError) as caught:
        m.chat([{"role": "user", "content": "hi"}], "auto")
    assert caught.value.code == ErrorCode.PRIVATE_MODE


# -------------------------------------------------------------- privacy routes


@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.parametrize("url", ["http://127.0.0.1:5000", "http://192.168.1.50", "http://printer.local"])
def test_maker_http_rechecks_private_mode(tmp_path, monkeypatch, injected, url):
    from mazhost.maker import OctoPrintSource

    key = tmp_path / "printer-key"
    key.write_text("test-printer-key")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "HTTPTransport", lambda: transport)
    client = httpx.Client(transport=transport) if injected else None
    source = OctoPrintSource(url, str(key), client)
    private_mode.set_enabled(True)
    assert source._request("GET", "/api/printer").status_code == 200
    source.send("pause")
    assert len(requests) == 2

    source.url = "https://example.com"
    for method in ("GET", "POST"):
        with pytest.raises(private_mode.PrivateModeError):
            source._request(method, "/api/job")
    assert len(requests) == 2

    private_mode.set_enabled(False)
    assert source._request("GET", "/api/job").status_code == 200
    private_mode.set_enabled(True)
    with pytest.raises(private_mode.PrivateModeError):
        source.send("cancel")
    assert len(requests) == 3
    if client:
        client.close()


def test_maker_local_tools_work_in_private_mode(tmp_path, monkeypatch):
    import json
    from mazhost.accessories import Accessories
    from mazhost.maker import EnderForgeSource

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "openscad":
            Path(args[2]).write_text("solid fixture\nendsolid fixture\n")
            return subprocess.CompletedProcess(args, 0)
        assert args in (["ef", "status", "--json"], ["ef", "next", "--json"])
        return subprocess.CompletedProcess(args, 0, json.dumps({"connection": "Operational", "next": None}))

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("shutil.which", lambda _: "openscad")
    private_mode.set_enabled(True)
    source = EnderForgeSource("ef")
    assert source.status()["state"] == "idle"
    assert source.next_step()["next"] is None
    assert Accessories(tmp_path).make("desk stand")["format"] == "stl"
    assert len(calls) == 3


def test_privacy_endpoint_flips_and_reports(tmp_path, monkeypatch):
    import hashlib
    import hmac
    key = "privacy-test-device-key"
    monkeypatch.setenv("MAZ_ASK_DEVICE_KEY", key)
    cfg = _settings(tmp_path, private_mode=False)
    private_mode.init(cfg)
    app = create_app(cfg, stt=_FakeStt(), pc=_FakePC())
    h = {"Authorization": f"Bearer {TOKEN}"}

    with TestClient(app) as c:
        assert c.get("/privacy").status_code == 401  # bearer-protected like the rest

        off = c.get("/privacy", headers=h)
        assert off.status_code == 200
        assert off.json()["private_mode"] is False
        assert off.json()["outbound"] == "cloud_optional"

        on = c.post("/privacy", headers=h, json={"private_mode": True})
        assert on.status_code == 200
        assert on.json()["private_mode"] is True
        assert on.json()["outbound"] == "none"
        assert on.json()["cloud_services_in_use"] == []

        # persisted: a fresh init from the same data dir still reports private
        private_mode.init(cfg)
        assert private_mode.enabled() is True
        assert c.get("/privacy", headers=h).json()["private_mode"] is True

        with concurrent.futures.ThreadPoolExecutor() as pool:
            back = pool.submit(c.post, "/privacy", headers=h, json={"private_mode": False})
            import time
            deadline = time.monotonic() + 5
            while not (pending := c.get("/buddy/pending", headers=h).json().get("pending")):
                assert time.monotonic() < deadline, "disable must request Piblo approval"
                time.sleep(0.01)
            assert private_mode.enabled() is True
            assert pending[0]["category"] == "other"
            item = pending[0]
            message = f"{item['id']}:{item['digest']}:allow:{item['nonce']}".encode()
            proof = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
            decision = c.post("/buddy/decide", headers=h, json={"id": item["id"], "decision": "allow", "proof": proof})
            assert decision.status_code == 200
            assert back.result(timeout=5).json()["private_mode"] is False


@pytest.mark.parametrize("provider", ["claude", "codex", "hermes"])
def test_private_blocks_agent_before_any_cli(tmp_path, monkeypatch, provider):
    cfg = _settings(tmp_path, control_dir=str(tmp_path / "control"))
    runner = AgentRunner(cfg, AuthorityBroker(cfg))
    private_mode.set_enabled(True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("launched a subprocess"))
    with pytest.raises(RuntimeError, match="PRIVATE_MODE"):
        runner.run_sync(provider=provider, prompt="hi", project=str(tmp_path), grant_token="invalid")
    with pytest.raises(RuntimeError, match="PRIVATE_MODE"):
        runner.start(provider=provider, prompt="hi", project=str(tmp_path), grant_token="invalid")
    with pytest.raises(RuntimeError, match="PRIVATE_MODE"):
        runner._help(provider)


def test_private_blocks_shell_and_remote(tmp_path, monkeypatch):
    cfg = _settings(tmp_path, control_dir=str(tmp_path / "control"))
    executor = ElevatedExecutor(cfg, AuthorityBroker(cfg))
    private_mode.set_enabled(True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("launched a subprocess"))
    with pytest.raises(RuntimeError, match="PRIVATE_MODE"):
        executor.run(grant_token="invalid", command="curl https://example.com")
    with pytest.raises(private_mode.PrivateModeError, match="PRIVATE_MODE"):
        remote._run_json("tailscale", ["status", "--json"])


def test_live_connect_rechecks_private_mode(tmp_path, monkeypatch):
    bridge = LiveBridge(None, _settings(tmp_path, private_mode=False, llamacpp_url="https://example.com"))
    private_mode.set_enabled(True)
    _block_nonlocal(monkeypatch)
    with pytest.raises(private_mode.PrivateModeError):
        asyncio.run(bridge._connect())


def test_live_private_mode_never_follows_redirect_to_public_host(tmp_path):
    from websockets.exceptions import InvalidStatus
    async def run():
        async def redirect(reader, writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 302 Found\r\nLocation: ws://203.0.113.1:8765/\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(redirect, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        loop = asyncio.get_running_loop()
        original = loop.create_connection
        destinations = []
        async def tracked(factory, host=None, port=None, **kwargs):
            destinations.append(host)
            if host != '127.0.0.1':
                raise OSError('public socket blocked by test')
            return await original(factory, host, port, **kwargs)
        loop.create_connection = tracked
        private_mode.set_enabled(True)
        try:
            bridge = LiveBridge(None, _settings(tmp_path, llamacpp_url=f'http://127.0.0.1:{port}'))
            with pytest.raises(InvalidStatus):
                await bridge._connect()
            assert destinations == ['127.0.0.1']
        finally:
            loop.create_connection = original
            server.close()
            await server.wait_closed()
    asyncio.run(run())


def test_private_blocks_updater_subprocesses(tmp_path, monkeypatch):
    updater = SelfUpdater(repo_dir=str(Path(__file__).resolve().parents[2]))
    private_mode.set_enabled(True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("launched a subprocess"))
    for args in (["fetch", "origin"], ["ls-remote", "origin"]):
        with pytest.raises(private_mode.PrivateModeError, match="PRIVATE_MODE"):
            updater._git(args)
    with pytest.raises(private_mode.PrivateModeError, match="PRIVATE_MODE"):
        updater._run_pip()


@pytest.mark.parametrize("decision", ["deny", "cancel"])
def test_privacy_disable_refusal_stays_private(tmp_path, decision):
    cfg = _settings(tmp_path)
    app = create_app(cfg, stt=_FakeStt(), pc=_FakePC())
    h = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c, concurrent.futures.ThreadPoolExecutor() as pool:
        back = pool.submit(c.post, "/privacy", headers=h, json={"private_mode": False})
        import time
        deadline = time.monotonic() + 5
        while not (pending := c.get("/buddy/pending", headers=h).json()["pending"]):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        c.post("/buddy/decide", headers=h, json={"id": pending[0]["id"], "decision": decision})
        assert back.result(timeout=5).status_code == 403
        assert private_mode.enabled()
        assert c.get("/privacy", headers=h).json()["private_mode"]


def test_privacy_disable_expired_stays_private(tmp_path, monkeypatch):
    monkeypatch.setattr("mazhost.buddy.TIMEOUT_S", 0)
    cfg = _settings(tmp_path)
    with TestClient(create_app(cfg, stt=_FakeStt(), pc=_FakePC())) as c:
        back = c.post("/privacy", headers={"Authorization": f"Bearer {TOKEN}"}, json={"private_mode": False})
        assert back.status_code == 403
        private_mode.init(cfg)
        assert private_mode.enabled()


def test_private_allows_read_only_git(tmp_path):
    private_mode.set_enabled(True)
    core = MazCore(_settings(tmp_path))
    result = core._run(["git", "rev-parse", "--is-inside-work-tree"], Path(__file__).resolve().parents[2], 5)
    assert result["ok"]
    assert result["output"].strip() == "true"


def test_private_blocks_project_builds(tmp_path, monkeypatch):
    private_mode.set_enabled(True)
    core = MazCore(_settings(tmp_path))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("launched a subprocess"))
    with pytest.raises(private_mode.PrivateModeError, match="PRIVATE_MODE"):
        core._run(["npm", "run", "build"], tmp_path, 5)


def test_private_git_status_never_runs_repository_fsmonitor(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f'#!/bin/sh\necho invoked > "{marker.as_posix()}"\n', encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", hook.as_posix()], cwd=repo, check=True)
    # Establish that this hook actually runs without the private-mode override.
    subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True)
    assert marker.exists()
    marker.unlink()
    private_mode.set_enabled(True)
    assert MazCore(_settings(tmp_path))._run(["git", "status", "--porcelain"], repo, 5)["ok"]
    updater = SelfUpdater(repo_dir=str(repo))
    updater._git(["status", "--porcelain", "--untracked-files=no"])
    assert not marker.exists()


def test_bridge_survives_private_mode_switch(tmp_path, monkeypatch):
    cfg = _settings(tmp_path, bridge_enabled=True)
    bridge = BridgeWorker(cfg, MazCore(cfg))
    private_mode.set_enabled(False)
    monkeypatch.setattr("mazhost.bridge.shutil.which", lambda _: "gh")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("launched gh"))

    def switched_headers():
        private_mode.set_enabled(True)
        return bridge._token()

    monkeypatch.setattr(bridge, "_headers", switched_headers)
    monkeypatch.setattr(bridge._stop, "wait", lambda _: bridge._stop.set())
    bridge._loop()
    assert "PRIVATE_MODE" in bridge.status()["last_error"]


def test_voice_save_cannot_clear_concurrent_private_mode(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from mazhost.voices import VoiceService, ADRIAN_ID
    cfg = _settings(tmp_path, private_mode=False)
    voice = VoiceService(cfg)
    read_done, resume = Event(), Event()
    read = voice._read
    def delayed_read():
        data = read()
        read_done.set()
        assert resume.wait(5)
        return data
    monkeypatch.setattr(voice, '_read', delayed_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(voice.select, ADRIAN_ID)
        assert read_done.wait(5)
        pool.submit(private_mode.save, cfg, True).result(timeout=5)
        resume.set()
        saving.result(timeout=5)
    private_mode.init(cfg)
    assert private_mode.enabled()
