with open('mazhost/app.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Fix lines with 20 spaces (5 tabs) -> 4 spaces
    if line.startswith('                    '):
        stripped = line.lstrip()
        if stripped.startswith('@api.get("/health")'):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('def health('):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('if not security.token_ok'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('# Public by design'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('return {"ok": True'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('if "ESP32"'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('core_service.note_device'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('if fw_version:'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('device_fw'):
            new_lines.append('                ' + stripped)
        elif stripped.startswith('core_status = versioned_core_status'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('return {'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('"ok": True,'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"name": "nod Core",'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"version": CORE_VERSION,'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"private_mode":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"git_sha":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"update":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"fw_latest":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"fw_update_available":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"stt":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"llm":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"nudge":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"tts":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"pc_control":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"core":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"bridge":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('**remote_info'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"authority":'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('"enabled":'):
            new_lines.append('                ' + stripped)
        elif stripped.startswith('"token_id":'):
            new_lines.append('                ' + stripped)
        elif stripped.startswith('"pending":'):
            new_lines.append('                ' + stripped)
        elif stripped.startswith('"active_grants":'):
            new_lines.append('                ' + stripped)
        elif stripped.startswith('"phone_url":'):
            new_lines.append('                ' + stripped)
        elif stripped.startswith('},'):
            new_lines.append('            ' + stripped)
        elif stripped.startswith('}') and not stripped.startswith('}'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('@api.get("/core/update")'):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('def core_update_status():'):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('return {**updates.status()'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('@api.post("/core/update/check")'):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('async def core_update_check():'):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('return await asyncio.to_thread'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('@api.get("/fw/status")'):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('def fw_status'):
            new_lines.append('    ' + stripped)
        elif stripped.startswith('return fw.staged_status'):
            new_lines.append('        ' + stripped)
        else:
            # Default: reduce 20 spaces to 4
            if line.startswith('                    '):
                new_lines.append('    ' + stripped)
            else:
                new_lines.append(line)
    elif line.startswith('                '):
        # 16 spaces -> check what it is
        stripped = line.lstrip()
        if stripped.startswith('turn_timings.add'):
            new_lines.append('        ' + stripped)
        elif stripped.startswith('return result'):
            new_lines.append('        ' + stripped)
        else:
            new_lines.append(line)
    else:
        new_lines.append(line)

with open('mazhost/app.py', 'w') as f:
    f.writelines(new_lines)

print('Fixed v2')