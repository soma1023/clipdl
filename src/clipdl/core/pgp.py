"""OpenPGP の分離署名（yt-dlp の SHA2-256SUMS.sig）を検証する最小限の実装。

依存は pycryptodomex だけ（yt-dlp が使うもので、すでに同梱されている）。
対応範囲をあえて絞り、少しでも想定と違えば「検証失敗」にする（失敗したら同梱版を使う安全側の設計）。

対応するもの:
  - 鍵: v4 公開鍵パケット、RSA（2048bit 以上）。アプリに埋め込んだ指紋と一致すること
  - 署名: v4 署名パケット 1 つだけ、署名種別 0x00（バイナリ文書）、RSA、SHA-256 / SHA-384 / SHA-512
  - サブパケット: 作成時刻（必須・ハッシュ対象側）、有効期限、発行者の鍵 ID、発行者の指紋
    それ以外のサブパケットは、critical（必ず理解すべき印）が付いていれば拒否する
  - 発行者の指紋（または鍵 ID）は、埋め込んだ指紋と一致すること（どちらも無ければ拒否）
ハッシュの対象: 署名対象のデータ ＋ 署名パケットの先頭からハッシュ対象サブパケットの末尾まで
  ＋ trailer（0x04 0xFF ＋ その長さ 4 バイト）。RFC 4880 §5.2.4 のとおり。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from Cryptodome.Hash import SHA256, SHA384, SHA512
from Cryptodome.PublicKey import RSA
from Cryptodome.Signature import pkcs1_15

# yt-dlp の署名鍵（Simon Sawicki (yt-dlp signing key)、RSA 4096、2023-02-26 作成）。
# yt-dlp リポジトリの public.key から計算し、keys.openpgp.org と keyserver.ubuntu.com の鍵とも一致を確認済み
YTDLP_FINGERPRINT = "AC0CBBE6848D6A873464AF4E57CF65933B5A7581"
YTDLP_KEY_FILE = Path(__file__).parent / "keys" / "yt-dlp_public.key"

_HASHES = {8: SHA256, 9: SHA384, 10: SHA512}
_RSA_ALGOS = {1, 3}            # RSA (Encrypt or Sign) / RSA Sign-Only
_SP_CREATED, _SP_EXPIRES, _SP_ISSUER, _SP_ISSUER_FPR = 2, 3, 16, 33
_KNOWN_SUBPACKETS = {_SP_CREATED, _SP_EXPIRES, _SP_ISSUER, _SP_ISSUER_FPR}
MIN_RSA_BITS = 2048


class SignatureError(Exception):
    """署名を検証できなかった（理由つき）。"""


# ---------------------------------------------------------------------- 低レベル


def dearmor(data: bytes) -> bytes:
    """ASCII 形式（-----BEGIN PGP ...）ならバイナリに戻す。バイナリならそのまま返す。"""
    if not data.lstrip().startswith(b"-----BEGIN PGP"):
        return data
    lines = data.decode("ascii", "strict").strip().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("-----BEGIN PGP"))
        end = next(i for i, l in enumerate(lines) if l.startswith("-----END PGP"))
    except StopIteration:
        raise SignatureError("ASCII 形式の区切りが正しくありません") from None
    body = lines[start + 1:end]
    if "" in body:  # ヘッダ（Comment: など）は最初の空行まで
        body = body[body.index("") + 1:]
    b64 = "".join(l.strip() for l in body if l.strip() and not l.startswith("="))
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("ASCII 形式の中身を読めません") from None
    crc = next((l for l in body if l.startswith("=")), None)
    if crc is not None and base64.b64decode(crc[1:]) != _crc24(raw).to_bytes(3, "big"):
        raise SignatureError("ASCII 形式のチェックサムが一致しません")
    return raw


def _crc24(data: bytes) -> int:
    crc = 0xB704CE
    for b in data:
        crc ^= b << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def iter_packets(data: bytes):
    """(タグ, 本体) を順に返す。長さが途中で切れていればエラー。分割長（partial）は扱わない。"""
    i, n = 0, len(data)
    while i < n:
        h = data[i]
        i += 1
        if not h & 0x80:
            raise SignatureError("パケットの形式が正しくありません")
        if h & 0x40:  # 新形式
            tag = h & 0x3F
            if i >= n:
                raise SignatureError("パケットが途中で切れています")
            o = data[i]
            i += 1
            if o < 192:
                ln = o
            elif o < 224:
                if i >= n:
                    raise SignatureError("パケットが途中で切れています")
                ln = ((o - 192) << 8) + data[i] + 192
                i += 1
            elif o == 255:
                if i + 4 > n:
                    raise SignatureError("パケットが途中で切れています")
                ln = struct.unpack(">I", data[i:i + 4])[0]
                i += 4
            else:
                raise SignatureError("分割された長さ（partial length）には対応していません")
        else:  # 旧形式
            tag, lt = (h >> 2) & 0x0F, h & 0x03
            size = {0: 1, 1: 2, 2: 4}.get(lt)
            if size is None:
                raise SignatureError("長さ不定のパケットには対応していません")
            if i + size > n:
                raise SignatureError("パケットが途中で切れています")
            ln = int.from_bytes(data[i:i + size], "big")
            i += size
        if i + ln > n:
            raise SignatureError("パケットが途中で切れています")
        yield tag, data[i:i + ln]
        i += ln


def _mpi(b: bytes, i: int) -> tuple[int, int]:
    if i + 2 > len(b):
        raise SignatureError("数値（MPI）が途中で切れています")
    bits = struct.unpack(">H", b[i:i + 2])[0]
    nbytes = (bits + 7) // 8
    if i + 2 + nbytes > len(b):
        raise SignatureError("数値（MPI）が途中で切れています")
    v = int.from_bytes(b[i + 2:i + 2 + nbytes], "big")
    if v.bit_length() != bits:
        raise SignatureError("数値（MPI）のビット数が正しくありません")
    return v, i + 2 + nbytes


def _subpackets(area: bytes) -> list[tuple[int, bool, bytes]]:
    """(種類, critical, 本体) の一覧。領域をちょうど使い切らなければエラー。"""
    out, i, n = [], 0, len(area)
    while i < n:
        o = area[i]
        i += 1
        if o < 192:
            ln = o
        elif o < 255:
            if i >= n:
                raise SignatureError("サブパケットが途中で切れています")
            ln = ((o - 192) << 8) + area[i] + 192
            i += 1
        else:
            if i + 4 > n:
                raise SignatureError("サブパケットが途中で切れています")
            ln = struct.unpack(">I", area[i:i + 4])[0]
            i += 4
        if ln < 1 or i + ln > n:
            raise SignatureError("サブパケットの長さが正しくありません")
        typ = area[i]
        out.append((typ & 0x7F, bool(typ & 0x80), area[i + 1:i + ln]))
        i += ln
    return out


# ---------------------------------------------------------------------- 鍵


@dataclass(frozen=True)
class PublicKey:
    fingerprint: str      # 40 桁の 16 進（大文字）
    n: int
    e: int
    created: int

    @property
    def key_id(self) -> str:
        return self.fingerprint[-16:]


def load_public_key(data: bytes, expected_fingerprint: str) -> PublicKey:
    """主鍵（最初の公開鍵パケット）を読み、指紋が期待どおりか確かめる。"""
    pk = next((body for tag, body in iter_packets(dearmor(data)) if tag == 6), None)
    if pk is None:
        raise SignatureError("公開鍵が見つかりません")
    if len(pk) < 6 or pk[0] != 4:
        raise SignatureError("v4 以外の鍵には対応していません")
    if pk[5] not in _RSA_ALGOS:
        raise SignatureError("RSA 以外の鍵には対応していません")
    created = struct.unpack(">I", pk[1:5])[0]
    n, j = _mpi(pk, 6)
    e, j = _mpi(pk, j)
    if j != len(pk):
        raise SignatureError("公開鍵パケットに余分なデータがあります")
    if n.bit_length() < MIN_RSA_BITS:
        raise SignatureError("鍵が短すぎます")
    fpr = hashlib.sha1(b"\x99" + struct.pack(">H", len(pk)) + pk).hexdigest().upper()
    if fpr != expected_fingerprint.replace(" ", "").upper():
        raise SignatureError(f"鍵の指紋が一致しません（{fpr}）")
    return PublicKey(fpr, n, e, created)


def load_ytdlp_key() -> PublicKey:
    return load_public_key(YTDLP_KEY_FILE.read_bytes(), YTDLP_FINGERPRINT)


# ---------------------------------------------------------------------- 署名


@dataclass
class SignatureInfo:
    created: int
    hash_name: str
    issuer_fingerprint: str | None
    issuer_key_id: str | None
    subpacket_types: list[int] = field(default_factory=list)


def verify_detached(data: bytes, signature: bytes, key: PublicKey, now: float | None = None) -> SignatureInfo:
    """分離署名を検証する。成功すれば署名の情報を返し、失敗すれば SignatureError を投げる。"""
    packets = list(iter_packets(dearmor(signature)))
    if len(packets) != 1 or packets[0][0] != 2:
        raise SignatureError("署名パケットがちょうど 1 つではありません")
    s = packets[0][1]
    if len(s) < 6 or s[0] != 4:
        raise SignatureError("v4 以外の署名には対応していません")
    sig_type, pk_algo, h_algo = s[1], s[2], s[3]
    if sig_type != 0x00:
        raise SignatureError(f"バイナリ文書の署名ではありません（種別 0x{sig_type:02x}）")
    if pk_algo not in _RSA_ALGOS:
        raise SignatureError("RSA 以外の署名には対応していません")
    if h_algo not in _HASHES:
        raise SignatureError(f"対応していないハッシュ方式です（{h_algo}）")
    hl = struct.unpack(">H", s[4:6])[0]
    if 6 + hl + 2 > len(s):
        raise SignatureError("署名パケットが途中で切れています")
    hashed_area = s[6:6 + hl]
    ul = struct.unpack(">H", s[6 + hl:8 + hl])[0]
    k = 8 + hl + ul
    if k + 2 > len(s):
        raise SignatureError("署名パケットが途中で切れています")
    unhashed_area = s[8 + hl:k]
    left16 = s[k:k + 2]
    sig_int, end = _mpi(s, k + 2)
    if end != len(s):
        raise SignatureError("署名パケットに余分なデータがあります")

    hashed = _subpackets(hashed_area)
    unhashed = _subpackets(unhashed_area)
    for typ, critical, _ in hashed + unhashed:
        if critical and typ not in _KNOWN_SUBPACKETS:
            raise SignatureError(f"知らない critical サブパケットがあります（種類 {typ}）")

    created = [body for typ, _, body in hashed if typ == _SP_CREATED]
    if len(created) != 1 or len(created[0]) != 4:
        raise SignatureError("署名の作成時刻がありません")
    created_at = struct.unpack(">I", created[0])[0]
    now = time.time() if now is None else now
    if created_at < key.created:
        raise SignatureError("署名が鍵より前に作られています")
    if created_at > now + 86400:
        raise SignatureError("署名の作成時刻が未来です")
    for typ, _, body in hashed:
        if typ == _SP_EXPIRES:
            if len(body) != 4:
                raise SignatureError("署名の有効期限が正しくありません")
            exp = struct.unpack(">I", body)[0]
            if exp and created_at + exp < now:
                raise SignatureError("署名の有効期限が切れています")

    # 発行者: 指紋（v4）または鍵 ID のどちらかが必要。あるものはすべて一致しなければならない
    fprs = [body for typ, _, body in hashed + unhashed if typ == _SP_ISSUER_FPR]
    ids = [body for typ, _, body in hashed + unhashed if typ == _SP_ISSUER]
    if not fprs and not ids:
        raise SignatureError("署名に発行者の情報がありません")
    for body in fprs:
        if len(body) != 21 or body[0] != 4 or body[1:].hex().upper() != key.fingerprint:
            raise SignatureError("署名の発行者の指紋が埋め込んだ鍵と一致しません")
    for body in ids:
        if len(body) != 8 or body.hex().upper() != key.key_id:
            raise SignatureError("署名の発行者の鍵 ID が埋め込んだ鍵と一致しません")

    # ハッシュ: データ ＋ 署名の先頭〜ハッシュ対象サブパケット ＋ trailer
    hashed_part = s[:6 + hl]
    h = _HASHES[h_algo].new(data + hashed_part + b"\x04\xff" + struct.pack(">I", len(hashed_part)))
    if h.digest()[:2] != left16:
        raise SignatureError("署名がデータと一致しません")
    size = (key.n.bit_length() + 7) // 8
    if sig_int >= key.n:
        raise SignatureError("署名の値が正しくありません")
    try:
        pkcs1_15.new(RSA.construct((key.n, key.e))).verify(h, sig_int.to_bytes(size, "big"))
    except (ValueError, TypeError):
        raise SignatureError("署名がデータと一致しません") from None
    return SignatureInfo(created_at, _HASHES[h_algo].__name__.split(".")[-1],
                         fprs[0][1:].hex().upper() if fprs else None, ids[0].hex().upper() if ids else None,
                         [t for t, _, _ in hashed + unhashed])
