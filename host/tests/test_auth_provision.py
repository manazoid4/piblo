import importlib.util
import stat
import sys
from pathlib import Path

import pytest

from mazhost import piblo_key as pk

_path = Path(__file__).resolve().parents[2] / "tools" / "piblo_key" / "provision.py"
_spec = importlib.util.spec_from_file_location("piblo_provision", _path)
prov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prov)

MASTER = bytes(range(16))
UID = bytes.fromhex("04DE5F1EACC040")


def test_ndef_offsets_point_at_placeholders():
    data, off = prov.ndef_template()
    assert int.from_bytes(data[:2], "big") == len(data) - 2
    assert data[2:6] == bytes([0xD1, 0x01, len(data) - 6, 0x55]) and data[6] == 0x04       # https:// prefix code
    assert data[7:].decode().startswith("piblo.co.uk/k?p=")
    p, c = off["picc_data"], off["mac"]
    assert data[p:p + 32] == b"0" * 32 and data[p - 3:p] == b"?p="
    assert data[c:c + 16] == b"0" * 16 and data[c - 3:c] == b"&c="
    assert off["mac_input"] == c                      # empty MAC input, matching AUTH.md (CMAC over nothing)
    assert (p, c) == (23, 58)


def test_file_settings_bytes():
    _, off = prov.ndef_template()
    s = prov.sdm_file_settings(off)
    assert s == bytes.fromhex("4000E0C112FF") + bytes([23, 0, 0, 58, 0, 0, 58, 0, 0])
    assert len(s) == 15


def test_plan_is_consistent_with_core_verifier():
    info = prov.plan(MASTER, UID)
    assert pk.parse_enrol_code(info["enrol_code"]) == (UID, info["keys"]["meta"], info["keys"]["file"])
    assert info["keys"] == pk.card_keys(MASTER, UID) and info["keys"]["app"] != bytes(16)


def test_master_env_validation():
    assert prov.parse_master("00" * 16) == bytes(16)
    for bad in (None, "", "zz", "00" * 15):
        with pytest.raises(SystemExit):
            prov.parse_master(bad)


def test_dry_run_cli_writes_private_csv(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PIBLO_KEY_MASTER", MASTER.hex())
    out = tmp_path / "keys.csv"
    assert prov.main(["--dry-run", "--uid", UID.hex(), "--csv", str(out)]) == 0
    assert prov.main(["--dry-run", "--csv", str(out)]) == 0
    rows = out.read_text().splitlines()
    assert rows[0].startswith("uid,enrol_code") and len(rows) == 3
    assert rows[1].split(",")[0] == UID.hex().upper() and rows[1].split(",")[1].startswith("PK1-04DE5F1EACC040-")
    if sys.platform != "win32":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
    shown = capsys.readouterr().out
    assert "Enrol code   PK1-" in shown and MASTER.hex() not in shown


# ---- EV2 secure messaging, with the card side re-implemented independently in the test
def test_session_key_layout():
    a, b = bytes(range(16)), bytes(range(16, 32))
    k = bytes(16)
    enc, mac = prov.session_keys(k, a, b)
    sv = bytes.fromhex("A55A00010080") + a[0:2] + bytes(x ^ y for x, y in zip(a[2:8], b[0:6])) + b[6:16] + a[8:16]
    assert len(sv) == 32 and enc == pk.aes_cmac(k, sv)
    assert mac == pk.aes_cmac(k, b"\x5a\xa5" + sv[2:]) and enc != mac


class FakePicc:
    """Card side of AuthenticateEV2First + Full-mode commands, written from the datasheet separately from Ev2."""
    def __init__(self, key):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        self.C, self.A, self.M = Cipher, algorithms, modes
        self.key, self.rnd_b, self.ti, self.ctr = key, bytes(range(100, 116)), b"\x76\x14\x28\x1a", 0

    def cbc(self, key, iv, data, enc):
        c = self.C(self.A.AES(key), self.M.CBC(iv))
        op = c.encryptor() if enc else c.decryptor()
        return op.update(data) + op.finalize()

    def auth(self, reply):
        plain = self.cbc(self.key, bytes(16), reply, False)
        self.rnd_a = plain[:16]
        assert plain[16:] == self.rnd_b[1:] + self.rnd_b[:1]
        self.k_enc, self.k_mac = prov.session_keys(self.key, self.rnd_a, self.rnd_b)
        return self.cbc(self.key, bytes(16), self.ti + self.rnd_a[1:] + self.rnd_a[:1] + bytes(12), True)

    def open_command(self, cmd, header, body):
        enc, mac = body[len(header):-8], body[-8:]
        want = bytes(pk.aes_cmac(self.k_mac, bytes([cmd]) + self.ctr.to_bytes(2, "little") + self.ti + header + enc)[i] for i in range(1, 16, 2))
        assert mac == want
        iv = self.cbc(self.k_enc, bytes(16), b"\xa5\x5a" + self.ti + self.ctr.to_bytes(2, "little") + bytes(8), True)
        plain = self.cbc(self.k_enc, iv, enc, False)
        self.ctr += 1
        return plain.rstrip(b"\x00")[:-1]

    def ok_response(self):
        return bytes(pk.aes_cmac(self.k_mac, b"\x00" + self.ctr.to_bytes(2, "little") + self.ti)[i] for i in range(1, 16, 2))


def test_ev2_handshake_and_full_commands_against_independent_card():
    key = bytes(16)
    picc = FakePicc(key)
    rnd_a = bytes(range(50, 66))
    # reader side steps exactly as Card.authenticate does
    rnd_b = prov._cbc(key, bytes(16), prov._cbc(key, bytes(16), picc.rnd_b, True), False)
    assert rnd_b == picc.rnd_b
    reply = picc.auth(prov._cbc(key, bytes(16), rnd_a + prov._rotl(rnd_b), True))
    plain = prov._cbc(key, bytes(16), reply, False)
    assert plain[4:20] == prov._rotl(rnd_a)
    ses = prov.Ev2(plain[:4], *prov.session_keys(key, rnd_a, rnd_b))
    assert (ses.k_enc, ses.k_mac) == (picc.k_enc, picc.k_mac)

    _, off = prov.ndef_template()
    settings = prov.sdm_file_settings(off)
    body = ses.command(0x5F, b"\x02", settings)
    assert picc.open_command(0x5F, b"\x02", body) == settings
    assert ses.response(0x00, picc.ok_response()) == b"" and ses.ctr == 1
    new = bytes(range(16))
    data = prov.change_key_data(1, new, bytes(16))
    assert len(data) == 21 and data[:16] == new and data[16] == 1
    body = ses.command(0xC4, b"\x01", data)
    assert picc.open_command(0xC4, b"\x01", body) == data
    assert ses.response(0x00, picc.ok_response()) == b""
    with pytest.raises(IOError):
        ses.response(0x00, bytes(8))
    assert prov.change_key_data(0, new, bytes(16)) == new + b"\x01"


def test_provision_drives_apdus_in_order_with_scripted_card(monkeypatch):
    """Smoke test of the card flow with a scripted reader: checks command order and that it stops on errors."""
    class Conn:
        def __init__(self):
            self.seen = []
        def transmit(self, apdu):
            self.seen.append(bytes(apdu))
            return [], 0x6A, 0x82
    conn = Conn()
    with pytest.raises(IOError):
        prov.provision_card(conn, MASTER, log=lambda *_: None)
    assert conn.seen[0] == bytes.fromhex("FFCA000000")
