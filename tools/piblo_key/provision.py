#!/usr/bin/env python3
"""Factory provisioning for the Piblo Key (NXP NTAG 424 DNA). See README.md.

  PIBLO_KEY_MASTER=<32 hex> python provision.py            # real card on a PC/SC reader (pyscard)
  PIBLO_KEY_MASTER=<32 hex> python provision.py --dry-run  # no reader: keys, NDEF, offsets, CSV row

The pure parts (diversification, NDEF template, SDM settings, EV2 secure messaging) are unit-tested.
The reader path has NOT been run against a real card from this repo's CI; read README.md first."""
from __future__ import annotations

import argparse
import csv
import os
import secrets
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host"))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from mazhost import piblo_key  # noqa: E402
from mazhost.piblo_key import aes_cmac  # noqa: E402

APP_AID = bytes.fromhex("D2760000850101")
FACTORY_KEY = bytes(16)
URL_PREFIX = "piblo.co.uk/k?p="
NDEF_FILE_NO = 0x02
KEY_VERSION = 0x01


# ---------------------------------------------------------------- NDEF + SDM settings (pure)

def ndef_template() -> tuple[bytes, dict]:
    """NDEF file content (NLEN + one URI record, https:// prefix code 0x04) and SDM offsets into the file."""
    body = URL_PREFIX + "0" * 32 + "&c=" + "0" * 16
    payload = b"\x04" + body.encode("ascii")
    record = bytes([0xD1, 0x01, len(payload), 0x55]) + payload
    data = len(record).to_bytes(2, "big") + record
    p_off = 2 + 4 + 1 + len(URL_PREFIX)
    c_off = p_off + 32 + len("&c=")
    return data, {"picc_data": p_off, "mac_input": c_off, "mac": c_off}


def sdm_file_settings(offsets: dict) -> bytes:
    """ChangeFileSettings data for file 02: SDM on, PICCData (UID+counter) encrypted with key 1, MAC with key 2.

    FileOption 0x40 (plain comm, SDM) | AccessRights RW=0 Chg=0 Read=E Write=0 | SDMOptions 0xC1 (UID, ctr, ASCII)
    | SDMAccessRights 0xFF12 little-endian (ctr read none, meta key 1, file key 2) | 3-byte LE offsets."""
    le3 = lambda n: n.to_bytes(3, "little")  # noqa: E731
    return (bytes([0x40, 0x00, 0xE0, 0xC1]) + struct.pack("<H", 0xFF12)
            + le3(offsets["picc_data"]) + le3(offsets["mac_input"]) + le3(offsets["mac"]))


def parse_master(text: str | None) -> bytes:
    try:
        master = bytes.fromhex((text or "").strip())
    except ValueError:
        master = b""
    if len(master) != 16:
        raise SystemExit("Set PIBLO_KEY_MASTER to 32 hex characters (the Piblo master key).")
    return master


def plan(master: bytes, uid: bytes) -> dict:
    keys = piblo_key.card_keys(master, uid)
    ndef, offsets = ndef_template()
    return {"uid": uid, "keys": keys, "ndef": ndef, "offsets": offsets, "file_settings": sdm_file_settings(offsets),
            "enrol_code": piblo_key.format_enrol_code(uid, keys["meta"], keys["file"])}


# ---------------------------------------------------------------- EV2 secure messaging (pure)

def _cbc(key: bytes, iv: bytes, data: bytes, encrypt: bool) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


def _pad(data: bytes) -> bytes:
    return data + b"\x80" + bytes(-(len(data) + 1) % 16)


def _rotl(b: bytes) -> bytes:
    return b[1:] + b[:1]


def session_keys(key: bytes, rnd_a: bytes, rnd_b: bytes) -> tuple[bytes, bytes]:
    """(KSesAuthENC, KSesAuthMAC) per NT4H2421 section on AuthenticateEV2First."""
    def sv(label: bytes) -> bytes:
        mixed = bytes(x ^ y for x, y in zip(rnd_a[2:8], rnd_b[0:6]))
        return label + bytes.fromhex("00010080") + rnd_a[0:2] + mixed + rnd_b[6:16] + rnd_a[8:16]
    return aes_cmac(key, sv(b"\xA5\x5A")), aes_cmac(key, sv(b"\x5A\xA5"))


def _even(mac: bytes) -> bytes:
    return bytes(mac[i] for i in range(1, 16, 2))


