"""テスト専用: OpenPGP の鍵（自己署名つきの証明書）と分離署名を作る。

本物の yt-dlp の署名だけでは作れないケース（別の鍵、critical サブパケット、ハッシュ方式違いなど）を
作り、自作の検証と pysequoia の結果を比べるために使う。アプリ本体からは使わない。
"""
from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass

from Cryptodome.Hash import MD5, SHA1, SHA256, SHA512
from Cryptodome.PublicKey import RSA
from Cryptodome.Signature import pkcs1_15

HASHES = {1: MD5, 2: SHA1, 8: SHA256, 10: SHA512}


def mpi(v: int) -> bytes:
    b = v.to_bytes((v.bit_length() + 7) // 8, "big")
    return struct.pack(">H", v.bit_length()) + b


def packet(tag: int, body: bytes) -> bytes:
    """新形式のパケットにする。"""
    n = len(body)
    if n < 192:
        ln = bytes([n])
    elif n < 8384:
        n2 = n - 192
        ln = bytes([(n2 >> 8) + 192, n2 & 0xFF])
    else:
        ln = b"\xff" + struct.pack(">I", n)
    return bytes([0xC0 | tag]) + ln + body


def subpacket(typ: int, body: bytes, critical: bool = False) -> bytes:
    data = bytes([typ | (0x80 if critical else 0)]) + body
    assert len(data) < 192
    return bytes([len(data)]) + data


@dataclass
class Key:
    rsa: RSA.RsaKey
    created: int
    body: bytes        # 公開鍵パケットの本体

    @property
    def fingerprint(self) -> str:
        return hashlib.sha1(b"\x99" + struct.pack(">H", len(self.body)) + self.body).hexdigest().upper()

    @property
    def key_id(self) -> bytes:
        return bytes.fromhex(self.fingerprint[-16:])


def make_key(bits: int = 3072, created: int | None = None) -> Key:
    rsa = RSA.generate(bits)
    created = int(time.time()) - 3600 if created is None else created
    body = b"\x04" + struct.pack(">I", created) + b"\x01" + mpi(rsa.n) + mpi(rsa.e)
    return Key(rsa, created, body)


def _sign(key: Key, prefix: bytes, sig_type: int, h_algo: int, hashed: bytes, unhashed: bytes,
          pk_algo: int = 1) -> bytes:
    head = bytes([4, sig_type, pk_algo, h_algo]) + struct.pack(">H", len(hashed)) + hashed
    h = HASHES[h_algo].new(prefix + head + b"\x04\xff" + struct.pack(">I", len(head)))
    sig = pkcs1_15.new(key.rsa).sign(h)
    return head + struct.pack(">H", len(unhashed)) + unhashed + h.digest()[:2] + mpi(int.from_bytes(sig, "big"))


def make_cert(key: Key, uid: str = "test <test@example.invalid>") -> bytes:
    """公開鍵 ＋ ユーザー ID ＋ 自己署名（0x13）。pysequoia に読ませるため。"""
    uid_b = uid.encode()
    prefix = (b"\x99" + struct.pack(">H", len(key.body)) + key.body
              + b"\xb4" + struct.pack(">I", len(uid_b)) + uid_b)
    hashed = (subpacket(2, struct.pack(">I", key.created)) + subpacket(27, b"\x03")
              + subpacket(33, b"\x04" + bytes.fromhex(key.fingerprint)))
    sig = _sign(key, prefix, 0x13, 10, hashed, subpacket(16, key.key_id))
    return packet(6, key.body) + packet(13, uid_b) + packet(2, sig)


def make_signature(key: Key, data: bytes, *, sig_type: int = 0x00, h_algo: int = 10,
                   extra_hashed: bytes = b"", issuer: Key | None = None, created: int | None = None,
                   include_issuer: bool = True, include_created: bool = True, pk_algo: int = 1) -> bytes:
    """分離署名（署名パケット 1 つ）を作る。issuer で発行者の情報だけ別の鍵にできる。"""
    who = issuer or key
    created = int(time.time()) - 60 if created is None else created
    hashed = b""
    if include_created:
        hashed += subpacket(2, struct.pack(">I", created))
    if include_issuer:
        hashed += subpacket(33, b"\x04" + bytes.fromhex(who.fingerprint))
    hashed += extra_hashed
    unhashed = subpacket(16, who.key_id) if include_issuer else b""
    return packet(2, _sign(key, data, sig_type, h_algo, hashed, unhashed, pk_algo))
