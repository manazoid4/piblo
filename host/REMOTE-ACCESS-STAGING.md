# Piblo away-from-home staging

This slice displays the latest PibloCraft task status for a paired computer. It uses operator-issued staging access keys. Customer account sign-in and hosted deployment are separate release gates.

## Run locally

Use the repository's existing Core Python environment. Run from `host/` with a private state directory outside the checkout. The examples use a POSIX shell. In PowerShell, use `$env:USERPROFILE` and keep the state in a directory with an ACL restricted to your Windows user. The device state contains a raw, revocable device credential.

1. Create a staging account and start the relay in a terminal.

   ```sh
   python -m mazhost.remote_relay --db "$HOME/.piblo-remote/relay.sqlite3" add-account alice
   python -m mazhost.remote_relay --db "$HOME/.piblo-remote/relay.sqlite3" serve --origin http://127.0.0.1:8790 --insecure-local
   ```

2. With Piblo Core running, set `PIBLO_CORE_TOKEN` in the connector environment to its existing local credential. Open `http://127.0.0.1:8790`, sign in and choose **Pair a computer**. Run the connector command in a second terminal, enter the five-minute code and confirm the account and computer name shown.

   ```sh
   python -m mazhost.remote_connector --state "$HOME/.piblo-remote/device.json" --insecure-local pair http://127.0.0.1:8790 --host "My Piblo computer"
   ```

3. Start the connector, then create a queued Craft job with `pc send <worker> "<task>"` on the Core machine.

   ```sh
   python -m mazhost.remote_connector --state "$HOME/.piblo-remote/device.json" --insecure-local run
   ```

The browser refreshes every eight seconds. An ID and worker category identify each task. Task names, prompts, logs and provider credentials stay on the customer's host. The browser marks the last update offline after 30 seconds without a snapshot. **Unpair this computer** revokes the connector credential and removes its saved snapshot.

## Run through an HTTPS staging origin

Run the relay on the staging machine with `--origin https://<your-staging-host>` and omit `--insecure-local`. Configure the existing HTTPS reverse proxy on that machine to forward that origin to `127.0.0.1:8790`. Pair the client's connector against the HTTPS origin and omit `--insecure-local` there too. Only outbound HTTPS is required at the customer's computer.

The connector checks Core's live privacy setting before sending any request to a public relay. Public pairing and publishing require Core to report private mode off. If Core becomes private, the connector stops sending and the relay marks its last snapshot offline.

Keep the database on persistent storage, use synthetic jobs and operator-issued keys, and restrict access to test participants. Add customer identity, rate limits, service operations and mobile-data proof before a customer rollout. This path covers jobs already synced to Core through `pc send`. Direct standalone `pc spawn` still needs its own Core sync path.

## Check

```sh
python -m pytest tests/test_remote_relay.py
```
