"""ZEPAY V3 security primitives.

ChaCha20-Poly1305 AEAD (RFC 8439) + HKDF-SHA256 (RFC 5869) + SecretBox.
Ported unchanged from the audited ZEPAY v2 monolith (legacy/zepay_v2.py),
which is covered by RFC test vectors at every boot. Pure stdlib: no
third-party crypto dependency.

SECRET POLICY (§50-52):
  * secrets are encrypted at rest under a per-install master key (0600)
  * secrets are NEVER logged, NEVER sent to the frontend, NEVER in git
  * exchange withdrawal permission is FORBIDDEN and refused at connect time
  * wallet private keys / seed phrases are NEVER requested or stored
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import threading


def b64e(b: bytes) -> str:
    """URL-safe base64 without padding noise (used for secret tokens)."""
    return base64.urlsafe_b64encode(b).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _rotl32(v, c):
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _chacha20_block(key, counter, nonce):
    """RFC 8439 §2.3 — one 64-byte block. key: 32 bytes, nonce: 12 bytes."""
    consts = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]
    k = list(struct.unpack("<8I", key))
    n = list(struct.unpack("<3I", nonce))
    state = consts + k + [counter & 0xFFFFFFFF] + n
    x = list(state)
    QR = [  # quarter-round column/ diagonal indices
        (0, 4, 8, 12),
        (1, 5, 9, 13),
        (2, 6, 10, 14),
        (3, 7, 11, 15),
        (0, 5, 10, 15),
        (1, 6, 11, 12),
        (2, 7, 8, 13),
        (3, 4, 9, 14),
    ]

    def qr(a, b, c, d):
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF
        x[d] ^= x[a]
        x[d] = _rotl32(x[d], 16)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF
        x[b] ^= x[c]
        x[b] = _rotl32(x[b], 12)
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF
        x[d] ^= x[a]
        x[d] = _rotl32(x[d], 8)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF
        x[b] ^= x[c]
        x[b] = _rotl32(x[b], 7)

    for _ in range(10):
        for a, b, c, d in QR:
            qr(a, b, c, d)
    out = struct.pack("<16I", *[(x[i] + state[i]) & 0xFFFFFFFF for i in range(16)])
    return out


def chacha20_xor(key, counter_start, nonce, data):
    """XOR data with the ChaCha20 keystream starting at block counter_start."""
    if len(key) != 32:
        raise ValueError("chacha20 key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("chacha20 nonce must be 12 bytes")
    out = bytearray()
    for i in range(0, len(data), 64):
        block = _chacha20_block(key, counter_start + (i // 64), nonce)
        chunk = data[i : i + 64]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block, strict=False)))
    return bytes(out)


# ---- Poly1305 (RFC 8439 §2.5) ----

_P1305 = (1 << 130) - 5


def poly1305_mac(key, msg):
    """RFC 8439 §2.5. key: 32 bytes. Returns 16-byte tag."""
    if len(key) != 32:
        raise ValueError("poly1305 key must be 32 bytes")
    r = struct.unpack("<4I", key[:16])
    r = (
        (r[0] & 0x0FFFFFFF)
        | ((r[1] & 0x0FFFFFFC) << 32)
        | ((r[2] & 0x0FFFFFFC) << 64)
        | ((r[3] & 0x0FFFFFFC) << 96)
    )
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i : i + 16]
        n = int.from_bytes(block + b"\x01", "little")
        acc = (acc + n) * r % _P1305
    acc = (acc + s) & ((1 << 128) - 1)
    return acc.to_bytes(16, "little")


def _poly1305_key_gen(key, nonce):
    """RFC 8439 §2.6: one-time Poly1305 key = first 32 bytes of ChaCha20 block 0."""
    block = _chacha20_block(key, 0, nonce)
    return block[:32]


def aead_chacha20_poly1305_encrypt(key, nonce, plaintext, aad=b""):
    """RFC 8439 §2.8. key 32B, nonce 12B. Returns ciphertext||tag."""
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    otk = _poly1305_key_gen(key, nonce)
    ct = chacha20_xor(key, 1, nonce, plaintext)
    mac_data = aead_pad(aad) + aead_pad(ct) + struct.pack("<QQ", len(aad), len(ct))
    tag = poly1305_mac(otk, mac_data)
    return ct + tag


def aead_chacha20_poly1305_decrypt(key, nonce, ciphertext_and_tag, aad=b""):
    """RFC 8439 §2.8. Raises ValueError on auth failure."""
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    if len(ciphertext_and_tag) < 16:
        raise ValueError("ciphertext too short")
    ct, tag = ciphertext_and_tag[:-16], ciphertext_and_tag[-16:]
    otk = _poly1305_key_gen(key, nonce)
    mac_data = aead_pad(aad) + aead_pad(ct) + struct.pack("<QQ", len(aad), len(ct))
    expect = poly1305_mac(otk, mac_data)
    if not hmac.compare_digest(expect, tag):
        raise ValueError("AEAD authentication failed (wrong key or tampered data)")
    return chacha20_xor(key, 1, nonce, ct)


def aead_pad(data):
    if len(data) % 16 == 0:
        return data
    return data + b"\x00" * (16 - (len(data) % 16))


# ---- HKDF-SHA256 (RFC 5869) ----


def hkdf_sha256(ikm, salt=b"", info=b"", length=32):
    if not salt:
        salt = b"\x00" * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    t = b""
    i = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
        i += 1
    return okm[:length]


# ---- SecretBox: per-install master key + AEAD storage helpers ----


class SecretBox:
    """Encrypts secrets at rest. Master key generated once per install and
    stored with 0600 permissions. Subkeys derived per purpose via HKDF."""

    def __init__(self, master_key_path: str):
        if not master_key_path:
            raise ValueError("master_key_path is required (no implicit global path in V3)")
        self.master_key_path = master_key_path
        self._key = self._load_or_create()
        self._lock = threading.RLock()

    def _load_or_create(self):
        os.makedirs(os.path.dirname(self.master_key_path), exist_ok=True)
        if os.path.exists(self.master_key_path):
            with open(self.master_key_path, "rb") as f:
                key = f.read().strip()
            if len(key) == 64:
                return bytes.fromhex(key.decode("ascii"))
            raise RuntimeError(
                "master key file is corrupt; restore from backup or delete "
                f"{self.master_key_path} (all stored secrets will be lost)"
            )
        key = secrets.token_hex(32)
        fd = os.open(self.master_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key.encode("ascii"))
        finally:
            os.close(fd)
        try:
            os.chmod(self.master_key_path, 0o600)
        except Exception:
            pass
        return bytes.fromhex(key)

    def _purpose_key(self, purpose):
        return hkdf_sha256(
            self._key, salt=b"zepay-v2", info=("secretbox:" + purpose).encode(), length=32
        )

    def encrypt(self, plaintext, purpose="general"):
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        nonce = secrets.token_bytes(12)
        key = self._purpose_key(purpose)
        ct = aead_chacha20_poly1305_encrypt(key, nonce, plaintext, aad=purpose.encode())
        return "v1." + b64e(nonce) + "." + b64e(ct)

    def decrypt(self, token, purpose="general"):
        if not token or not isinstance(token, str) or not token.startswith("v1."):
            raise ValueError("invalid secret token format")
        try:
            _, nb, cb = token.split(".", 2)
            nonce = b64d(nb)
            ct = b64d(cb)
        except Exception:
            raise ValueError("invalid secret token format") from None
        key = self._purpose_key(purpose)
        return aead_chacha20_poly1305_decrypt(key, nonce, ct, aad=purpose.encode()).decode("utf-8")

    def self_test(self):
        """Round-trip + RFC 8439 §A.5 AEAD vector. Raises on failure."""
        tok = self.encrypt("zepay-secret-roundtrip", purpose="selftest")
        assert self.decrypt(tok, purpose="selftest") == "zepay-secret-roundtrip"
        key = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = bytes.fromhex("070000004041424344454647")
        aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        pt = (
            b"Ladies and Gentlemen of the class of '99: If I could offer you only one "
            b"tip for the future, sunscreen would be it."
        )
        ct = aead_chacha20_poly1305_encrypt(key, nonce, pt, aad)
        expect_ct = bytes.fromhex(
            "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
            "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
            "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
            "3ff4def08e4b7a9de576d26586cec64b6116"
        )
        assert ct[:-16] == expect_ct, "RFC 8439 A.5 ciphertext mismatch"
        expect_tag = bytes.fromhex("1ae10b594f09e26a7e902ecbd0600691")
        assert ct[-16:] == expect_tag, "RFC 8439 A.5 tag mismatch"
