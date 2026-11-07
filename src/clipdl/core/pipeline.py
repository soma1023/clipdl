"""1 件のジョブ（URL ＋ 範囲 ＋ チャット設定）を最後まで実行する。CLI と GUI の両方から使う。"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from .chat.base import ChatProvider, ChatUnavailable
from .chat.cache import ChatCache, union_span
from .chat.clipping import clip_messages
from .chat.export import spread_same_time, write_ass, write_comments_csv, write_csv, write_json
from .chat import twitch_style
from .chat.overlay import CODECS, OverlayOptions, VideoGeometry, render_overlay
from .chat.twitch_style import TwitchStyleOptions
from .chat.twitch import TwitchChatProvider
from .chat.youtube_comments import fetch_comments
from .chat.youtube_live import YouTubeLiveChatProvider
from .downloader import CancelToken, ClipOutput, DownloadOptions, ProgressCallback, download_clip
from .errors import Cancelled, UserError, classify
from .models import ChatFormat, ClipRange, Progress, VideoInfo
from .naming import render
from .timeutil import validate_ranges
from .ytdl import extract_info

log = logging.getLogger(__name__)


@dataclass
class JobSpec:
    url: str
    ranges: list[ClipRange] = field(default_factory=list)
    download: DownloadOptions | None = None
    save_chat: bool = True
    chat_formats: tuple[ChatFormat, ...] = (ChatFormat.JSON, ChatFormat.ASS)
    save_comments: bool = False
    max_comments: int | None = None
    video: bool = True   # False ならチャットだけ
    overlay: OverlayOptions = field(default_factory=OverlayOptions)
    ass_style: str = "vertical"          # "vertical"（縦流れ）/ "scroll"（横流れ・ニコニコ風）
    chat_style: str = "twitch"           # オーバーレイ動画: "twitch"（Twitch配信風）/ "vertical" / "scroll"
    twitch_style: TwitchStyleOptions = field(default_factory=TwitchStyleOptions)
    info: VideoInfo | None = None         # GUI で取得済みの動画情報（あれば取り直さない）


@dataclass
class JobResult:
    info: VideoInfo | None = None
    files: list[Path] = field(default_factory=list)
    clips: list[ClipOutput] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # 計測値など（警告ではない情報）
    warnings: list[str] = field(default_factory=list)


CHAT_STYLES = ("twitch", "vertical", "scroll")   # Twitch配信風 / シンプル（縦流れ） / 横流れ（ニコニコ風）


def style_options(settings, chat_style: str | None = None) -> dict:
    """設定から JobSpec のスタイル関係の指定を作る（GUI と CLI で共通）。"""
    style = chat_style or getattr(settings, "chat_style", "twitch")
    if style not in CHAT_STYLES:
        style = "twitch"
    overlay = OverlayOptions(codec=settings.overlay_codec if settings.overlay_codec in CODECS else "qtrle",
                             crop=bool(settings.overlay_crop) and style == "vertical",
                             fps=settings.overlay_fps or None)
    tw = TwitchStyleOptions(width=int(settings.twitch_style_width), font_size=int(settings.twitch_style_font_size),
                            bg_opacity=min(1.0, max(0.0, float(settings.twitch_style_bg_opacity))),
                            font=str(settings.twitch_style_font or "Yu Gothic UI"))
    return {"chat_style": style, "ass_style": "scroll" if style == "scroll" else "vertical",
            "overlay": overlay, "twitch_style": tw}


def chat_provider_for(info: VideoInfo, cookie_opts: dict) -> ChatProvider | None:
    for p in (YouTubeLiveChatProvider(cookie_opts), TwitchChatProvider()):
        if p.supports(info):
            return p
    return None


def run_job(spec: JobSpec, on_progress: ProgressCallback | None = None, cancel: CancelToken | None = None,
            info: VideoInfo | None = None) -> JobResult:
    cancel = cancel or CancelToken()
    opts = spec.download
    assert opts is not None
    result = JobResult()

    def emit(p: Progress):
        if on_progress:
            on_progress(p)

    emit(Progress("info", None, "動画情報を取得中"))
    info = info or spec.info or extract_info(spec.url, opts.cookie_opts)
    result.info = info
    emit(Progress("info", 1.0, info.title))  # GUI のキューにタイトルを出すため
    ranges = validate_ranges(spec.ranges, info.duration)
    cancel.check()

    # 1) 動画
    if spec.video:
        for clip in (ranges or [None]):
            cancel.check()
            out = download_clip(info, opts, clip, on_progress, cancel)
            result.files.append(out.path)
            result.clips.append(out)

    # 2) チャット（取得は 1 回、範囲ごとに切り出し）
    if spec.save_chat:
        try:
            clip_videos = {(c.clip.index if c.clip else None): c.path for c in result.clips}
            result.files += _save_chat(info, spec, ranges, opts, emit, cancel, result.notes, clip_videos)
        except ChatUnavailable as e:
            result.warnings.append(str(e))
            log.info("チャットなし: %s", e)
        except (Cancelled, KeyboardInterrupt):
            raise
        except UserError as e:
            # 動画は保存できているので、チャットの失敗は警告にとどめる
            result.warnings.append(f"チャットを保存できませんでした: {e.message_ja}")
            log.warning("チャット失敗: %s", e.raw)

    # 3) YouTube の通常コメント（範囲とは連動しない）
    if spec.save_comments and info.site == "youtube":
        try:
            comments = fetch_comments(info, opts.cookie_opts, spec.max_comments, emit, cancel)
            stem = render(opts.template, info)
            opts.output_dir.mkdir(parents=True, exist_ok=True)
            jp = opts.output_dir / f"{stem}.comments.json"
            jp.write_text(json.dumps(comments, ensure_ascii=False, indent=1), "utf-8")
            result.files.append(jp)
            if ChatFormat.CSV in spec.chat_formats:
                result.files.append(write_comments_csv(opts.output_dir / f"{stem}.comments.csv", comments))
        except (Cancelled, KeyboardInterrupt):
            raise
        except Exception as e:  # noqa: BLE001
            ue = classify(e)
            result.warnings.append(f"コメントを保存できませんでした: {ue.message_ja}")
            log.warning("コメント失敗: %s", ue.raw)

    emit(Progress("done", 1.0, "完了"))
    return result


def _save_chat(info: VideoInfo, spec: JobSpec, ranges: list[ClipRange], opts: DownloadOptions, emit,
               cancel: CancelToken, notes: list[str], clip_videos: dict) -> list[Path]:
    provider = chat_provider_for(info, opts.cookie_opts)
    if provider is None:
        raise ChatUnavailable("このサイトのチャット取得には対応していません")
    span = union_span([(r.start, r.end) for r in ranges]) if ranges else None
    use_twitch_style = ChatFormat.MOV in spec.chat_formats and spec.chat_style == twitch_style.STYLE
    if span is not None and use_twitch_style and info.site == "twitch":
        # 最初のフレームからチャット欄が埋まって見えるよう、開始前のコメントも取得する
        span = (max(0.0, span[0] - twitch_style.PREROLL), span[1])
    messages, stats = ChatCache().get(provider, info, span, emit, cancel)
    embedded = twitch_style.load_embedded(stats.raw_path) if use_twitch_style and info.site == "twitch" else None
    images = None
    notes.append(stats.summary())
    opts.output_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    # Twitch の時刻は 1 秒単位なので、同じ秒のコメントを表示用（ASS・オーバーレイ）だけ均等にずらす
    spread = info.site == "twitch"
    want_ass = ChatFormat.ASS in spec.chat_formats
    want_mov = ChatFormat.MOV in spec.chat_formats
    for clip in (ranges or [None]):
        cancel.check()
        msgs = clip_messages(messages, clip) if clip else messages
        stem = render(opts.template, info, clip)
        duration = (clip.duration if clip else info.duration) or (msgs[-1].time + 10 if msgs else 10)
        base = opts.output_dir / stem
        if ChatFormat.JSON in spec.chat_formats:
            files.append(write_json(base.with_name(stem + ".chat.json"), msgs, source=provider.name,
                                    video_id=info.id, clip=clip))
        if ChatFormat.CSV in spec.chat_formats:
            files.append(write_csv(base.with_name(stem + ".chat.csv"), msgs))
        if want_ass or want_mov:
            geo = _geometry(clip_videos.get(clip.index if clip else None), info, duration)
            shown = spread_same_time(msgs) if spread else msgs
            ass_path = base.with_name(stem + ".chat.ass")
            tmp_ass = None
            if not want_ass:  # オーバーレイだけ欲しい場合は ASS を一時ファイルに作る
                tmp_ass = Path(tempfile.mkdtemp(prefix="clipdl-ass-")) / "chat.ass"
                ass_path = tmp_ass
            write_ass(ass_path, shown, kind=spec.ass_style, duration=geo.duration, width=geo.width, height=geo.height,
                               title=info.title)
            if want_ass:
                files.append(ass_path)
            done_twitch_style = False
            if want_mov and use_twitch_style:
                if images is None and info.site != "twitch":
                    images = twitch_style.ImageStore()
                try:
                    ov = _render_twitch_style(info, spec, clip, messages, geo, embedded, images, base, stem, emit,
                                              cancel, spread)
                    files.append(ov.path)
                    notes.append(_overlay_note(ov, geo, "Twitch配信風"))
                    done_twitch_style = True
                except Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 — 書き出せなければシンプルで書き出す
                    log.exception("Twitch配信風で書き出せませんでした")
                    notes.append(f"Twitch配信風で書き出せなかったため、シンプルで書き出しました（{e}）")
            if want_mov and not done_twitch_style:
                idx = clip.index if clip else None
                emit(Progress("overlay", 0.0, "オーバーレイ動画を書き出し中", idx))
                ov = render_overlay(ass_path, base.with_name(stem + ".chat_overlay.mov"), geo,
                                    on_progress=lambda r, idx=idx: emit(
                                        Progress("overlay", r, "オーバーレイ動画を書き出し中", idx)),
                                    cancel=cancel.event,
                                    # 横流れは画面全体を使うので切り出さない
                                    options=spec.overlay if spec.ass_style == "vertical"
                                    else replace(spec.overlay, crop=False))
                files.append(ov.path)
                notes.append(_overlay_note(ov, geo, "シンプル"))
            if tmp_ass is not None:
                shutil.rmtree(tmp_ass.parent, ignore_errors=True)  # アプリが作った一時フォルダ
        log.info("チャット %s: %d 件", stem, len(msgs))
    return files


def _overlay_note(ov, geo: VideoGeometry, style: str) -> str:
    size = ov.path.stat().st_size / 1048576
    note = (f"オーバーレイ動画（{style}）: {ov.path.name}（{CODECS[ov.codec]['label']} {ov.width}x{ov.height} "
            f"{ov.fps} fps {ov.duration:.2f} 秒 {size:.0f} MB・背景透明）")
    if ov.crop_x:
        px, py = ov.premiere_position(geo.width, geo.height)
        note += (f" → Premiere では {geo.width}x{geo.height} のシーケンスで"
                 f"「位置」を X={px:g} / Y={py:g} にすると元の位置に重なります")
    return note


def _render_twitch_style(info: VideoInfo, spec: JobSpec, clip, messages, geo: VideoGeometry, embedded, images,
                         base: Path, stem: str, emit, cancel: CancelToken, spread: bool):
    """chatrender で「Twitch配信風」のオーバーレイ動画を書き出す。"""
    start = clip.start if clip else 0.0
    end = clip.end if clip else start + geo.duration
    window = [m for m in messages if start - twitch_style.PREROLL <= m.time < end]
    if spread:
        window = spread_same_time(window)
    data, st = twitch_style.build_render_json(window, info.site, embedded=embedded, images=images,
                                              video_id=info.id, title=info.title, start=start, end=end)
    tmp = Path(tempfile.mkdtemp(prefix="clipdl-twstyle-"))
    try:
        jp = tmp / "chat.json"
        jp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        idx = clip.index if clip else None
        emit(Progress("overlay", 0.0, "オーバーレイ動画を書き出し中（Twitch配信風）", idx))
        offline = info.site != "twitch" or twitch_style.has_embedded_images(embedded)
        ov = twitch_style.render(jp, base.with_name(stem + ".chat_overlay.mov"), geo, start, end,
                                 opts=spec.twitch_style, overlay=spec.overlay, offline=offline, site=info.site,
                                 on_progress=lambda r, idx=idx: emit(
                                     Progress("overlay", r, "オーバーレイ動画を書き出し中（Twitch配信風）", idx)),
                                 cancel=cancel.event)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # アプリが作った一時フォルダ
    log.info("Twitch配信風: コメント %d 件 / エモート画像 %d / バッジ画像 %d / 取得できなかった画像 %d",
             st["comments"], st["emotes"], st["badges"], st["image_failed"])
    return ov


def _geometry(video: Path | None, info: VideoInfo, duration: float) -> VideoGeometry:
    """オーバーレイの解像度・fps・長さ。保存したクリップがあればそれに合わせる。"""
    if video is not None and video.exists():
        try:
            return VideoGeometry.from_file(video)
        except (StopIteration, KeyError, UserError):
            log.info("クリップから映像情報を読めないため、動画情報から決めます: %s", video.name)
    return VideoGeometry.fallback(info.width, info.height, info.fps, duration)
