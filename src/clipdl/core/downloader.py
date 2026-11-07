"""動画のダウンロード（範囲指定・高速/正確・CFR）。

範囲 DL は yt-dlp の download_ranges（= --download-sections）を使い、必要な部分だけ取得する。
yt-dlp は範囲 DL のとき内部で ffmpeg を起動する。正確モードでは、その ffmpeg に
「エンコーダ指定＋fps フィルタ」を出力引数として渡し、カットと CFR 化を 1 回のエンコードで済ませる。
"""
from __future__ import annotations

import copy
import logging
import os
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from glob import escape as glob_escape
from pathlib import Path
from typing import Callable

import yt_dlp
import yt_dlp.downloader.external as _ytdlp_external
from yt_dlp.utils import download_range_func

from . import cfr
from .errors import Cancelled, UserError, classify
from .ffmpeg import (ProgressFileWatcher, failure_reason, is_encoder_failure, mark_broken, select_encoder,
                     video_encode_args, wait_file_released)
from .models import CfrMode, ClipRange, CutMode, Progress, VideoInfo
from .naming import render
from .presets import Preset, get_preset
from .ytdl import base_params

log = logging.getLogger(__name__)

ProgressCallback = Callable[[Progress], None]


# ------------------------------------------------- yt-dlp が起動する ffmpeg を中止できるようにする

_active_procs: dict[int, set] = {}
_lock = threading.Lock()


_last_stderr: dict[int, str] = {}


class _TrackedPopen(_ytdlp_external.Popen):  # type: ignore[misc, name-defined]
    """中止できるよう記録し、失敗理由を知るため ffmpeg のエラー出力を受け取る。"""

    def __init__(self, *args, **kwargs):
        capture = kwargs.get("stderr") is None
        if capture:
            kwargs["stderr"] = subprocess.PIPE
        super().__init__(*args, **kwargs)
        self._tid = threading.get_ident()
        self._err: list[str] = []
        self._reader = None
        if capture and self.stderr is not None:
            self._reader = threading.Thread(target=self._drain, daemon=True)
            self._reader.start()
        with _lock:
            _active_procs.setdefault(self._tid, set()).add(self)

    def _drain(self):
        for raw in iter(self.stderr.readline, ""):
            if not raw:  # bytes の終端
                break
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            self._err.append(line)
            del self._err[:-60]

    def wait(self, *args, **kwargs):
        try:
            return super().wait(*args, **kwargs)
        finally:
            if self._reader is not None:
                self._reader.join(timeout=2)
            with _lock:
                _active_procs.get(self._tid, set()).discard(self)
                _last_stderr[self._tid] = "".join(self._err)


_ytdlp_external.Popen = _TrackedPopen  # FFmpegFD はこのモジュールの Popen を使う


def last_ffmpeg_stderr(thread_id: int | None = None) -> str:
    """このスレッドで yt-dlp が最後に起動した ffmpeg のエラー出力（末尾）。"""
    return _last_stderr.get(thread_id or threading.get_ident(), "")


def kill_thread_processes(thread_id: int) -> None:
    with _lock:
        procs = list(_active_procs.get(thread_id, ()))
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass


class CancelToken:
    """中止（一時停止も中止として扱う）。別スレッドから cancel() を呼ぶ。"""

    def __init__(self):
        self.event = threading.Event()
        self.thread_id: int | None = None
        self.reason = "cancel"  # "cancel" または "pause"

    def cancel(self, reason: str = "cancel") -> None:
        self.reason = reason
        self.event.set()
        if self.thread_id is not None:
            kill_thread_processes(self.thread_id)

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def check(self) -> None:
        if self.event.is_set():
            raise Cancelled()


# ------------------------------------------------- 設定

@dataclass
class DownloadOptions:
    output_dir: Path
    preset: str = "edit1080"
    cut_mode: CutMode = CutMode.FAST
    cfr_mode: CfrMode = CfrMode.AUTO
    template: str = "{date}_{channel}_{title}"
    cookie_opts: dict = field(default_factory=dict)

    @property
    def preset_obj(self) -> Preset:
        return get_preset(self.preset)


@dataclass
class ClipOutput:
    path: Path
    clip: ClipRange | None = None
    encoder: str | None = None   # 再エンコードに使ったエンコーダ（しなかった場合は None）
    cfr: str | None = None       # CFR 化した fps
    notes: list[str] = field(default_factory=list)


def _container(preset: Preset) -> str:
    return preset.audio_ext if preset.audio_only else (preset.merge_ext or "mp4")


