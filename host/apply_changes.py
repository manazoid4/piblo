with open('mazhost/app.py', 'r') as f:
    lines = f.readlines()

# Make all three changes in one pass
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Change 1: Add device_fw = {} after inflight = {"n": 0}
    if line.strip() == 'inflight = {"n": 0}  # live turns/jobs: self-update never restarts Core under one':
        new_lines.append(line)
        # Add device_fw = {} with same indentation (4 spaces)
        new_lines.append('    device_fw = {}\n')
        i += 1
        continue
    
    # Change 2: In health function, after core_service.note_device, add fw_version capture
    if 'core_service.note_device(request.client.host)  # the device polls this; live view reuses its address' in line:
        new_lines.append(line)
        # Add the fw_version capture with proper indentation (12 spaces - inside the if block)
        new_lines.append('            if fw_version:\n')
        new_lines.append('                device_fw["version"] = fw_version\n')
        i += 1
        continue
    
    # Change 3: In core_cardputer_status, replace return line
    if line.strip() == 'return core_service.cardputer_status()' and i > 0 and 'def core_cardputer_status' in lines[i-1]:
        # Replace with new implementation
        new_lines.append('        status = core_service.cardputer_status()\n')
        new_lines.append('        m = fw.manifest()\n')
        new_lines.append('        return {**status, "device_fw_version": device_fw.get("version") or None, "staged_fw_version": m["version"] if m else None}\n')
        i += 1
        continue
    
    new_lines.append(line)
    i += 1

with open('mazhost/app.py', 'w') as f:
    f.writelines(new_lines)

print('All changes applied')