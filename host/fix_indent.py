import re

with open('mazhost/app.py', 'r') as f:
    content = f.read()

# The issue is that the patch tool keeps over-indenting.
# Let's find the health function and fix it by rewriting the specific section.

# Find the problematic section and replace with correct indentation
old_section = '''        turn_timings.add(row)
        return result

    @api.get("/health")
    def health(request: Request, authorization: str | None = Header(default=None), fw_version: str = ""):
        if not security.token_ok(authorization):
            # Public by design (reachable via Funnel): no inventory, no paths.
            return {"ok": True, "name": "nod Core", "version": CORE_VERSION}
        if "ESP32" in request.headers.get("user-agent", "") and request.client:
            core_service.note_device(request.client.host)  # the device polls this; live view reuses its address
            if fw_version:
                device_fw["version"] = fw_version
        core_status = versioned_core_status() if cfg.core_enabled else {"ok": False, "disabled": True}
        return {
            "ok": True,
            "name": "nod Core",
            "version": CORE_VERSION,
            "private_mode": private_mode.enabled(),
            "git_sha": updates.running_sha,
            "update": updates.brief(),
            "fw_latest": (lambda m: m and {"version": m["version"], "sha": m.get("sha", "")})(fw.manifest()),
            "fw_update_available": fw.staged_status(fw_version)["update_available"],
            "stt": speech.available(),
            "llm": model_router.status(),
            "nudge": nudge_client.status(),
            "tts": speech_out.available(),
            "pc_control": pc_controller.available,
            "core": core_status,
            "bridge": bridge_worker.status(),
            **remote_info.get(),
            "authority": {
                "enabled": cfg.control_enabled,
                "token_id": authority.token_id,
                "pending": len(authority.pending()),
                "active_grants": len(authority.active_grants()),
                "phone_url": "/control/",
            },
        }

    @api.get("/core/update")
    def core_update_status():
        return {**updates.status(), "fw_report": fw.last_report()}'''

# This is a more flexible approach - use regex to find and fix
# Let's just fix the specific lines we know are broken

lines = content.split('\n')
new_lines = []
for line in lines:
    # Fix over-indented lines that should be at 4-space indent level
    if line.startswith('                    @api.get("/health")'):
        new_lines.append('    @api.get("/health")')
    elif line.startswith('                def health('):
        new_lines.append('    def health(request: Request, authorization: str | None = Header(default=None), fw_version: str = ""):')
    elif line.startswith('                    if not security.token_ok'):
        new_lines.append('        if not security.token_ok(authorization):')
    elif line.startswith('                        # Public by design'):
        new_lines.append('            # Public by design (reachable via Funnel): no inventory, no paths.')
    elif line.startswith('                        return {"ok": True'):
        new_lines.append('            return {"ok": True, "name": "nod Core", "version": CORE_VERSION}')
    elif line.startswith('                    if "ESP32"'):
        new_lines.append('        if "ESP32" in request.headers.get("user-agent", "") and request.client:')
    elif line.startswith('                        core_service.note_device'):
        new_lines.append('            core_service.note_device(request.client.host)  # the device polls this; live view reuses its address')
    elif line.startswith('                        if fw_version:'):
        new_lines.append('            if fw_version:')
    elif line.startswith('                            device_fw'):
        new_lines.append('                device_fw["version"] = fw_version')
    elif line.startswith('                    core_status = versioned_core_status'):
        new_lines.append('        core_status = versioned_core_status() if cfg.core_enabled else {"ok": False, "disabled": True}')
    elif line.startswith('                    return {'):
        new_lines.append('        return {')
    elif line.startswith('                        "ok": True,'):
        new_lines.append('            "ok": True,')
    elif line.startswith('                        "name": "nod Core",'):
        new_lines.append('            "name": "nod Core",')
    elif line.startswith('                        "version": CORE_VERSION,'):
        new_lines.append('            "version": CORE_VERSION,')
    elif line.startswith('                        "private_mode":'):
        new_lines.append('            "private_mode": private_mode.enabled(),')
    elif line.startswith('                        "git_sha":'):
        new_lines.append('            "git_sha": updates.running_sha,')
    elif line.startswith('                        "update":'):
        new_lines.append('            "update": updates.brief(),')
    elif line.startswith('                        "fw_latest":'):
        new_lines.append('            "fw_latest": (lambda m: m and {"version": m["version"], "sha": m.get("sha", "")})(fw.manifest()),')
    elif line.startswith('                        "fw_update_available":'):
        new_lines.append('            "fw_update_available": fw.staged_status(fw_version)["update_available"],')
    elif line.startswith('                        "stt":'):
        new_lines.append('            "stt": speech.available(),')
    elif line.startswith('                        "llm":'):
        new_lines.append('            "llm": model_router.status(),')
    elif line.startswith('                        "nudge":'):
        new_lines.append('            "nudge": nudge_client.status(),')
    elif line.startswith('                        "tts":'):
        new_lines.append('            "tts": speech_out.available(),')
    elif line.startswith('                        "pc_control":'):
        new_lines.append('            "pc_control": pc_controller.available,')
    elif line.startswith('                        "core":'):
        new_lines.append('            "core": core_status,')
    elif line.startswith('                        "bridge":'):
        new_lines.append('            "bridge": bridge_worker.status(),')
    elif line.startswith('                        **remote_info'):
        new_lines.append('            **remote_info.get(),')
    elif line.startswith('                        "authority":'):
        new_lines.append('            "authority": {')
    elif line.startswith('                            "enabled":'):
        new_lines.append('                "enabled": cfg.control_enabled,')
    elif line.startswith('                            "token_id":'):
        new_lines.append('                "token_id": authority.token_id,')
    elif line.startswith('                            "pending":'):
        new_lines.append('                "pending": len(authority.pending()),')
    elif line.startswith('                            "active_grants":'):
        new_lines.append('                "active_grants": len(authority.active_grants()),')
    elif line.startswith('                            "phone_url":'):
        new_lines.append('                "phone_url": "/control/",')
    elif line.startswith('                        },'):
        new_lines.append('            },')
    elif line.startswith('                    }'):
        new_lines.append('        }')
    elif line.startswith('                @api.get("/core/update")'):
        new_lines.append('    @api.get("/core/update")')
    elif line.startswith('                def core_update_status():'):
        new_lines.append('    def core_update_status():')
    elif line.startswith('                    return {**updates.status()'):
        new_lines.append('        return {**updates.status(), "fw_report": fw.last_report()}')
    else:
        new_lines.append(line)

with open('mazhost/app.py', 'w') as f:
    f.write('\n'.join(new_lines))

print('Fixed')