def _outtmpl(opts: DownloadOptions, stem: str) -> str:
    safe = stem.replace("%", "%%")
    return str(opts.output_dir / f"{safe}.%(ext)s")


# ------------------------------------------------- 本体

def download_clip(info: VideoInfo, opts: DownloadOptions, clip: ClipRange | None,
                  on_progress: ProgressCallback | None = None, cancel: CancelToken | None = None) -> "ClipOutput":
    """1 本（範囲なしなら動画全体、ありならその範囲）をダウンロードし、出力ファイルを返す。"""
    cancel = cancel or CancelToken()
    cancel.thread_id = threading.get_ident()
    preset = opts.preset_obj
    container = _container(preset)
    opts.output_dir.mkdir(parents=True, exist_ok=True)
    stem = render(opts.template, info, clip)
    clip_idx = clip.index if clip else None

    def emit(stage, ratio=None, msg=""):
        if on_progress:
            on_progress(Progress(stage, ratio, msg, clip_idx))

    precise = clip is not None and opts.cut_mode == CutMode.PRECISE
    # 正確モードは再エンコードするので、CFR（自動/常に）はそのついでに行う（追加コストなし）
    want_cfr_inline = precise and not preset.audio_only and opts.cfr_mode != CfrMode.OFF
    target_fps = cfr.nearest_standard_rate(info.fps)

    params = base_params(opts.cookie_opts)
    params.update({
        "format": preset.format,
        "outtmpl": {"default": _outtmpl(opts, stem)},
        "overwrites": True,
        "continuedl": True,
        "writethumbnail": False,
    })
    if preset.format_sort:
        params["format_sort"] = list(preset.format_sort)
    if preset.merge_ext:
        params["merge_output_format"] = preset.merge_ext
    if preset.audio_only and preset.audio_ext:
        params["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": preset.audio_ext}]

    def hook(d):
        if cancel.cancelled:
            raise Cancelled()
        if clip is not None:
            return  # 範囲 DL の進捗は ffmpeg の -progress から取る（yt-dlp のフックは 0 を送ってくるため）
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            ratio = (d.get("downloaded_bytes") or 0) / total if total else None
            fmt = (d.get("info_dict") or {}).get("vcodec")
            emit("video", ratio, "音声" if fmt == "none" else "映像")

    params["progress_hooks"] = [hook]
    emit("video", 0.0, "開始")
    duration = clip.duration if clip else info.duration
    needs_video_encode = precise and not preset.audio_only
    retry_notes: list[str] = []

    while True:  # 正確モードでエンコーダが失敗したら、次のエンコーダでやり直す
        enc = select_encoder() if needs_video_encode else None
        ffmpeg_out: list[str] = []
        progress_file = Path(tempfile.gettempdir()) / f"clipdl-progress-{uuid.uuid4().hex}.txt"
        if clip is not None:
            params["download_ranges"] = download_range_func(None, [(clip.start, clip.end)])
            # yt-dlp は -loglevel quiet を付けるので、失敗理由が分かるようエラーだけ出させる（後の指定が優先）
            ffmpeg_out += ["-progress", str(progress_file), "-loglevel", "error"]
            if precise:
                params["force_keyframes_at_cuts"] = True  # yt-dlp に "-c copy" を付けさせない
                if enc is None:
                    ffmpeg_out += ["-c:a", "aac", "-b:a", "192k"]
                else:
                    ffmpeg_out += video_encode_args(target_fps if want_cfr_inline else None,
                                                    container=container, encoder=enc)
                    emit("encode", 0.0, f"エンコーダ: {enc.name}")
            params["external_downloader_args"] = {"ffmpeg_o": ffmpeg_out}
        try:
            with ProgressFileWatcher(progress_file, duration,
                                     (lambda r: emit("encode" if precise else "video", r)) if clip else None):
                with yt_dlp.YoutubeDL(params) as ydl:
                    result = ydl.process_ie_result(copy.deepcopy(info.raw), download=True)
            break
        except Exception as e:  # noqa: BLE001
            if cancel.cancelled or isinstance(e, Cancelled):
                # 範囲 DL は途中から再開できないので、キャンセル・一時停止どちらでも途中ファイルを片付ける。
                # 通常 DL の一時停止は .part を残して、再開時に続きから取る。
                if clip is not None or cancel.reason == "cancel":
                    _remove_partials(opts.output_dir, stem)
                raise Cancelled() from e
            stderr = last_ffmpeg_stderr()
            if (enc is not None and isinstance(e, yt_dlp.utils.DownloadError) and "ffmpeg exited" in str(e)
                    and is_encoder_failure(stderr)):
                reason = failure_reason(stderr)
                mark_broken(enc, reason)
                _remove_partials(opts.output_dir, stem)
                note = f"{enc.name} でエンコードに失敗したため次のエンコーダでやり直しました（理由: {reason}）"
                retry_notes.append(note)
                log.warning(note)
                emit("encode", 0.0, f"{enc.name} が失敗 → やり直し")
                continue  # 次の候補が無ければ select_encoder() が「使えるエンコーダなし」を出す
            if isinstance(e, yt_dlp.utils.DownloadError):
                ue = classify(str(e))
                if stderr:
                    ue.raw = f"{ue.raw}\n--- ffmpeg ---\n{stderr[-3000:]}"
                raise ue from e
            raise classify(e) from e
    cancel.check()

    out = _result_path(result, opts, stem)
    log.info("ダウンロード完了: %s", out.name)

    res = ClipOutput(out, clip, notes=list(retry_notes))
    if enc is not None:
        res.encoder = enc.name
        res.notes.append(f"正確モードで再エンコード（エンコーダ: {enc.name} / {enc.label}）")
        log.info("正確モードで再エンコード: %s", enc.name)
        if want_cfr_inline:
            res.cfr = target_fps
            res.notes.append(f"再エンコード時に CFR 化（{target_fps} fps）")
            log.info("正確モードのエンコード時に CFR 化済み（%s fps）", target_fps)
    # 高速モード・範囲なしのときは、必要に応じて別途 CFR 化する（再エンコードはこの 1 回だけ）
    elif not preset.audio_only and opts.cfr_mode != CfrMode.OFF:
        _maybe_cfr(res, opts.cfr_mode, duration, emit, cancel)
    emit("done", 1.0, out.name)
    return res


