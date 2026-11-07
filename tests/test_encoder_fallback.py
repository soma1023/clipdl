"""エンコード中に失敗したら次のエンコーダで自動的にやり直すこと（実際の ffmpeg を使う結合テスト）。

CLIPDL_TEST_FAIL_ENCODERS で指定したエンコーダは、本番のエンコードだけがわざと失敗する
（「使えるかのテスト」は通るので、選ばれてから失敗する状況を再現できる）。
"""
import subprocess

import pytest

from clipdl.core import ffmpeg as ff
from clipdl.core.downloader import ClipOutput, CancelToken, _maybe_cfr
from clipdl.core.models import CfrMode
from clipdl.core.paths import find_binary

pytestmark = pytest.mark.skipif(find_binary("ffmpeg") is None, reason="ffmpeg が無い")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("CLIPDL_TEST_FAIL_ENCODERS", raising=False)
    ff.configure_encoders()
    ff.reset_broken()
    yield
    monkeypatch.delenv("CLIPDL_TEST_FAIL_ENCODERS", raising=False)
    ff.configure_encoders()
    ff.reset_broken()


def _usable() -> list[str]:
    names = ff.available_encoder_names()
    return [e.name for e in ff.ENCODER_CANDIDATES if e.name in names and ff._test_encode(e)]


def _make_vfr(path):
    subprocess.run([find_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=d=4:r=30:s=320x240",
                    "-vf", "select='if(lt(t,2),not(mod(n,3)),1)'", "-fps_mode", "vfr",
                    "-c:v", "libopenh264", str(path)], check=True)


def test_failure_reason_and_classification():
    err = ("[h264_nvenc @ 00000275b29a4c00] InitializeEncoder failed: invalid param (8): Frame Dimension\n"
           "[vost#0:0] Error while opening encoder\n")
    assert ff.is_encoder_failure(err)
    assert ff.failure_reason(err).startswith("[h264_nvenc ] InitializeEncoder failed")
    assert not ff.is_encoder_failure("[https @ 01] HTTP error 403 Forbidden")
    assert ff.failure_reason("") == "ffmpeg がエラーで終了しました"


def test_cfr_falls_back_to_next_encoder(tmp_path, monkeypatch):
    usable = _usable()
    if len(usable) < 2:
        pytest.skip("使えるエンコーダが 2 つ以上必要")
    first, second = usable[0], usable[1]
    monkeypatch.setenv("CLIPDL_TEST_FAIL_ENCODERS", first)
    ff.configure_encoders()
    src = tmp_path / "v.mp4"
    _make_vfr(src)
    res = ClipOutput(src)
    _maybe_cfr(res, CfrMode.ALWAYS, 4, lambda *a, **k: None, CancelToken())
    assert res.encoder == second
    assert any(first in n and "やり直し" in n and "理由" in n for n in res.notes), res.notes
    assert first in ff._broken
    assert not (tmp_path / "v.vfr-src.mp4").exists()  # 中間ファイルは残らない
    out = ff.ffprobe_json(src)
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    assert v["r_frame_rate"] == v["avg_frame_rate"] == "30/1"


def test_all_encoders_fail_restores_original(tmp_path, monkeypatch):
    usable = _usable()
    if not usable:
        pytest.skip("使えるエンコーダが無い")
    monkeypatch.setenv("CLIPDL_TEST_FAIL_ENCODERS", ",".join(usable))
    ff.configure_encoders()
    src = tmp_path / "v.mp4"
    _make_vfr(src)
    before = src.read_bytes()
    with pytest.raises(ff.UserError) as ei:
        _maybe_cfr(ClipOutput(src), CfrMode.ALWAYS, 4, lambda *a, **k: None, CancelToken())
    assert ei.value.code == "no_encoder"
    assert src.read_bytes() == before  # 元のファイルに戻っている
