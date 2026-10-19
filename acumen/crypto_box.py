"""Optional encryption of the published data files.

Why: the site is served from GitHub Pages, which is public on the free plan. Market data belongs to its
publishers and Acumen's ideas are the owner's private edge, so every data file can be published as
ciphertext. The owner types a passphrase once per device; the browser decrypts locally.

Format (see docs/DATA_CONTRACT.md):

* key   = PBKDF2-HMAC-SHA256(passphrase UTF-8, salt, iterations) -> 32 bytes
* file  = iv(12 bytes) || AES-256-GCM(ciphertext || 16 byte tag) of gzip(utf-8 JSON)
* check = same layout, no gzip, plaintext b"acumen-ok"; lets the client verify a passphrase quickly

The browser side uses WebCrypto (PBKDF2 + AES-GCM) and DecompressionStream, so nothing here needs a library.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 600_000
CHECK_PLAINTEXT = b"acumen-ok"
IV_LEN = 12


def stable_salt(passphrase: str) -> bytes:
    """A salt that depends only on the passphrase.

    The site is rebuilt many times; a fresh random salt each time would change the derived key and force the
    owner to type the passphrase again on every device after every refresh. One owner, one passphrase: a
    deterministic, app-specific salt costs nothing in security (every guess still needs the full PBKDF2 work).
    """
    return hashlib.sha256(b"acumen/v1/salt/" + passphrase.encode("utf-8")).digest()[:16]


def store_key(passphrase: str, iterations: int = ITERATIONS) -> bytes:
    """The key that seals the history store kept between runs. Derived separately from the published files' key: the browser
    never needs it, so the two can never stand in for each other."""
    salt = hashlib.sha256(b"acumen/v1/store/" + passphrase.encode("utf-8")).digest()[:16]
    return derive_key(passphrase, salt, iterations)


def derive_key(passphrase: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_bytes(key: bytes, plaintext: bytes, compress: bool = True) -> bytes:
    data = gzip.compress(plaintext, compresslevel=6, mtime=0) if compress else plaintext
    iv = os.urandom(IV_LEN)
    return iv + AESGCM(key).encrypt(iv, data, None)


def decrypt_bytes(key: bytes, blob: bytes, compressed: bool = True) -> bytes:
    if len(blob) < IV_LEN + 16:
        raise ValueError("ciphertext too short")
    iv, body = blob[:IV_LEN], blob[IV_LEN:]
    data = AESGCM(key).decrypt(iv, body, None)
    return gzip.decompress(data) if compressed else data


def encrypt_json(key: bytes, obj: Any) -> bytes:
    text = json.dumps(obj, separators=(",", ":"), allow_nan=False, ensure_ascii=False)
    return encrypt_bytes(key, text.encode("utf-8"), compress=True)


def decrypt_json(key: bytes, blob: bytes) -> Any:
    return json.loads(decrypt_bytes(key, blob, compressed=True).decode("utf-8"))


def make_enc_meta(passphrase: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> tuple[dict[str, Any], bytes]:
    """Return ``(enc block for meta.json, derived key)``."""
    salt = salt or stable_salt(passphrase)
    key = derive_key(passphrase, salt, iterations)
    check = encrypt_bytes(key, CHECK_PLAINTEXT, compress=False)
    meta = {
        "alg": "AES-256-GCM",
        "kdf": "PBKDF2-SHA256",
        "iter": iterations,
        "salt": base64.b64encode(salt).decode("ascii"),
        "gzip": True,
        "check": base64.b64encode(check).decode("ascii"),
    }
    return meta, key


def key_from_meta(enc_meta: dict[str, Any], passphrase: str) -> bytes | None:
    """Derive the key for an existing ``enc`` block. Returns None when the passphrase is wrong."""
    salt = base64.b64decode(enc_meta["salt"])
    key = derive_key(passphrase, salt, int(enc_meta["iter"]))
    try:
        ok = decrypt_bytes(key, base64.b64decode(enc_meta["check"]), compressed=False)
    except (InvalidTag, ValueError):
        return None
    return key if ok == CHECK_PLAINTEXT else None
