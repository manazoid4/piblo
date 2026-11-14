; Interactive setup for the Piblo bootstrap board (does not flash firmware).
; Lists real COM ports, lets you pick, then sends Wi-Fi credentials over USB.
import getpass
import json
import time


def encode_credentials(ssid, password):
    if not 1 <= len(ssid.encode('utf-8')) <= 32 or len(password.encode('utf-8')) > 63:
        return None
    payload = json.dumps({'ssid': ssid, 'password': password}, ensure_ascii=False).encode('utf-8') + b'\n'
    return payload if len(payload) <= 257 else None


def main():
    import serial
    from serial.tools import list_ports

    ports = [p for p in list_ports.comports() if p.device.strip().upper() != 'COM3']
    if not ports:
        print('No board port found. Plug in the Piblo board and retry.')
        return 1
    for number, port in enumerate(ports, 1):
        print(f'{number}: {port.device} - {port.description}')
    try:
        choice = int(input('Choose the board port number: '))
        if choice < 1 or choice > len(ports):
            raise ValueError
        ssid = input('Wi-Fi name (2.4 GHz): ')
        password = getpass.getpass('Wi-Fi password (blank for open network): ')
        payload = encode_credentials(ssid, password)
        if payload is None:
            print('Wi-Fi details are too long or the name is empty.')
            return 1
        with serial.Serial(ports[choice - 1].device, 115200, timeout=1) as board:
            time.sleep(2)
            board.write(payload)
            print('Sent. Leave this window open; the board prints its IP when it joins.')
            while True:
                line = board.readline()
                if line:
                    print(line.decode('utf-8', errors='replace').rstrip())
    except (ValueError, serial.SerialException) as error:
        print(f'Could not use that port: {error}')
        return 1
    except KeyboardInterrupt:
        print('\nStopped watching.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
