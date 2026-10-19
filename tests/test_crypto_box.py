import pytest

from acumen import crypto_box as cb


def test_roundtrip_and_check():
    meta, key = cb.make_enc_meta("correct horse battery staple", salt=b"0123456789abcdef", iterations=1000)
    blob = cb.encrypt_json(key, {"a": 1, "b": [1.5, None], "s": "Pakistan Rs 250"})
    assert cb.decrypt_json(key, blob) == {"a": 1, "b": [1.5, None], "s": "Pakistan Rs 250"}
    assert cb.key_from_meta(meta, "correct horse battery staple") == key
    assert cb.key_from_meta(meta, "wrong") is None


def test_tamper_detected():
    _, key = cb.make_enc_meta("pw", salt=b"0123456789abcdef", iterations=1000)
    blob = bytearray(cb.encrypt_json(key, {"x": 1}))
    blob[-1] ^= 1
    with pytest.raises(Exception):
        cb.decrypt_json(key, bytes(blob))


def test_nan_rejected():
    _, key = cb.make_enc_meta("pw", salt=b"0123456789abcdef", iterations=1000)
    with pytest.raises(ValueError):
        cb.encrypt_json(key, {"x": float("nan")})


def test_salt_is_stable_so_devices_keep_their_key_after_every_rebuild():
    m1, k1 = cb.make_enc_meta("my passphrase", iterations=1000)
    m2, k2 = cb.make_enc_meta("my passphrase", iterations=1000)
    assert m1["salt"] == m2["salt"] and k1 == k2
    m3, k3 = cb.make_enc_meta("another passphrase", iterations=1000)
    assert m3["salt"] != m1["salt"] and k3 != k1
    # but ciphertext is still randomised (fresh IV per file)
    assert cb.encrypt_json(k1, {"x": 1}) != cb.encrypt_json(k1, {"x": 1})
