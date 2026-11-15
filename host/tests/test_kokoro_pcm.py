import http.client
import threading

import numpy as np

from mazhost.kokoro_pcm import RATE, make_server


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    return r, r.read()


def _serve(synth):
    srv = make_server(synth, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_pcm_body_http10_no_chunking():
    calls = []

    def fake(sentence):
        calls.append(sentence)
        return np.full(RATE // 10, 0.5, dtype=np.float32)

    srv = _serve(fake)
    try:
        r, body = _get(srv.server_port, "/?text=Hello%20there.%20How%20are%20you%3F")
    finally:
        srv.shutdown()
    assert r.status == 200 and r.version == 10
    assert r.getheader("Transfer-Encoding") is None and r.getheader("Content-Length") is None
    assert calls == ["Hello there.", "How are you?"]
    assert len(body) == 2 * (RATE // 10) * 2
    assert np.frombuffer(body, "<i2")[0] == 16383


def test_bad_input_400_and_missing_model_503():
    srv = _serve(lambda s: np.zeros(10, dtype=np.float32))
    try:
        assert _get(srv.server_port, "/?text=")[0].status == 400
        assert _get(srv.server_port, "/")[0].status == 400
        assert _get(srv.server_port, "/?text=" + "a" * 1001)[0].status == 400
    finally:
        srv.shutdown()

    class Missing:
        def check(self):
            raise RuntimeError("Kokoro model files missing")

    srv = _serve(Missing())
    try:
        r, body = _get(srv.server_port, "/?text=hi")
    finally:
        srv.shutdown()
    assert r.status == 503 and b"missing" in body


def _tone(s):
    return np.full(RATE // 10, 0.5, dtype=np.float32)


def test_synth_failure_mid_stream_keeps_earlier_audio_and_closes_cleanly():
    def fake(sentence):
        if sentence.startswith("Two"):
            raise RuntimeError("boom")
        return _tone(sentence)

    srv = _serve(fake)
    try:
        r, body = _get(srv.server_port, "/?text=One.%20Two.%20Three.")
    finally:
        srv.shutdown()
    assert r.status == 200 and len(body) == (RATE // 10) * 2


def _serve_with(synth, token=None):
    srv = make_server(synth, "127.0.0.1", 0, token)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_non_lan_peer_refused_403(monkeypatch):
    import mazhost.kokoro_pcm as m
    monkeypatch.setattr(m, "is_lan_ip", lambda ip: False)
    srv = _serve_with(_tone)
    try:
        r, _ = _get(srv.server_port, "/?text=hi")
    finally:
        srv.shutdown()
    assert r.status == 403


def test_token_required_when_configured():
    srv = _serve_with(_tone, token="s3cret")
    port = srv.server_port
    try:
        assert _get(port, "/?text=hi")[0].status == 403
        assert _get(port, "/?text=hi&k=wrong")[0].status == 403
        assert _get(port, "/?text=hi&k=s3cret")[0].status == 200
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/?text=hi", headers={"Authorization": "Bearer s3cret"})
        assert c.getresponse().status == 200
    finally:
        srv.shutdown()


def test_503_retry_after_when_busy():
    gate, started = threading.Event(), threading.Semaphore(0)

    def slow(sentence):
        started.release()
        gate.wait(5)
        return _tone(sentence)

    srv = _serve(slow)
    port = srv.server_port
    try:
        out = []
        ts = [threading.Thread(target=lambda: out.append(_get(port, "/?text=hi")[0].status)) for _ in range(2)]
        for t in ts:
            t.start()
        for _ in range(2):
            assert started.acquire(timeout=5)
        r, body = _get(port, "/?text=hi")
        assert r.status == 503 and r.getheader("Retry-After") == "1" and body == b"busy"
        gate.set()
        for t in ts:
            t.join(5)
        assert out == [200, 200]
        assert _get(port, "/?text=hi")[0].status == 200  # slots released
    finally:
        gate.set()
        srv.shutdown()


def test_serve_in_thread_bind_conflict_does_not_raise():
    import socket
    from mazhost.config import Settings
    from mazhost.kokoro_pcm import serve_in_thread
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        s = Settings(kokoro_pcm_port=blocker.getsockname()[1], kokoro_pcm_bind="127.0.0.1")
        assert serve_in_thread(s) is None
    finally:
        blocker.close()


def test_serve_in_thread_starts_and_serves():
    from mazhost.config import Settings
    from mazhost.kokoro_pcm import serve_in_thread
    # port 0 would disable it in Core, so use an ephemeral port via the handle after binding to a free one
    import socket
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    srv = serve_in_thread(Settings(kokoro_pcm_port=port, kokoro_model_path="", kokoro_voices_path=""))
    assert srv is not None
    try:
        # empty model paths -> check() fails -> 503 proves the handler is live
        assert _get(srv.server_port, "/?text=hi")[0].status == 503
    finally:
        srv.shutdown()


def test_require_token_with_default_token_rejects_everything():
    from mazhost.config import Settings
    from mazhost.kokoro_pcm import _token
    assert _token(Settings(kokoro_pcm_require_token=True)) == ""
    assert _token(Settings(kokoro_pcm_require_token=True, token="real-token")) == "real-token"
    assert _token(Settings()) is None
    srv = _serve_with(_tone, token="")
    try:
        assert _get(srv.server_port, "/?text=hi&k=")[0].status == 403
    finally:
        srv.shutdown()
