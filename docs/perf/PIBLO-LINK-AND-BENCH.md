# Piblo Link (UART) and benchmark harness: what is proven on a PC (20 Nov 2026)

## Proven on the PC (no hardware)
- `cd firmware/nod && make test` : 2234 checks, 0 failed (framing, CRC, resync, credits, version mismatch,
  reconnect, peer reboot, lost-DATA credit resync).
- `cd firmware/nod && make link-sim` : two `Link`s over a simulated UART (10 bits/byte, rate-limited,
  optional bit errors / junk bytes / cable pull). Exits non-zero if a damaged frame is ever delivered or the link
  fails to recover. Numbers from this run (simulated time, MinGW g++ -O1):

| case | 115200 baud | 921600 baud |
|---|---|---|
| clean, saturated sender: payload throughput | 10.8 kB/s (wire max 11.5) | 86.4 kB/s (wire max 92.2) |
| clean: frame latency min/mean/max | 8 / 46 / 83 ms | 0 / 3 / 9 ms |
| noisy (1e-3 per byte flips + junk) | link stays up, 2.3 kB/s, 0 damaged frames delivered | 2.2 kB/s, 0 damaged delivered |
| cable pulled 3 s: fallback active, re-link after replug | 500 ms | 500 ms |
| one side power-cycles | data flows again < 2 s | < 2 s |

- Bug found and fixed by the sim: a DATA frame lost on the wire leaked its credit forever; ~4 losses froze the
  link at 0 credit until a reconnect (noisy run delivered 47 frames in 30 s, then 195 B/s). HEARTBEAT now carries
  the peer's free RX slots and a sender stuck at 0 credit with nothing in flight resyncs to it
  (`creditResyncs` counter). After the fix the same noisy run delivers ~10x more and keeps going.
- `python -m pytest host/tests/test_bench_parse.py` : 2 passed (log parser, merge, compare, empty-log error).

## NOT proven (needs the device)
- Nothing wires `piblo_link.h` into the Cardputer or a Waveshare firmware yet; no real UART pins, no real wire
  timing, no ESP32 numbers. The bench firmware (`cardputer-adv-bench`) was not built or flashed here.
- The benchmark harness numbers (loop_ms, net_ms, voice_ms, boot_ms) exist only on hardware. Nothing recorded yet.

## For Maz: on-device benchmark (do not run unattended; flashing needs your yes)
```
pio device list                                   # note the Cardputer COM port, call it COMx
pio run -e cardputer-adv-bench -t upload --upload-port COMx
pio device monitor -p COMx -b 115200 --filter log2file   # use it ~5 min incl. one voice question, then Ctrl+C
python scripts/bench_parse.py platformio-device-monitor-*.log
```
To compare a change: capture a second log the same way and run `python scripts/bench_parse.py base.log cand.log`.
