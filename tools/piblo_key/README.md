# Piblo Key provisioning

Factory tool for the Piblo Key, an NXP NTAG 424 DNA card. Contract: `docs/protocol/AUTH.md`.

```
export PIBLO_KEY_MASTER=<32 hex>          # the Piblo master key; never a file in the repo
python tools/piblo_key/provision.py --dry-run [--uid 04DE5F1EACC040] [--csv piblo_keys.csv]
python tools/piblo_key/provision.py [--reader 0]      # real card, needs: pip install pyscard
```

## What it does

1. Reads the UID, derives keys 0 (app), 1 (meta) and 2 (file) from the master (`mazhost/piblo_key.py`).
2. Writes the NDEF URL template `https://piblo.co.uk/k?p=<32 zeros>&c=<16 zeros>` (ISO UpdateBinary, file 02 is free-write on a new card).
3. AuthenticateEV2First with the factory key (all zeros), then ChangeFileSettings (file 02: SDM on, PICCData encrypted with key 1, MAC with key 2, empty MAC input), then ChangeKey for keys 1, 2 and finally 0.
4. Reads the NDEF file back and verifies the mirrored `p`/`c` with the new keys.
5. Appends `uid, PK1-... enrolment code, NDEF hex, offsets, status` to the CSV (created `0600`). **The CSV contains card keys**: keep it off shared drives and out of git. Feed the `enrol_code` column to the insert printer's QR generator.

`--dry-run` needs no reader: it prints the keys' derived enrolment code, NDEF bytes, SDM offsets (p at 23, c at 58) and writes a CSV row.

## Tested here, and what is not

Unit-tested without hardware (`host/tests/test_auth_provision.py`, `test_auth_key.py`): diversification, NDEF bytes and offsets, ChangeFileSettings payload, EV2 session key layout, Full-mode command MAC and encryption against an independently written card model, the NXP AN12196 SUN example.

**Not tested against a real card or reader** (no hardware in CI): all of `Card` and `provision_card`: PC/SC transport, the `FF CA` UID read, ISO select and update/read binary, the live AuthenticateEV2First exchange, and ChangeKey. Details to confirm on the first real card before a batch run:

- the exact `SDMAccessRights` byte order (`12 FF` here) and `FileOption`/`AccessRights` values;
- the ChangeKey CRC (`~crc32` of the new key, JAMCRC) and that key 0's ChangeKey response carries no MAC;
- that file 02 is free-write on your batch's factory state.

Provision one spare card first and tap it with a phone. Do not run on a batch until read-back verification passes.
