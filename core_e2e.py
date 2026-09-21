"""phix 传输与凭证工具（站点代理本地副本）。

复制自 D:\\moodsite\\web\\core\\phix_e2e.py，仅改 import 路径为本地。
对 phix 的所有请求都必须走信封（X-Phix-Enc: 1），公钥固定到 website/.phix_pubkey。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import unicodedata
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (X25519PrivateKey,
                                                              X25519PublicKey)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---- 协议常量（与规范 §2.4 / §10 一致，改这里 = 改协议）----
ENVELOPE_PREFIX = "PHIX1."
SEAL_INFO = b"phix/v1/seal"
AUTH_INFO = b"phix/v1/auth"
ENC_INFO = b"phix/v1/enc"
REQ_AAD_PREFIX = b"phix/v1/req|"
KDF_ALGO_V1 = "scrypt-n15-r8-p1"
KDF_ALGO_V2 = "scrypt-hkdf-v2"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
EPK_LEN, NONCE_LEN, SK_LEN = 32, 12, 32

#: 与 JavaScript `String.prototype.trim()` 一致的空白集合（两端必须一样）
_JS_TRIM_CHARS = "".join(
    [chr(c) for c in range(0x09, 0x0E)] +
    ["\u0020", "\u00a0", "\u1680"] +
    [chr(c) for c in range(0x2000, 0x200B)] +
    ["\u2028", "\u2029", "\u202f", "\u205f", "\u3000", "\ufeff"]
)


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt,
                info=info).derive(ikm)


def derive_mk(passphrase: str, salt_hex: str) -> bytes:
    """口令 → MK（scrypt，两代 KDF 共用这一步）。"""
    pw = unicodedata.normalize("NFKC", passphrase).strip(_JS_TRIM_CHARS).encode("utf-8")
    return hashlib.scrypt(pw, salt=bytes.fromhex(salt_hex), n=SCRYPT_N, r=SCRYPT_R,
                          p=SCRYPT_P, dklen=32, maxmem=SCRYPT_MAXMEM)


def derive_auth_hash(passphrase: str, auth_salt_hex: str,
                     algo: str = KDF_ALGO_V2) -> bytes:
    """口令 → AuthHash（**这是要发给 phix 的东西**，不是口令本身）。"""
    if algo == KDF_ALGO_V1:
        raise ValueError("v1 账号没有 AuthHash（它只能比对口令原文）")
    raw = bytes.fromhex(auth_salt_hex)
    return _hkdf(derive_mk(passphrase, auth_salt_hex), raw, AUTH_INFO, 32)


def auth_hash_hex(passphrase: str, auth_salt_hex: str,
                  algo: str = KDF_ALGO_V2) -> str:
    return derive_auth_hash(passphrase, auth_salt_hex, algo).hex()


def uses_auth_hash(algo: str | None) -> bool:
    return (algo or KDF_ALGO_V2) != KDF_ALGO_V1


# ---------------- 应用层加密信封（规范 §10） ----------------

def seal_box(msg: bytes, recipient_pk: bytes) -> bytes:
    esk = X25519PrivateKey.generate()
    epk = esk.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    shared = esk.exchange(X25519PublicKey.from_public_bytes(recipient_pk))
    salt = epk + recipient_pk
    key = _hkdf(shared, salt, SEAL_INFO, 32)
    nonce = os.urandom(NONCE_LEN)
    return epk + nonce + AESGCM(key).encrypt(nonce, msg, salt)


def open_box(sealed: bytes, recipient_sk_raw: bytes, recipient_pk: bytes) -> bytes:
    """打开给自己的密封盒。"""
    if len(sealed) < EPK_LEN + NONCE_LEN + 16:
        raise ValueError("密封盒长度不对")
    epk = sealed[:EPK_LEN]
    nonce = sealed[EPK_LEN:EPK_LEN + NONCE_LEN]
    ct = sealed[EPK_LEN + NONCE_LEN:]
    sk = X25519PrivateKey.from_private_bytes(recipient_sk_raw)
    shared = sk.exchange(X25519PublicKey.from_public_bytes(epk))
    salt = epk + recipient_pk
    key = _hkdf(shared, salt, SEAL_INFO, 32)
    return AESGCM(key).decrypt(nonce, ct, salt)


def raw_private_bytes(sk: X25519PrivateKey) -> bytes:
    """X25519 私钥 → 32 字节原始字节。"""
    return sk.private_bytes(serialization.Encoding.Raw,
                            serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())


def env_aad(method: str, path: str) -> bytes:
    return REQ_AAD_PREFIX + method.encode("utf-8") + b"|" + path.encode("utf-8")


def make_envelope(server_pk: bytes, method: str, path: str, body,
                  query: str = "") -> tuple[dict, bytes]:
    sk = os.urandom(SK_LEN)
    inner = {"m": method, "p": path, "q": query, "b": body,
             "ts": int(time.time()), "nonce": b64e(os.urandom(16))}
    iv = os.urandom(NONCE_LEN)
    ct = AESGCM(sk).encrypt(iv, json.dumps(inner, ensure_ascii=False).encode("utf-8"),
                            env_aad(method, path))
    return {"sealed_sk": b64e(seal_box(sk, server_pk)), "iv": b64e(iv),
            "ct": b64e(ct)}, sk


def open_envelope_response(sk: bytes, method: str, path: str, envelope: dict) -> bytes:
    return AESGCM(sk).decrypt(b64d(envelope["iv"]), b64d(envelope["ct"]),
                              env_aad(method, path))


# ---------------- 服务器公钥固定 ----------------

class ServerKeyError(Exception):
    pass


def pin_file(base_dir: Path) -> Path:
    return Path(base_dir) / ".phix_pubkey"


def load_pinned(base_dir: Path) -> dict:
    try:
        return json.loads(pin_file(base_dir).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def pin_key(base_dir: Path, server: str, pk_hex: str) -> None:
    """把服务器公钥记下来；**已有且不一致就报错**。"""
    doc = load_pinned(base_dir)
    old = doc.get(server)
    if old and old != pk_hex:
        raise ServerKeyError(
            f"phix 服务器的加密公钥变了（{server}）！可能是服务器重装过，"
            "也可能是有人在中间冒充。确认无误后删掉 "
            f"{pin_file(base_dir)} 里对应的那条再试。")
    if old != pk_hex:
        doc[server] = pk_hex
        try:
            p = pin_file(base_dir)
            p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
            os.chmod(p, 0o600)
        except OSError:
            pass


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