class Ev2:
    """Authenticated EV2 session. Builds Full-mode commands and checks responses; no I/O."""

    def __init__(self, ti: bytes, k_enc: bytes, k_mac: bytes) -> None:
        self.ti, self.k_enc, self.k_mac, self.ctr = ti, k_enc, k_mac, 0

    def _iv(self, label: bytes, ctr: int) -> bytes:
        return _cbc(self.k_enc, bytes(16), label + self.ti + ctr.to_bytes(2, "little") + bytes(8), True)

    def command(self, cmd: int, header: bytes, data: bytes) -> bytes:
        """Native APDU body (without the 90 .. wrapper): header || E(data) || MACt."""
        enc = _cbc(self.k_enc, self._iv(b"\xA5\x5A", self.ctr), _pad(data), True) if data else b""
        mac = _even(aes_cmac(self.k_mac, bytes([cmd]) + self.ctr.to_bytes(2, "little") + self.ti + header + enc))
        return header + enc + mac

    def response(self, rc: int, body: bytes, expect_mac: bool = True) -> bytes:
        """Verify the response MAC (counter+1) and return decrypted data. Advances the counter."""
        self.ctr += 1
        if not expect_mac:
            return body
        data, mac = body[:-8], body[-8:]
        want = _even(aes_cmac(self.k_mac, bytes([rc]) + self.ctr.to_bytes(2, "little") + self.ti + data))
        if mac != want:
            raise IOError("Response MAC mismatch")
        if not data:
            return b""
        plain = _cbc(self.k_enc, self._iv(b"\x5A\xA5", self.ctr), data, False)
        return plain.rstrip(b"\x00")[:-1] if plain.rstrip(b"\x00").endswith(b"\x80") else plain


def change_key_data(key_no: int, new: bytes, old: bytes) -> bytes:
    """ChangeKey payload. Key 0 sends the new key; other keys send new XOR old plus CRC32 of the new key (JAMCRC)."""
    if key_no == 0:
        return new + bytes([KEY_VERSION])
    crc = (~zlib.crc32(new)) & 0xFFFFFFFF
    return bytes(a ^ b for a, b in zip(new, old)) + bytes([KEY_VERSION]) + crc.to_bytes(4, "little")


# ---------------------------------------------------------------- card I/O (hardware only)

class Card:
    def __init__(self, connection) -> None:
        self.conn = connection
        self.session: Ev2 | None = None

    def raw(self, apdu: bytes) -> tuple[bytes, int, int]:
        data, sw1, sw2 = self.conn.transmit(list(apdu))
        return bytes(data), sw1, sw2

    def native(self, cmd: int, data: bytes = b"") -> tuple[bytes, int]:
        body = bytes([0x90, cmd, 0, 0]) + (bytes([len(data)]) + data if data else b"") + b"\x00"
        out, sw1, sw2 = self.raw(body)
        if sw1 != 0x91 or sw2 not in (0x00, 0xAF):
            raise IOError(f"card error {sw1:02X}{sw2:02X} on command {cmd:02X}")
        return out, sw2

    def uid(self) -> bytes:
        out, sw1, sw2 = self.raw(bytes.fromhex("FFCA000000"))
        if (sw1, sw2) != (0x90, 0x00) or len(out) != 7:
            raise IOError("Could not read a 7-byte UID (is this an NTAG 424 DNA?)")
        return out

    def select_app(self) -> None:
        _, sw1, sw2 = self.raw(bytes.fromhex("00A40400") + bytes([len(APP_AID)]) + APP_AID + b"\x00")
        if (sw1, sw2) != (0x90, 0x00):
            raise IOError("NTAG 424 DNA application not found")

    def write_ndef(self, data: bytes) -> None:
        """Factory file 02 is free-write, so this runs before authentication (ISO UpdateBinary)."""
        for apdu in (bytes.fromhex("00A4000C02E104"), bytes.fromhex("00D60000") + bytes([len(data)]) + data):
            _, sw1, sw2 = self.raw(apdu)
            if (sw1, sw2) != (0x90, 0x00):
                raise IOError(f"NDEF write failed {sw1:02X}{sw2:02X}")

    def read_ndef(self) -> bytes:
        self.raw(bytes.fromhex("00A4000C02E104"))
        out, sw1, sw2 = self.raw(bytes.fromhex("00B0000080"))
        if (sw1, sw2) != (0x90, 0x00):
            raise IOError("NDEF read failed")
        return out

    def authenticate(self, key: bytes, key_no: int = 0) -> None:
        enc_b, sw = self.native(0x71, bytes([key_no, 0x00]))
        if sw != 0xAF or len(enc_b) != 16:
            raise IOError("AuthenticateEV2First step 1 failed")
        rnd_b = _cbc(key, bytes(16), enc_b, False)
        rnd_a = secrets.token_bytes(16)
        reply, sw = self.native(0xAF, _cbc(key, bytes(16), rnd_a + _rotl(rnd_b), True))
        plain = _cbc(key, bytes(16), reply, False)
        ti, rnd_a_back = plain[:4], plain[4:20]
        if rnd_a_back != _rotl(rnd_a):
            raise IOError("Card failed to prove it knows the key")
        self.session = Ev2(ti, *session_keys(key, rnd_a, rnd_b))

    def secure(self, cmd: int, header: bytes, data: bytes, expect_mac: bool = True) -> bytes:
        assert self.session
        body = self.session.command(cmd, header, data)
        out, _ = self.native(cmd, body)
        return self.session.response(0x00, out, expect_mac)