def _remove_partials(folder: Path, stem: str) -> None:
    """このアプリが作った途中ファイル（.part / .ytdl）だけを削除する。"""
    for p in folder.glob(f"{glob_escape(stem)}*"):
        if p.suffix in (".part", ".ytdl") or ".part-Frag" in p.name:
            try:
                p.unlink()
            except OSError:
                log.warning("途中ファイルを削除できません: %s", p.name)


def _result_path(result: dict, opts: DownloadOptions, stem: str) -> Path:
    for d in (result or {}).get("requested_downloads") or []:
        fp = d.get("filepath") or d.get("_filename")
        if fp and Path(fp).exists():
            return Path(fp)
    cands = sorted(opts.output_dir.glob(f"{glob_escape(stem)}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    cands = [p for p in cands if p.suffix not in (".part", ".ytdl", ".json", ".ass", ".csv")]
    if cands:
        return cands[0]
    raise classify("download finished but output file was not found")


def _maybe_cfr(res: "ClipOutput", mode: CfrMode, duration, emit, cancel: CancelToken) -> None:
    path = res.path
    emit("encode", None, "フレームレートを確認中")
    report = cfr.analyze(path)
    if mode == CfrMode.AUTO and not report.is_vfr:
        res.notes.append("CFR のため変換なし（" + report.summary() + "）")
        return
    src = path.with_name(path.stem + ".vfr-src" + path.suffix)
    wait_file_released(path)
    os.replace(path, src)
    try:
        while True:  # エンコーダが失敗したら次のエンコーダでやり直す
            enc = select_encoder()
            emit("encode", 0.0, f"CFR に変換中（{report.target} fps / {enc.name}）")
            try:
                cfr.convert_to_cfr(src, path, report.target, duration, encoder=enc,
                                   on_progress=lambda r: emit("encode", r, "CFR に変換中"), cancel=cancel.event)
                break
            except UserError as e:
                if e.code != "ffmpeg_failed" or not is_encoder_failure(e.raw):
                    raise
                reason = failure_reason(e.raw)
                mark_broken(enc, reason)
                path.unlink(missing_ok=True)
                note = f"{enc.name} で CFR 変換に失敗したため次のエンコーダでやり直しました（理由: {reason}）"
                res.notes.append(note)
                log.warning(note)
    except BaseException:
        # 失敗・中止時は元のファイルを戻す
        if path.exists():
            path.unlink()
        os.replace(src, path)
        raise
    src.unlink(missing_ok=True)  # アプリが作った中間ファイルのみ削除
    res.encoder, res.cfr = enc.name, report.target
    res.notes.append(f"CFR に変換（{report.target} fps、エンコーダ: {enc.name} / {enc.label}）")
    log.info("CFR 変換: %s → %s fps（%s）", path.name, report.target, enc.name)
