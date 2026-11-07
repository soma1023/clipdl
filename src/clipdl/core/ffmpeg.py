"""ffmpeg / ffprobe の検出、エンコーダ選択、実行（進捗つき）。"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .errors import Cancelled, UserError
from .paths import env, find_binary

log = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def ffmpeg_path() -> str:
    p = find_binary("ffmpeg")
    if not p:
        raise UserError("ffmpeg_missing", "ffmpeg が見つかりません。アプリを再インストールしてください。",
                        "ffmpeg was not found. Please reinstall the app.", "ffmpeg not found")
    return p


def ffprobe_path() -> str:
    p = find_binary("ffprobe")
    if not p:
        raise UserError("ffmpeg_missing", "ffprobe が見つかりません。アプリを再インストールしてください。",
                        "ffprobe was not found. Please reinstall the app.", "ffprobe not found")
    return p


# ---------------------------------------------------------------- エンコーダ

@dataclass(frozen=True)
class Encoder:
    name: str
    args: tuple[str, ...]   # 画質関連の引数（-c:v は含まない）
    label: str


# 選ぶ順番（PLAN.md §4.3）。GPU → OS 標準 → ソフトウェア
ENCODER_CANDIDATES: tuple[Encoder, ...] = (
    Encoder("h264_nvenc", ("-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0"), "NVIDIA NVENC"),
    Encoder("h264_qsv", ("-preset", "medium", "-global_quality", "20"), "Intel Quick Sync"),
    Encoder("h264_amf", ("-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "21"), "AMD AMF"),
    Encoder("h264_mf", ("-rate_control", "u_vbr", "-b:v", "16M", "-hw_encoding", "0"), "Windows Media Foundation"),
    Encoder("libopenh264", ("-b:v", "16M", "-maxrate", "24M"), "OpenH264"),
    Encoder("libx264", ("-preset", "medium", "-crf", "18"), "x264"),
)


def _run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NO_WINDOW)


@lru_cache(maxsize=1)
def available_encoder_names() -> frozenset[str]:
    r = _run([ffmpeg_path(), "-hide_banner", "-encoders"], timeout=30)
    names = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return frozenset(names)


def _test_encode(enc: Encoder) -> bool:
    """短いテストエンコードで、実際に使えるか確かめる（GPU が無いと失敗する）。"""
    args = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=black:s=320x240:r=30:d=0.5", "-pix_fmt", "yuv420p",
            "-c:v", enc.name, *enc.args, "-f", "null", "-"]
    try:
        r = _run(args, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    ok = r.returncode == 0
    log.debug("encoder test %s: %s %s", enc.name, "ok" if ok else "ng", r.stderr.strip()[-300:])
    return ok


ENCODER_NAMES = tuple(e.name for e in ENCODER_CANDIDATES)

# エンコーダ選択の上書き（CLI の --encoder / --disable-encoder、または環境変数）
#   CLIPDL_ENCODER=h264_qsv                 … これだけを使う
#   CLIPDL_DISABLE_ENCODERS=h264_nvenc,h264_qsv … これらを候補から外す（テスト用）
#   CLIPDL_TEST_FAIL_ENCODERS=h264_nvenc   … 本番のエンコードだけをわざと失敗させる（自動切り替えのテスト用）
_forced: str | None = None
_disabled: frozenset[str] = frozenset()
_broken: dict[str, str] = {}   # エンコード中に失敗したエンコーダ → 理由（このプロセスの間は使わない）
_test_fail: frozenset[str] = frozenset()


def _split(v: str | None) -> frozenset[str]:
    return frozenset(x.strip() for x in (v or "").split(",") if x.strip())


def configure_encoders(force: str | None = None, disable: list[str] | tuple[str, ...] | None = None) -> None:
    """エンコーダ選択を上書きする（引数 > 環境変数）。選択結果のキャッシュも消す。"""
    global _forced, _disabled, _test_fail
    _test_fail = _split(env("CLIPDL_TEST_FAIL_ENCODERS"))
    f = (force or "").strip() or env("CLIPDL_ENCODER")
    _forced = None if f in (None, "", "auto") else f
    _disabled = frozenset(disable or ()) | _split(env("CLIPDL_DISABLE_ENCODERS"))
    unknown = ({_forced} if _forced else set()) | set(_disabled)
    unknown -= set(ENCODER_NAMES)
    if unknown:
        raise ValueError(f"不明なエンコーダです: {', '.join(sorted(unknown))}（{', '.join(ENCODER_NAMES)}）")
    select_encoder.cache_clear()


def candidate_order() -> list[Encoder]:
    """実際に試す順番（強制・無効化を反映）。"""
    return [e for e in ENCODER_CANDIDATES
            if (not _forced or e.name == _forced) and e.name not in _disabled and e.name not in _broken]


def mark_broken(enc: Encoder, reason: str) -> None:
    """エンコード中に失敗したエンコーダを候補から外し、次の select_encoder() で次の候補を選ばせる。"""
    _broken[enc.name] = reason
    log.warning("エンコーダ %s がエンコード中に失敗したため候補から外します: %s", enc.name, reason)
    select_encoder.cache_clear()


def reset_broken() -> None:
    _broken.clear()
    select_encoder.cache_clear()


# ネットワークや入力側の失敗。エンコーダを変えても直らないので、切り替えずにそのままエラーにする
_NON_ENCODER_FAILURE = re.compile(
    r"Server returned|HTTP error|Connection (?:reset|refused|timed out)|Failed to resolve|I/O error|"
    r"Input/output error|No such file|Invalid data found when processing input|No space left|Permission denied",
    re.IGNORECASE)
# エンコーダが原因と分かる失敗（ログの理由表示に使う）
_ENCODER_REASON = re.compile(
    r"(InitializeEncoder failed.*|Error while opening encoder.*|could not set output type.*|"
    r"Error initializing output stream.*|\[(?:h264_\w+|libopenh264|libx264)[^\]]*\].*"
    r"(?:error|failed|not support|unsupported).*)", re.IGNORECASE)


def is_encoder_failure(stderr: str) -> bool:
    """ffmpeg の失敗が「エンコーダを替えれば直る可能性がある」ものか。"""
    return not _NON_ENCODER_FAILURE.search(stderr or "")


def failure_reason(stderr: str) -> str:
    """ffmpeg の出力から、人が読める短い失敗理由を取り出す。"""
    text = stderr or ""
    m = _ENCODER_REASON.search(text)
    if m:
        line = m.group(1)
    else:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        line = lines[-1] if lines else "ffmpeg がエラーで終了しました"
    line = re.sub(r"@ 0x?[0-9a-fA-F]+", "", line)
    return line.strip()[:200]


@lru_cache(maxsize=1)
def select_encoder() -> Encoder:
    names = available_encoder_names()
    tried = []
    for enc in candidate_order():
        if enc.name not in names:
            tried.append(f"{enc.name}=ビルドに無し")
            continue
        if _test_encode(enc):
            skipped = [e.name for e in ENCODER_CANDIDATES if e.name in _disabled or e.name in _broken]
            log.info("H.264 エンコーダ: %s（%s）%s", enc.name, enc.label,
                     f" 無効化: {', '.join(skipped)}" if skipped else "")
            if tried:
                log.info("使えなかったエンコーダ: %s", ", ".join(tried))
            return enc
        tried.append(f"{enc.name}=テスト失敗")
    raise UserError("no_encoder", "使える H.264 エンコーダが見つかりません。高速モードをお使いください。",
                    "No usable H.264 encoder was found. Please use fast mode.",
                    f"tried: {tried}; disabled: {sorted(_disabled)}; failed during encode: {_broken}; "
                    f"forced: {_forced}")


try:  # 環境変数だけで指定された場合も反映する
    configure_encoders()
except ValueError as _e:
    log.warning("環境変数のエンコーダ指定を無視します: %s", _e)


def video_encode_args(fps: str | None, *, container: str = "mp4", keyint_seconds: float = 2.0,
                      encoder: Encoder | None = None) -> list[str]:
    """再エンコード時の出力引数。fps を渡すと CFR 化も同時に行う。"""
    enc = encoder or select_encoder()
    args = []
    if fps:
        args += ["-vf", f"fps={fps}", "-fps_mode", "cfr", "-r", fps]
        try:
            num, den = (fps.split("/") + ["1"])[:2]
            g = max(1, round(float(num) / float(den) * keyint_seconds))
            args += ["-g", str(g)]
        except ValueError:
            pass
    args += ["-c:v", enc.name, *enc.args, "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k"]
    if enc.name in _test_fail:
        # テスト用: どのエンコーダも初期化に失敗する画面サイズを指定する
        log.warning("テスト用にエンコーダ %s をわざと失敗させます（CLIPDL_TEST_FAIL_ENCODERS）", enc.name)
        args += ["-s", "2x2"]
    if container in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
    return args


# ---------------------------------------------------------------- probe

def ffprobe_json(path: str | Path) -> dict:
    r = _run([ffprobe_path(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
             timeout=60)
    if r.returncode != 0:
        raise UserError("probe_failed", "動画ファイルを解析できませんでした。", "Could not analyze the video file.",
                        r.stderr)
    return json.loads(r.stdout or "{}")


# ---------------------------------------------------------------- 実行（進捗つき）

ProgressFn = Callable[[float | None], None]


def run_with_progress(args: list[str], duration: float | None, on_progress: ProgressFn | None = None,
                      cancel: threading.Event | None = None, cwd: Path | None = None) -> None:
    """ffmpeg を実行し、-progress の出力から進捗（0〜1）を通知する。"""
    full = [args[0], "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-stats_period", "0.5", *args[1:]]
    log.debug("ffmpeg: %s", subprocess.list2cmdline(full))
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                            cwd=str(cwd) if cwd else None)
    err_tail: list[str] = []

    def _read_err():
        assert proc.stderr
        for line in proc.stderr:
            err_tail.append(line)
            del err_tail[:-40]

    t = threading.Thread(target=_read_err, daemon=True)
    t.start()
    try:
        assert proc.stdout
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            key, _, val = line.strip().partition("=")
            if key == "out_time_us" and on_progress and duration:
                try:
                    on_progress(min(1.0, max(0.0, int(val) / 1e6 / duration)))
                except ValueError:
                    pass
        proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    finally:
        t.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        raise UserError("ffmpeg_failed", "動画の変換に失敗しました。", "Video conversion failed.", "".join(err_tail))
    if on_progress:
        on_progress(1.0)


class ProgressFileWatcher:
    """yt-dlp が起動する ffmpeg に `-progress <file>` を渡し、そのファイルを監視して進捗を得る。"""

    def __init__(self, path: Path, duration: float | None, on_progress: ProgressFn | None,
                 interval: float = 0.5):
        self.path, self.duration, self.on_progress, self.interval = path, duration, on_progress, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self):
        if self.on_progress and self.duration:
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                text = self.path.read_text("utf-8", errors="replace")
            except OSError:
                continue
            idx = text.rfind("out_time_us=")
            if idx < 0:
                continue
            val = text[idx + len("out_time_us="):].split("\n", 1)[0].strip()
            try:
                assert self.on_progress and self.duration
                self.on_progress(min(1.0, max(0.0, int(val) / 1e6 / self.duration)))
            except (ValueError, AssertionError):
                pass


def wait_file_released(path: Path, timeout: float = 5.0) -> None:
    """Windows でファイルのロックが外れるのを少し待つ。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with open(path, "ab"):
                return
        except OSError:
            time.sleep(0.2)