def provision_card(connection, master: bytes, log=print) -> dict:
    card = Card(connection)
    uid = card.uid()
    info = plan(master, uid)
    card.select_app()
    log(f"Card UID {uid.hex().upper()}: writing NDEF template")
    card.write_ndef(info["ndef"])
    card.authenticate(FACTORY_KEY)
    log("Enabling SDM")
    card.secure(0x5F, bytes([NDEF_FILE_NO]), info["file_settings"])
    for key_no, role in ((1, "meta"), (2, "file")):
        log(f"Changing key {key_no}")
        card.secure(0xC4, bytes([key_no]), change_key_data(key_no, info["keys"][role], FACTORY_KEY))
    log("Changing key 0 (last)")
    card.secure(0xC4, bytes([0]), change_key_data(0, info["keys"]["app"], FACTORY_KEY), expect_mac=False)
    verify_readback(card, info)
    return info


def verify_readback(card: Card, info: dict) -> None:
    """Read the NDEF file back as a phone would; the mirrored p/c must verify with the new keys."""
    raw = card.read_ndef().decode("latin-1")
    query = raw.split("?", 1)[-1]
    p, c = query.split("p=")[1][:32], query.split("&c=")[1][:16]
    uid, ctr = piblo_key.verify_sun(bytes.fromhex(p), bytes.fromhex(c), info["keys"]["meta"], info["keys"]["file"])
    if uid != info["uid"]:
        raise IOError("Read-back UID mismatch")
    print(f"Read-back verified (tap counter {ctr})")


# ---------------------------------------------------------------- CLI

def write_csv_row(path: Path, info: dict, status: str) -> None:
    """The CSV holds enrolment strings (secrets). Created 0600; keep it off shared drives and out of git."""
    new = not path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["uid", "enrol_code", "ndef_hex", "picc_offset", "mac_offset", "status"])
        writer.writerow([info["uid"].hex().upper(), info["enrol_code"], info["ndef"].hex(),
                         info["offsets"]["picc_data"], info["offsets"]["mac"], status])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provision a Piblo Key (NTAG 424 DNA).")
    ap.add_argument("--dry-run", action="store_true", help="no reader: compute keys, NDEF, offsets, CSV row")
    ap.add_argument("--uid", help="7-byte UID in hex (dry run; random if omitted)")
    ap.add_argument("--csv", default="piblo_keys.csv", type=Path)
    ap.add_argument("--reader", type=int, default=0, help="PC/SC reader index")
    args = ap.parse_args(argv)
    master = parse_master(os.environ.get("PIBLO_KEY_MASTER"))

    if args.dry_run:
        uid = bytes.fromhex(args.uid) if args.uid else b"\x04" + secrets.token_bytes(6)
        if len(uid) != 7:
            raise SystemExit("--uid must be 14 hex characters")
        info = plan(master, uid)
        print(f"UID          {uid.hex().upper()}")
        print(f"NDEF         {info['ndef'].hex()}")
        print(f"Offsets      {info['offsets']}")
        print(f"FileSettings {info['file_settings'].hex()}")
        print(f"Enrol code   {info['enrol_code']}")
        write_csv_row(args.csv, info, "dry-run")
        return 0

    try:
        from smartcard.System import readers
    except ImportError:
        raise SystemExit("pyscard is not installed: pip install pyscard") from None
    found = readers()
    if not found:
        raise SystemExit("No PC/SC reader found.")
    connection = found[args.reader].createConnection()
    connection.connect()
    info = provision_card(connection, master)
    write_csv_row(args.csv, info, "provisioned")
    print(f"Done. Enrolment code written to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
