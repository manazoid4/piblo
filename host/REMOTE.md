# Remote access to Piblo

## Browser and Android over Tailscale

Connect the computer and the device you're using to the same tailnet. With Core running, open `http://<tailscale-ip>:8787/control/` in a browser, or enter the Core address in the Android app.

For a private HTTPS browser link, enable Tailscale Serve for the computer and run `tailscale serve --bg --https=443 http://127.0.0.1:8787` on it. Open `/control/` at the HTTPS address printed by Serve. The browser link works on devices that can reach the tailnet.

On the computer, open **Piblo → Settings → Browser sign-in** and generate a code. Enter it on the browser sign-in page within five minutes. The browser receives a control session cookie scoped to `/control/`.

## Standalone Piblo firmware (optional public HTTPS)

Goal: the Cardputer (on a phone hotspot or any Wi-Fi) and your phone reach Core on your home PC, free, with the token still required.

## Decision

| | Tailscale (+ Funnel) | Cloudflare Tunnel |
|---|---|---|
| Phone | joins the tailnet (app) | plain browser |
| Cardputer | cannot run Tailscale, so needs a public HTTPS URL: **Funnel** | public URL |
| Cost | free (Personal plan) | free, but a stable name needs a domain you own; "quick tunnels" give a random URL that changes on restart |
| Owner steps | install on PC + phone, run one script | buy/point a domain, create a named tunnel, run cloudflared as a service |
| Public URL | `https://<pc>.<tailnet>.ts.net` (stable, real Let's Encrypt cert) | needs domain |

**Recommendation: Tailscale on the PC and phone, plus Tailscale Funnel for the Cardputer.** Fewest steps, no domain, stable URL, one tool for both devices. Funnel facts (tailscale.com docs, checked): available on all plans including free; needs MagicDNS + HTTPS certificates enabled for the tailnet; only listens on ports 443, 8443, 10000; TLS only; DNS name must be your `*.ts.net` name; traffic is subject to non-configurable (unpublished) bandwidth limits, fine for nod's small JSON and short audio, not for bulk transfer. CLI: `tailscale funnel --bg 8787` (persistent, proxies to `http://127.0.0.1:8787`). Cloudflare Tunnel remains the fallback if you later want your own domain.

The PC firewall needs no inbound rule: Funnel connects outbound from the PC and tailscaled hands requests to Core on loopback.

## Owner steps

1. **PC:** `winget install --id Tailscale.Tailscale -e`, open Tailscale, sign in.
2. **Phone:** install the Tailscale app, sign in with the same account.
3. **Admin console (once):** https://login.tailscale.com/admin/dnsettings - make sure MagicDNS and "HTTPS certificates" are enabled. The first `funnel` run also prints a link to enable Funnel if needed.
4. **PC:** make sure Core is running, then
   `powershell -ExecutionPolicy Bypass -File host\setup-remote.ps1`
   It prints your URL, e.g. `https://mypc.tail1234.ts.net`. (Stop publishing: `host\setup-remote.ps1 -Off`.)
5. **Cardputer (automatic):** `setup-remote.ps1` writes `MAZ_REMOTE_URL` to Core's `.env` (Core also auto-detects a funneled port every 10 min). Core reports it in the authenticated `/health` (`remote_url`, plus `tailnet_ip` for the phone: `http://<tailnet_ip>:8787/control`); the device saves it when it next reads `/health` over the LAN. Manual alternative: while on home Wi-Fi (or via its setup AP) open the device web page, unlock, paste the URL into **Remote URL**, Save. The same field exists in the phone control page and serial config. The device uses the LAN address first (1.8 s timeout) and switches to the remote URL only when LAN is unreachable; the header shows LAN or REMOTE.

## Verify from the phone

Open `https://<your-url>/health` in the phone browser (mobile data, Wi-Fi off). You should see `{"ok":true,"name":"nod Core","version":"..."}`. Without the token this is all anyone gets. Any other path returns 401.

## Approvals and Calls over remote

- Cardputer requests (talk, WORK, agents, authority requests) are the same HTTPS calls with `Authorization: Bearer <token>`, sent to the remote URL when LAN is down.
- Approvals: use `/control/` over the LAN or your tailnet with a one-time browser code. Pending approvals, grants and revoke-all work with the browser session cookie.
- Firmware update (`/fw/*`) is deliberately LAN-only on the device (1.4 MB over Funnel is slow and unreliable).
- `/pair/*` (device pairing page) is blocked remotely except `/pair/start` with the token; pair at home.

## What Core enforces (host/mazhost/security.py)

- Every route except `/health` needs the bearer token (or the `/control` session cookie). `/health` without a token returns only `ok`, `name`, `version`; with the token it returns the full status.
- A request is "remote" when it arrives via loopback carrying `X-Forwarded-For` / `Tailscale-Funnel-Request` (what tailscaled adds when proxying Funnel), or when the TCP peer is a public IP. Forwarding headers are trusted only from a loopback peer; the rightmost `X-Forwarded-For` entry is the client IP. Remote requests cannot reach `/docs`, `/redoc`, `/openapi.json`, `/pair/` page or `/pair/claim`.
- Per-IP lockout: 10 auth failures (401/403, bad pairing code) in 60 s locks that IP out for 60 s with 429. Loopback (the PC itself) is exempt.

## Risks / limits

- Funnel makes Core reachable by anyone who learns the URL. The token is the only secret: keep it long and random (`MAZ_TOKEN`), rotate it if the URL and token leak together. Tokens are never printed by the script.
- Header behaviour (`X-Forwarded-For`, `Tailscale-Funnel-Request`) is not fully documented by Tailscale; if Funnel ever stopped sending both, remote requests would look like loopback, and the guard would only lose the docs/pairing blocks - token auth on every API route still holds.
- Device TLS trusts a small embedded CA bundle (`src/net/ca_bundle.bin`: ISRG/Let's Encrypt X1+X2, Google Trust Services R1-R4, DigiCert G2/G3, USERTrust). Funnel certs are Let's Encrypt; if Let's Encrypt moves Funnel to a root not in the bundle (ISRG Root YE/YR are listed but absent from the current Mozilla file), regenerate with `python scripts/gen_ca_bundle.py cacert.pem src/net/ca_bundle.bin` and reflash. A failed verification means the device refuses to send the token.
- Funnel bandwidth caps are unpublished; TLS handshake needs roughly 40 KB of free heap during a request and is released afterwards.
