from mazhost.command_router import CommandRouter, RouteResult


class FakeResponse:
    def __init__(self, content: str, ok: bool = True):
        self._content = content
        self.ok = ok

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("down")

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class FakeClient:
    def __init__(self, content: str | None = None, fail: bool = False):
        self.content = content
        self.fail = fail
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.fail:
            raise RuntimeError("ollama down")
        return FakeResponse(self.content or "{}", ok=not self.fail)


def test_tier0_pc_action():
    router = CommandRouter(ollama_url="http://x:11434", model="qwen3.5:4b", client=FakeClient())
    res = router.route("volume up")
    assert res.tier == "tier-0"
    assert res.capability == "pc.volume_up"
    assert res.confidence == 1.0


def test_tier0_reminder():
    router = CommandRouter(ollama_url="http://x:11434", model="qwen3.5:4b", client=FakeClient())
    res = router.route("remind me in 10 minutes to drink water")
    assert res.tier == "tier-0"
    assert res.capability == "reminder.create"
    assert res.args["title"] == "drink water"
    assert res.args["delay_seconds"] == 600


def test_tier1_selects_capability():
    router = CommandRouter(
        ollama_url="http://x:11434", model="qwen3.5:4b", client=FakeClient(
            content='{"intent":"check system","capability":"system.status","args":{},"confidence":0.9}'),
        capabilities=[{"id": "system.status", "description": "host status"}],
    )
    res = router.route("how is the pc doing")
    assert res.tier == "tier-1"
    assert res.capability == "system.status"
    assert res.confidence == 0.9


def test_tier1_model_down_is_unclear():
    router = CommandRouter(ollama_url="http://x:11434", model="qwen3.5:4b", client=FakeClient(fail=True))
    res = router.route("open chrome")
    assert res.tier == "unclear"
    assert res.capability is None


def test_tier1_bad_json_is_unclear():
    router = CommandRouter(
        ollama_url="http://x:11434", model="qwen3.5:4b",
        client=FakeClient(content="not json at all"),
    )
    res = router.route("open chrome")
    assert res.tier == "unclear"


def test_tier1_null_capability_is_unclear():
    router = CommandRouter(
        ollama_url="http://x:11434", model="qwen3.5:4b",
        client=FakeClient(content='{"intent":"chatting","capability":null,"args":{},"confidence":0.3}'),
    )
    res = router.route("hello there")
    assert res.tier == "unclear"


def test_route_without_model_never_calls_local_model():
    client = FakeClient()
    router = CommandRouter(ollama_url="http://localhost:11434", model="m", client=client)
    result = router.route("write the quarterly summary", model=False)
    assert result.tier == "unclear" and client.calls == []
    assert router.route("volume up", model=False).tier == "tier-0"
