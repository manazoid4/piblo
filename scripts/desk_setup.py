"""Interactive USB setup for the Piblo desk prototype; does not flash firmware."""
import getpass
import json
import time


def encode_credentials(ssid, password):
    if not 1 <= len(ssid.encode('utf-8')) <= 32 or len(password.encode('utf-8')) > 63:
        return None
    payload = json.dumps({'ssid': ssid, 'password': password}, ensure_ascii=False).encode('utf-8') + b'\n'
    return payload if len(payload) <= 257 else None


def is_safe_port(port):
    return port.strip().upper() != 'COM3'


def main():
    import serial
    from serial.tools import list_ports

    ports = [port for port in list_ports.comports() if is_safe_port(port.device)]
    if not ports:
        print('No available board port found. COM3 is reserved for Core.')
        return 1
    for number, port in enumerate(ports, 1):
        print(f'{number}: {port.device} - {port.description}')
    try:
        choice = int(input('Choose the board port number: '))
        if choice < 1 or choice > len(ports):
            raise ValueError
        ssid = input('Wi-Fi name: ')
        password = getpass.getpass('Wi-Fi password (blank for open network): ')
        payload = encode_credentials(ssid, password)
        if payload is None:
            print('Wi-Fi details are too long or the name is empty.')
            return 1
        with serial.Serial(ports[choice - 1].device, 115200, timeout=1) as board:
            time.sleep(2)
            board.write(payload)
            print('Sent. Compare the board code with the Piblo app and click Allow. Press Ctrl+C to stop watching.')
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
