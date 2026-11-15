# End-to-end test of /voice/live against the REAL LFM3-Omni server, acting like the Cardputer.
# Runs a throwaway app with only the live route on 127.0.0.1:8788; production Core is untouched.
import asyncio, json, sys, threading, time, wave
import numpy as np, httpx, uvicorn, websockets
from fastapi import FastAPI
from mazhost.config import Settings
from mazhost.live_voice import install_live_voice

cfg = Settings()


class Sec:
    @staticmethod
    def token_ok(auth):
        return auth == f"Bearer {cfg.token}"


app = FastAPI()
install_live_voice(app, cfg, Sec)
threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=8788, log_level="warning"), daemon=True).start()
time.sleep(2)

core = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {cfg.token}"})
QUESTIONS = sys.argv[1:] or ["Hey Piblo, what's a quick dinner idea?", "And how long does that take?"]


def speech(text):
    w = core.post(f"http://127.0.0.1:{cfg.port}/speak", json={"text": text}).content
    open("lt_q.wav", "wb").write(w)
    with wave.open("lt_q.wav") as f:
        r = f.getframerate(); x = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    n = int(len(x) * 16000 / r)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32)).astype(np.int16).tobytes()


async def main():
    async with websockets.connect("ws://127.0.0.1:8788/voice/live", additional_headers={"Authorization": f"Bearer {cfg.token}"}, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "fw": "livetest"}))
        state = None; out = bytearray(); events = []
        rx_q = asyncio.Queue()

        async def rx():
            async for m in ws:
                await rx_q.put(m)
        asyncio.create_task(rx())

        async def wait_state(want, timeout=30):
            nonlocal state
            end = time.monotonic() + timeout
            while state != want:
                m = await asyncio.wait_for(rx_q.get(), max(0.01, end - time.monotonic()))
                handle(m)

        def handle(m):
            nonlocal state
            if isinstance(m, bytes):
                out.extend(m)
            else:
                ev = json.loads(m)
                if ev["type"] == "state":
                    state = ev["state"]; events.append((time.monotonic(), state))
                elif ev["type"] == "text":
                    print(ev["text"], end="", flush=True)
                elif ev["type"] == "error":
                    print("\n[error]", ev["message"])

        await wait_state("listening", 10)
        for i, q in enumerate(QUESTIONS):
            pcm = speech(q) + bytes(16000 * 2 * 1)  # speech, then 1 s of silence like a real room
            noise = (np.random.randn(len(pcm) // 2) * 40).astype(np.int16).tobytes()
            pcm = (np.frombuffer(pcm, np.int16).astype(np.int32) + np.frombuffer(noise, np.int16)).clip(-32768, 32767).astype(np.int16).tobytes()
            speech_end = time.monotonic() + (len(pcm) - 32000) / 32000
            print(f"\n--- turn {i}: {q}\nPiblo: ", end="")
            for j in range(0, len(pcm), 1280):  # 40 ms frames in real time
                await ws.send(pcm[j:j + 1280])
                await asyncio.sleep(0.04)
                while not rx_q.empty():
                    handle(rx_q.get_nowait())
                if state != "listening":
                    break
            out.clear()
            await wait_state("speaking", 30)
            t_first = time.monotonic()
            # play it like the device: drain until frames stop for 600 ms, then report done
            while True:
                try:
                    handle(await asyncio.wait_for(rx_q.get(), 0.6))
                except asyncio.TimeoutError:
                    break
            await ws.send(json.dumps({"type": "playback_done"}))
            await wait_state("listening", 10)
            print(f"\n  end of speech -> first reply audio: {1000*(t_first - speech_end):.0f} ms; reply {len(out)/32000:.1f} s")
            with wave.open(f"live_turn{i}.wav", "wb") as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(bytes(out))
        await ws.send(json.dumps({"type": "bye"}))

asyncio.run(main())
