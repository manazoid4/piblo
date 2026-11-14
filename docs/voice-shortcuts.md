# Voice shortcuts service

`Shortcuts(actions, buddy, executor=executor)` accepts a trusted, owner-configured mapping. Voice input selects an exact configured name only; never create the mapping from model output. Webhook values are HTTP(S) URLs without credentials, query strings, or fragments. Each request sends an empty POST, without redirects, after an individual Buddy approval.

For a local command use `{"command": "Get-Service", "cwd": "C:/work"}` as the value and provide an existing `ElevatedExecutor`. Commands use PowerShell through that executor; the caller must obtain its existing signed AuthorityBroker grant independently. Buddy approval does not replace that grant. These commands have the authority of the grant, not an OS sandbox: never imply that a working directory confines them.

Call `request(name)` to queue approval, then `poll(id, grant_token=token)` until a terminal result. `grant_required` means the command is approved but still needs a signed grant. A request is consumed before execution, so `uncertain` or `failed` must not be automatically retried. Configuration and pending requests are in memory; restarting expires them. The service is not connected to voice or an API by itself.
