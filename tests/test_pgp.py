"""署名検証（core/pgp.py）のテスト。各ケースで pysequoia（開発用の依存）と結果が一致することも確かめる。"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from clipdl.core.pgp import (YTDLP_FINGERPRINT, SignatureError, load_public_key, load_ytdlp_key,
                             verify_detached)
from tests.pgp_forge import make_cert, make_key, make_signature, packet, subpacket

DATA = Path(__file__).parent / "data" / "ytdlp_2026.10.26"
SUMS = (DATA / "SHA2-256SUMS").read_bytes()
SIG = (DATA / "SHA2-256SUMS.sig").read_bytes()
KEY_FILE = Path(__file__).parents[1] / "src" / "clipdl" / "core" / "keys" / "yt-dlp_public.key"

ps = pytest.importorskip("pysequoia", reason="答え合わせには pysequoia（dev 依存）が必要")


# ---------------------------------------------------------------------- 比べる仕組み

def ours(data: bytes, sig: bytes, cert: bytes, fpr: str) -> bool:
    try:
        verify_detached(data, sig, load_public_key(cert, fpr))
        return True
    except SignatureError:
        return False


def sequoia(data: bytes, sig: bytes, cert: bytes) -> bool:
    try:
        c = ps.Cert.from_bytes(cert)
        s = ps.Sig.from_bytes(sig)
        ps.verify(bytes=data, store=lambda _ids: [c], signature=s)
        return True
    except Exception:  # noqa: BLE001 — pysequoia は読めない・検証失敗をすべて例外で返す
        return False


@pytest.fixture(scope="module")
def k1():
    return make_key()


@pytest.fixture(scope="module")
def k2():
    return make_key()


def check(data, sig, cert, fpr, expected: bool):
    a, b = ours(data, sig, cert, fpr), sequoia(data, sig, cert)
    assert (a, b) == (expected, expected), f"自作={a} pysequoia={b} 期待={expected}"


# ---------------------------------------------------------------------- 本物（yt-dlp 2026.10.26）

def test_real_ytdlp_signature_matches_sequoia():
    check(SUMS, SIG, KEY_FILE.read_bytes(), YTDLP_FINGERPRINT, True)


def test_real_info():
    info = verify_detached(SUMS, SIG, load_ytdlp_key())
    assert info.hash_name == "SHA512" and info.issuer_fingerprint == YTDLP_FINGERPRINT


@pytest.mark.parametrize("mutate", [
    lambda d: d + b"\n",
    lambda d: d.replace(b"e51e", b"e51f", 1),
    lambda d: d[:-1],
    lambda d: b"",
])
def test_real_tampered_data(mutate):
    check(mutate(SUMS), SIG, KEY_FILE.read_bytes(), YTDLP_FINGERPRINT, False)


def test_real_tampered_signature_bytes():
    bad = bytearray(SIG)
    bad[-5] ^= 0x01  # 署名値の末尾を 1 ビット変える
    check(SUMS, bytes(bad), KEY_FILE.read_bytes(), YTDLP_FINGERPRINT, False)


def test_hashed_subpacket_area_is_covered_by_signature():
    """ハッシュ対象サブパケット（作成時刻など）を書き換えると失敗する＝正しくハッシュに含めている。"""
    raw = bytearray(SIG)
    i = raw.find(bytes.fromhex("0502"))  # 作成時刻サブパケット（長さ 5, 種類 2）
    assert i > 0
    raw[i + 2] ^= 0x01
    check(SUMS, bytes(raw), KEY_FILE.read_bytes(), YTDLP_FINGERPRINT, False)


# ---------------------------------------------------------------------- 作った鍵

def test_forged_valid_signature_is_accepted_by_both(k1):
    """テスト用の署名作成が正しいことの確認（両方とも成功）。"""
    data = b"hello clipdl\n"
    check(data, make_signature(k1, data), make_cert(k1), k1.fingerprint, True)


def test_other_key_signature_rejected(k1):
    """別の鍵で作った署名を、yt-dlp の鍵で検証すると失敗する。"""
    check(SUMS, make_signature(k1, SUMS), KEY_FILE.read_bytes(), YTDLP_FINGERPRINT, False)


def test_wrong_pinned_fingerprint_rejected(k1, k2):
    """鍵ファイルが差し替えられても、埋め込んだ指紋と違えば使わない。"""
    with pytest.raises(SignatureError, match="指紋"):
        load_public_key(make_cert(k2), k1.fingerprint)


def test_issuer_fingerprint_must_match(k1, k2):
    """署名は k1 で正しく作られているが、発行者の指紋が k2 になっている。"""
    data = b"x"
    sig = make_signature(k1, data, issuer=k2)
    assert not ours(data, sig, make_cert(k1), k1.fingerprint)
    assert not sequoia(data, sig, make_cert(k1))


def test_missing_issuer_rejected_by_ours(k1):
    """発行者の情報が無い署名は、自作の検証では拒否する（pysequoia より厳しい）。"""
    data = b"x"
    sig = make_signature(k1, data, include_issuer=False)
    assert not ours(data, sig, make_cert(k1), k1.fingerprint)


def test_unknown_critical_subpacket_rejected(k1):
    data = b"x"
    sig = make_signature(k1, data, extra_hashed=subpacket(100, b"\x01", critical=True))
    check(data, sig, make_cert(k1), k1.fingerprint, False)


def test_unknown_noncritical_subpacket_accepted(k1):
    data = b"x"
    sig = make_signature(k1, data, extra_hashed=subpacket(100, b"\x01", critical=False))
    check(data, sig, make_cert(k1), k1.fingerprint, True)


def test_critical_notation_rejected(k1):
    """critical な注釈（notation, 種類 20）は理解できないので拒否する。"""
    note = b"\x80\x00\x00\x00" + struct.pack(">HH", 4, 1) + b"test" + b"x"
    data = b"x"
    sig = make_signature(k1, data, extra_hashed=subpacket(20, note, critical=True))
    check(data, sig, make_cert(k1), k1.fingerprint, False)


def test_known_critical_subpacket_accepted(k1):
    """作成時刻に critical が付いていても、理解できる種類なので受け入れる。"""
    data = b"x"
    sig = make_signature(k1, data, include_created=False,
                         extra_hashed=subpacket(2, struct.pack(">I", k1.created + 10), critical=True))
    check(data, sig, make_cert(k1), k1.fingerprint, True)


# ---------------------------------------------------------------------- 形式違い

@pytest.mark.parametrize("kwargs", [
    {"h_algo": 2},        # SHA-1
    {"h_algo": 1},        # MD5
    {"pk_algo": 17},      # 鍵の種類が DSA と書かれている
    {"include_created": False},  # 作成時刻なし
])
def test_format_variants_rejected(k1, kwargs):
    data = b"x"
    sig = make_signature(k1, data, **kwargs)
    check(data, sig, make_cert(k1), k1.fingerprint, False)


def test_text_signature_type_rejected_by_ours(k1):
    """署名種別 0x01（テキスト）は扱わない。"""
    data = b"x"
    assert not ours(data, make_signature(k1, data, sig_type=0x01), make_cert(k1), k1.fingerprint)


@pytest.mark.parametrize("sig", [
    SIG + SIG,      # 署名が 2 つ: pysequoia は「どれか 1 つが正しければ成功」
    SIG + b"\x00",  # 末尾に余分なデータ: pysequoia は無視する
])
def test_stricter_than_sequoia(sig):
    """自作は「署名パケットがちょうど 1 つ・余分なデータなし」に限るので拒否する。
    pysequoia は受け入れる（ここは意図して厳しくしている差）。"""
    assert not ours(SUMS, sig, KEY_FILE.read_bytes(), YTDLP_FINGERPRINT)
    assert sequoia(SUMS, sig, KEY_FILE.read_bytes())  # pysequoia の挙動が変わったら気付けるように


@pytest.mark.parametrize("sig", [
    SIG[:len(SIG) // 2],                       # 途中で切れている
    b"",                                       # 空
    b"-----BEGIN PGP SIGNATURE-----\n\n!!!!\n-----END PGP SIGNATURE-----\n",  # 壊れた ASCII 形式
    packet(2, b"\x03" + SIG[3:40]),            # v3 署名
])
def test_malformed_signature_rejected(sig):
    check(SUMS, sig, KEY_FILE.read_bytes(), YTDLP_FINGERPRINT, False)


def test_expired_signature_rejected(k1):
    data = b"x"
    created = k1.created + 10
    sig = make_signature(k1, data, created=created, extra_hashed=subpacket(3, struct.pack(">I", 60)))
    check(data, sig, make_cert(k1), k1.fingerprint, False)


def test_armored_signature_accepted():
    import base64
    from clipdl.core.pgp import _crc24
    b64 = base64.b64encode(SIG).decode()
    armored = ("-----BEGIN PGP SIGNATURE-----\n\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
               + "\n=" + base64.b64encode(_crc24(SIG).to_bytes(3, "big")).decode() + "\n-----END PGP SIGNATURE-----\n")
    check(SUMS, armored.encode(), KEY_FILE.read_bytes(), YTDLP_FINGERPRINT, True)


def test_embedded_key_fingerprint():
    assert load_ytdlp_key().fingerprint == YTDLP_FINGERPRINT
