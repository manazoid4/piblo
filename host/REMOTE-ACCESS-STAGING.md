# Piblo away-from-home staging

This slice displays the latest PibloCraft task status for a paired computer. People sign in with their email: the relay emails a 6-digit code (Resend). Only emails on the beta allowlist can sign in. Open sign-up and production hosting are separate release gates.

## Sign-in rules

- A code works once, for 10 minutes. Five wrong tries kill it. Codes are stored hashed.
- The page looks the same whether or not an email is allowed, so it never reveals who has access.
- Request limits per 15 minutes: 3 codes per email, 10 code requests and 30 code checks per address, 20 pairing calls per address.
- The browser session, the paired computer's credential and Core's local token stay separate.
- Logs never contain codes, cookies, keys or provider replies.

## Run locally

Use the repository's existing Core Python environment. Run from `host/` with a private state directory outside the checkout. The examples use a POSIX shell. In PowerShell, use `$env:USERPROFILE` and keep the state in a directory with an ACL restricted to your Windows user. The device state contains a raw, revocable device credential.

1. Allow an email and start the relay. `--print-codes` prints the code in the terminal instead of emailing it; it only works with `--insecure-local`.

   ```sh
   python -m mazhost.remote_relay --db "$HOME/.piblo-remote/relay.sqlite3" add-account you@example.com
   python -m mazhost.remote_relay --db "$HOME/.piblo-remote/relay.sqlite3" serve --origin http://127.0.0.1:8790 --insecure-local --print-codes
   ```

2. With Piblo Core running, set `PIBLO_CORE_TOKEN` in the connector environment to its existing local credential. Open `http://127.0.0.1:8790`, sign in with the code and choose **Pair a computer**. Run the connector command in a second terminal, enter the five-minute code and confirm the account and computer name shown.

   ```sh
   python -m mazhost.remote_connector --state "$HOME/.piblo-remote/device.json" --insecure-local pair http://127.0.0.1:8790 --host "My Piblo computer"
   ```

3. Start the connector, then create a queued Craft job with `pc send <worker> "<task>"` on the Core machine.

   ```sh
   python -m mazhost.remote_connector --state "$HOME/.piblo-remote/device.json" --insecure-local run
   ```

The browser refreshes every eight seconds. An ID and worker category identify each task. Task names, prompts, logs and provider credentials stay on the customer's host. The browser marks the last update offline after 30 seconds without a snapshot. **Unpair this computer** revokes the connector credential and removes its saved snapshot.

A relay database from the old access-key version is refused; start with a new `--db` file.

## Run through Tailscale Funnel (staging URL)

Funnel gives the relay a fixed public HTTPS address (`https://<machine>.<tailnet>.ts.net`). Customers only open that address in a browser and run the connector; they never install Tailscale. Their computer only needs outbound HTTPS.

1. Put `RESEND_API_KEY` in the relay's environment (optional: `PIBLO_REMOTE_MAIL_FROM`, default `Piblo <piblo@mazworks.uk>`). The relay refuses to start without a key.
2. Start the relay with the Funnel address as origin and trust Funnel's forwarded address for request limits:

   ```sh
   python -m mazhost.remote_relay --db "$HOME/.piblo-remote/relay.sqlite3" serve --origin https://<machine>.<tailnet>.ts.net --behind-proxy
   tailscale funnel --bg 8790
   ```

3. Pair connectors against the Funnel address without `--insecure-local`.

The connector checks Core's live privacy setting before sending any request to a public relay. Public pairing and publishing require Core to report private mode off. If Core becomes private, the connector stops sending and the relay marks its last snapshot offline. The relay's own sign-in email also goes through the private-mode guard.

The Funnel URL is only up while that machine and the relay are running. This path covers jobs already synced to Core through `pc send`. Direct standalone `pc spawn` still needs its own Core sync path.

## Check

```sh
python -m pytest tests/test_remote_relay.py tests/test_private_mode.py
```
