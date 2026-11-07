"""コマンドライン版。

例:
  python -m clipdl URL
  python -m clipdl URL --range 00:01:00-00:02:30 --range 1:05:00-1:06:00 --mode precise
  python -m clipdl URL --info
  python -m clipdl URL --chat-only --chat-formats json,csv,ass
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from .. import __version__
from ..core.cookies import CookieOptionError, cookie_params
from ..core.downloader import DownloadOptions
from ..core.errors import UserError, classify
from ..core.ffmpeg import ENCODER_CANDIDATES, ENCODER_NAMES, configure_encoders
from ..core.logging_setup import export_logs, setup_logging
from ..core.models import CfrMode, ChatFormat, CutMode, Progress
from ..core.naming import TemplateError, validate_template
from ..core.presets import PRESETS
from ..core.queue import JobQueue, JobState
from ..core.settings import TERMS_VERSION, Settings
from ..core.timeutil import TimeFormatError, format_time, parse_range

TERMS_TEXT = """\
【利用規約】
本ソフトウェアは、権利者の許諾がある、または利用者自身が権利を持つコンテンツのダウンロードにのみ使用できます。
DRM で保護されたコンテンツには対応していません。各サイトの利用規約と著作権法を守ってご利用ください。
"""


def build_parser() -> argparse.ArgumentParser:
    s = Settings.load()
    p = argparse.ArgumentParser(prog="clipdl", description="YouTube / Twitch 切り抜き用ダウンローダー")
    p.add_argument("urls", nargs="*", metavar="URL", help="動画の URL（複数可）")
    p.add_argument("-r", "--range", dest="ranges", action="append", default=[], metavar="開始-終了",
                   help="範囲（例 00:01:00-00:02:30）。複数指定可")
    p.add_argument("-p", "--preset", choices=list(PRESETS), default=s.preset, help="画質プリセット")
    p.add_argument("-m", "--mode", choices=[m.value for m in CutMode], default=s.cut_mode,
                   help="fast=キーフレーム単位（速い） / precise=正確（再エンコード）")
    p.add_argument("--cfr", choices=[m.value for m in CfrMode], default=s.cfr_mode, help="CFR 変換")
    p.add_argument("--encoder", default="auto", metavar="NAME",
                   help="再エンコードに使うエンコーダ（auto / " + " / ".join(ENCODER_NAMES) + "）")
    p.add_argument("--disable-encoder", action="append", default=[], metavar="NAME",
                   help="候補から外すエンコーダ（複数可。例 --disable-encoder h264_nvenc）")
    p.add_argument("--list-encoders", action="store_true", help="使えるエンコーダを確認して表示")
    p.add_argument("-o", "--out", default=s.output_dir, help="保存先フォルダ")
    p.add_argument("-t", "--template", default=s.filename_template,
                   help="ファイル名テンプレート（{date} {channel} {title} {id} {site} {clip} {start} {end}）")
    chat = p.add_mutually_exclusive_group()
    chat.add_argument("--chat", dest="chat", action="store_true", default=s.save_chat, help="チャットも保存（初期値）")
    chat.add_argument("--no-chat", dest="chat", action="store_false", help="チャットを保存しない")
    p.add_argument("--chat-only", action="store_true", help="動画は保存せずチャットだけ保存")
    p.add_argument("--chat-formats", default=",".join(s.chat_formats), help="json,csv,ass,mov から選ぶ（カンマ区切り。mov = オーバーレイ動画・背景透明の ProRes 4444）")
    p.add_argument("--comments", action="store_true", default=s.save_comments, help="YouTube の通常コメントも保存")
    p.add_argument("--max-comments", type=int, default=None, help="コメントの最大件数")
    p.add_argument("--cookies-from-browser", default=s.cookies_from_browser, metavar="BROWSER[:PROFILE]",
                   help="Cookie を読み込むブラウザ（メンバー限定動画用。Firefox 推奨）")
    p.add_argument("--cookies", default=s.cookies_file, metavar="FILE", help="cookies.txt のパス")
    p.add_argument("--info", action="store_true", help="動画情報だけ表示する")
    p.add_argument("--export-logs", metavar="DIR", help="ログを zip に書き出して終了")
    p.add_argument("--check-update", action="store_true", help="clipdl 本体と yt-dlp / yt-dlp-ejs / Deno の新しい版を確認して終了")
    p.add_argument("--update", action="store_true",
                   help="yt-dlp（と yt-dlp-ejs）を取得・署名検証して記録（次回の起動から反映）")
    p.add_argument("--reset-updates", action="store_true", help="更新版の記録を消して同梱版に戻す")
    p.add_argument("--lang", choices=["ja", "en"], default=s.language, help="メッセージの言語")
    p.add_argument("--accept-terms", action="store_true", help="利用規約に同意する（初回のみ必要）")
    p.add_argument("-v", "--verbose", action="store_true", help="詳しいログを画面にも出す")
    p.add_argument("--version", action="version", version=f"clipdl {__version__}")
    return p


class _Printer:
    def __init__(self):
        self._last = ""
        self._lock = threading.Lock()

    def progress(self, job_no: int, p: Progress):
        clip = f" クリップ{p.clip_index}" if p.clip_index else ""
        pct = f" {p.ratio * 100:5.1f}%" if p.ratio is not None else ""
        line = f"[{job_no}]{clip} {p.stage}{pct} {p.message}".rstrip()
        with self._lock:
            if line != self._last:
                sys.stderr.write("\r" + line[:110].ljust(110))
                sys.stderr.flush()
                self._last = line

    def newline(self):
        with self._lock:
            sys.stderr.write("\n")
            self._last = ""


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    args = build_parser().parse_args(argv)
    log_path = setup_logging(args.verbose)
    settings = Settings.load()

    if args.export_logs:
        print(f"ログを書き出しました: {export_logs(Path(args.export_logs))}")
        return 0

    if args.check_update or args.update or args.reset_updates:
        return _updates(args)

    if settings.terms_accepted_version != TERMS_VERSION:
        print(TERMS_TEXT, file=sys.stderr)
        if not args.accept_terms:
            print("同意する場合は --accept-terms を付けて実行してください。", file=sys.stderr)
            return 2
        settings.terms_accepted_version = TERMS_VERSION
        settings.save()

    try:
        configure_encoders(args.encoder, args.disable_encoder)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2

    if args.list_encoders:
        return _list_encoders()

    if not args.urls:
        print("URL を指定してください（python -m clipdl --help）", file=sys.stderr)
        return 2

    try:
        ranges = [parse_range(r, index=i) for i, r in enumerate(args.ranges, 1)]
        validate_template(args.template)
        cookie_opts = cookie_params(args.cookies_from_browser, args.cookies)
        chat_formats = tuple(ChatFormat(x.strip().lower()) for x in args.chat_formats.split(",") if x.strip())
    except (TimeFormatError, TemplateError, CookieOptionError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"エラー: チャット形式は json, csv, ass, mov から選んでください（{e}）", file=sys.stderr)
        return 2

    if args.info:
        from ..core.ytdl import extract_info
        code = 0
        for url in args.urls:
            try:
                info = extract_info(url, cookie_opts)
            except UserError as e:
                print(f"エラー: {e.message(args.lang)}", file=sys.stderr)
                code = 1
                continue
            dur = format_time(info.duration) if info.duration else "?"
            print(f"タイトル: {info.title}\nチャンネル: {info.channel}\n長さ: {dur}\n"
                  f"サイト: {info.site} ({info.extractor})\nID: {info.id}\n公開日: {info.upload_date}\n"
                  f"最大画質: {info.width}x{info.height} {info.fps}fps\n"
                  f"ライブチャット: {'あり' if info.has_live_chat else 'なし'}\nサムネイル: {info.thumbnail}\n")
        return code

    from ..core.pipeline import JobSpec
    opts = DownloadOptions(output_dir=Path(args.out).expanduser(), preset=args.preset, cut_mode=CutMode(args.mode),
                           cfr_mode=CfrMode(args.cfr), template=args.template, cookie_opts=cookie_opts)
    printer = _Printer()
    numbers: dict[str, int] = {}

    def listener(job):
        no = numbers.setdefault(job.id, len(numbers) + 1)
        if job.state == JobState.RUNNING and job.progress:
            printer.progress(no, job.progress)

    q = JobQueue(listener)
    for url in args.urls:
        q.add(JobSpec(url=url, ranges=ranges, download=opts, save_chat=args.chat or args.chat_only,
                      chat_formats=chat_formats, save_comments=args.comments, max_comments=args.max_comments,
                      video=not args.chat_only))
    try:
        q.run_until_idle()
    except KeyboardInterrupt:
        printer.newline()
        print("中止しました。", file=sys.stderr)
        return 130
    printer.newline()

    code = 0
    for no, job in enumerate(q.jobs, 1):
        title = job.result.info.title if job.result and job.result.info else job.spec.url
        if job.state == JobState.DONE and job.result:
            print(f"[{no}] 完了: {title}")
            for c in job.result.clips:
                print(f"    {c.path}")
                for n in c.notes:
                    print(f"      - {n}")
            clip_paths = {c.path for c in job.result.clips}
            for f in job.result.files:
                if f not in clip_paths:
                    print(f"    {f}")
            for n in job.result.notes:
                print(f"    情報: {n}")
            for w in job.result.warnings:
                print(f"    注意: {w}")
        else:
            code = 1
            err = job.error or classify("cancelled") if job.state != JobState.CANCELLED else None
            msg = err.message(args.lang) if err else "中止しました"
            print(f"[{no}] 失敗: {title}\n    {msg}", file=sys.stderr)
            if err and args.verbose:
                print(f"    詳しく: {err.raw}", file=sys.stderr)
    if code:
        print(f"（詳しいログ: {log_path}）", file=sys.stderr)
    return code


def _updates(args) -> int:
    from ..core import bootstrap, updater
    if args.reset_updates:
        updater.reset_updates()
        print("更新版の記録を消しました。次回の起動から同梱版を使います。")
        return 0
    for name, reason in bootstrap.REJECTED.items():
        print(f"注意: 更新版の {name} を使わず同梱版で動いています（{reason}）")
    import yt_dlp
    print(f"yt-dlp の読み込み元: {Path(yt_dlp.__file__).parent}")
    from ..core import app_update
    print(app_update.check_now().summary())  # clipdl 本体（知らせるだけ。入れ替えはしない）
    for st in updater.check_updates():
        print(st.summary())
    if not args.update:
        return 0
    try:
        res = updater.update(progress=lambda m: print(f"  … {m}"))
    except UserError as e:
        print(f"エラー: {e.message(args.lang)}", file=sys.stderr)
        if args.verbose:
            print(f"    詳しく: {e.raw}", file=sys.stderr)
        return 1
    for n in res.notes:
        print(f"  - {n}")
    return 0


def _list_encoders() -> int:
    from ..core import ffmpeg as ff
    names = ff.available_encoder_names()
    order = {e.name for e in ff.candidate_order()}
    print("H.264 エンコーダ（上から順に試します）:")
    for e in ENCODER_CANDIDATES:
        if e.name not in order:
            state = "無効化"
        elif e.name not in names:
            state = "ビルドに無し"
        else:
            state = "OK" if ff._test_encode(e) else "使えない（テスト失敗）"
        print(f"  {e.name:<12} {e.label:<26} {state}")
    try:
        sel = ff.select_encoder()
        print(f"→ 使用: {sel.name}")
        return 0
    except UserError as e:
        print(f"→ {e.message_ja}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
