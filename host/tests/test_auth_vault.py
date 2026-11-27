import json
import os
import stat
import sys

import pytest

from mazhost import auth_totp
from mazhost.auth_vault import Vault, VaultError

SECRET = b"super-secret-bytes-1234"


def account(**kw):
    return {"issuer": "GitHub", "label": "maz", "secret": SECRET, "digits": 6, "period": 30, "algorithm": "SHA1", **kw}


def test_roundtrip_encrypted_at_rest_and_private(tmp_path):
    v = Vault(tmp_path / "auth", use_dpapi=False)
    added, skipped = v.add_accounts([account()])
    assert skipped == [] and "secret" not in added[0]
    raw = (tmp_path / "auth" / "vault.json").read_bytes()
    assert b"GitHub" not in raw and SECRET not in raw
    assert (tmp_path / "auth" / "vault.key").read_bytes() not in raw
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "auth" / "vault.key").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "auth" / "vault.json").st_mode) == 0o600
    again = Vault(tmp_path / "auth", use_dpapi=False)          # new process: key comes from the key file
    pub, secret = again.account_secret(added[0]["id"])
    assert secret == SECRET and pub["issuer"] == "GitHub"
    assert not list((tmp_path / "auth").glob("*.tmp"))


def test_duplicates_patch_delete(tmp_path):
    v = Vault(tmp_path, use_dpapi=False)
    a = v.add_accounts([account()])[0][0]
    assert v.add_accounts([account()]) == ([], [{"label": "GitHub: maz", "reason": "duplicate"}])
    assert len(v.add_accounts([account(label="other")])[0]) == 1
    assert v.patch_account(a["id"], "GH", None, ["github"])["match"] == ["github"]
    v.delete_account(a["id"])
    with pytest.raises(auth_totp.AuthError):
        v.account_secret(a["id"])


def test_tamper_and_wrong_key_fail_closed_without_leaking(tmp_path):
    v = Vault(tmp_path, use_dpapi=False)
    v.add_accounts([account()])
    path = tmp_path / "vault.json"
    doc = json.loads(path.read_text())
    doc["c"] = doc["c"][:-4] + "AAAA"
    path.write_text(json.dumps(doc))
    with pytest.raises(VaultError) as e:
        Vault(tmp_path, use_dpapi=False).read()
    assert SECRET.decode() not in str(e.value)


def test_prefs_defaults(tmp_path):
    v = Vault(tmp_path, use_dpapi=False)
    assert v.prefs() == {"autosuggest": True, "require_key_for_show": False}
    assert v.set_prefs(require_key_for_show=True)["require_key_for_show"] is True
    assert not (tmp_path / "vault.key").exists() or v.prefs()["autosuggest"] is True
