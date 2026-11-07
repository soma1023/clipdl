"""チャットのスタイル「Twitch配信風」: TwitchDownloaderCLI の chatrender で、オーバーレイ動画を書き出す。

見た目: チャット欄全体に黒の半透明の背景、名前（ユーザーごとの色・太字・「:」）の左にバッジ、
本文は白の太字＋ふち、本文の中にエモート（画像）。新しいコメントが下に追加され、古いものは上へ押し出される。

- Twitch: チャット取得時に画像を埋め込んだ JSON（chatdownload -E）をそのまま使い、描画は通信なし（--offline）
- YouTube: チャットを chatrender が読める JSON に変換する。メンバー絵文字・メンバーバッジ・ステッカーの画像は
  yt3.ggpht.com などから取得して埋め込む（取得できなかった絵文字は「:名前:」の文字で描く）。
  モデレーター・オーナー・認証済みのバッジは画像が無いため、同梱の自作アイコンを使う
- 名前の色は、ユーザーごとに固定し（実行のたびに変わらない）、黒の半透明の上で読める明るさに持ち上げる

chatrender の制約: 名前に英数字以外が入ると、指定のフォントと太字を使わず Windows の代わりのフォントで描く
（日本語の名前は細くなる）。本文は指定のフォントの太字で描かれる。
"""
from __future__ import annotations

import base64
import colorsys
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..cfr import parse_rate
from ..errors import Cancelled
from ..ffmpeg import ffmpeg_path
from ..models import ChatMessage
from ..paths import cache_dir, find_binary
from .overlay import CODECS, OverlayOptions, OverlayResult, VideoGeometry

log = logging.getLogger(__name__)

STYLE = "twitch"
PREROLL = 120.0          # クリップの開始前のコメントも入れておく（最初のフレームからチャット欄が埋まって見える）
BADGE_DIR = Path(__file__).parent / "badges"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_PERCENT_RE = re.compile(r"Rendering Video\s+(\d{1,3})\s*%")
# Twitch の既定の名前の色（色の無いユーザーと YouTube に使う）
PALETTE = ["#FF0000", "#0000FF", "#008000", "#B22222", "#FF7F50", "#9ACD32", "#FF4500", "#2E8B57",
           "#DAA520", "#D2691E", "#5F9EA0", "#1E90FF", "#FF69B4", "#8A2BE2", "#00FF7F"]
MIN_LUMINANCE = 0.2      # これより暗い名前の色だけ明るくする（相対輝度。#FF0000 は 0.21 でそのまま、#0000FF は 0.07）
# YouTube の画像を取りに行ってよいホスト（チャットのデータに入っている URL のうち、これ以外は使わない）
IMAGE_HOSTS = (".ggpht.com", ".googleusercontent.com", ".ytimg.com", ".gstatic.com")
# 実測（幅 420・高さ 1080・30fps・チャット多め）の 1 秒あたりの MB。予想サイズの表示用
MEASURED_MB_PER_SEC = {"qtrle": 0.95, "prores": 6.6, "png": 3.0}


@dataclass(frozen=True)
class TwitchStyleOptions:
    width: int = 420              # 高さ 1080 のときのチャット欄の幅（px）。高さに合わせて拡大縮小する
    font_size: int = 22           # 高さ 1080 のときの文字サイズ
    bg_opacity: float = 0.55      # 背景（黒）の濃さ 0〜1
    font: str = "Yu Gothic UI"    # Windows 10 / 11 に標準で入っている
    outline: int = 4
    scale_vertical: float = 0.7   # 行の上下の余白（1.0 が chatrender の標準）


# ------------------------------------------------------------------------ 名前の色


def luminance(r: float, g: float, b: float) -> float:
    """相対輝度（人の目の感じる明るさ。0〜1）。"""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def readable_color(hex_color: str) -> str:
    """暗すぎる色だけ、黒の半透明の上で読める明るさまで持ち上げる（色合いは保つ。読める色はそのまま）。"""
    h = hex_color.strip().lstrip("#")
    if len(h) == 8:   # AARRGGBB
        h = h[2:]
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return "#FFFFFF"
    if luminance(r, g, b) >= MIN_LUMINANCE:
        return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))
    hh, light, s = colorsys.rgb_to_hls(r, g, b)
    while light < 0.95:
        light = min(0.95, light + 0.02)
        r, g, b = colorsys.hls_to_rgb(hh, light, s)
        if luminance(r, g, b) >= MIN_LUMINANCE:
            break
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def stable_color(key: str) -> str:
    return PALETTE[int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % len(PALETTE)]


# ------------------------------------------------------------------------ 日本語の折り返し
#
# chatrender は空白でしか折り返さず、空白で区切った 1 かたまりを丸ごと次の行に送る。
# 空白の無い日本語の文は 1 かたまりなので、名前の横に入りきらないと文全体が次の行に落ちる。
# そこで、日本語の文字の後ろに「区切り」（chatrender が空白とみなす U+200A）を入れて折り返せるようにする。
# chatrender は区切りごとに単語の間隔を空けるため、間隔を 1/4 に縮め（--scale-word-space 0.25）、
# もともとの空白（英単語の間・名前と本文の間）は区切りを 4 つ並べて元の幅に戻す。
# 英単語・URL・エモート名（空白を含まない英数字のかたまり）は途中で切らない。

BREAK = " "
WORD_SPACE_SCALE = 0.25
# chatrender は空のかたまりを飛ばすため、区切りを並べても幅は戻らない。かたまりは 1 つごとに単語の間隔
# （1/4）が付くので、もともとの空白は「幅 0 の文字（U+200B。chatrender は空白とみなさない）だけのかたまり」を
# 3 つ並べて、直前の単語の間隔 1/4 ＋ 3/4 ＝ 元の空白 1 つ分の幅に戻す
ZW = "​"
PAD = (ZW + BREAK) * (round(1 / WORD_SPACE_SCALE) - 1)
SPACE = BREAK + PAD
# 行頭に来てはいけない文字（この前では折り返さない）
_NO_BREAK_BEFORE = set("、。，．・：；？！ー～〜）」』】〕〉》｝］)]}!?.,:;ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々")
# 行末に来てはいけない文字（この後では折り返さない）
_NO_BREAK_AFTER = set("（「『【〔〈《｛［([{")
_SPACES = set(" \t　 ")


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x3000 <= o <= 0x30FF or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFFEF or 0x31F0 <= o <= 0x31FF)


def make_breakable(text: str, lead: bool = False) -> str:
    """日本語の文字の間に折り返し位置を入れる。lead=True なら先頭に「名前と本文の間」の空白を足す。"""
    if text and not text.strip():
        return text   # 空白だけのかたまり（エモートの間など）はそのまま
    out: list[str] = [PAD] if lead else []   # 名前の後ろの間隔 1/4 に足して、名前と本文の間を空白 1 つ分にする
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in _SPACES:
            while i < n and text[i] in _SPACES:   # 連続する空白は 1 つにまとめる
                i += 1
            out.append(SPACE)
            continue
        out.append(ch)
        nxt = text[i + 1] if i + 1 < n else ""
        if nxt and nxt not in _SPACES and (_is_cjk(ch) or _is_cjk(nxt)) \
                and nxt not in _NO_BREAK_BEFORE and ch not in _NO_BREAK_AFTER:
            out.append(BREAK)
        i += 1
    return "".join(out)


# ------------------------------------------------------------------------ 画像（YouTube）


class ImageStore:
    """YouTube の絵文字・バッジ画像を取得し、キャッシュする。取得できなければ None。"""

    def __init__(self, root: Path | None = None, fetch=None):
        self.root = root or (cache_dir() / "images")
        self.root.mkdir(parents=True, exist_ok=True)
        self._fetch = fetch or self._download
        self.failed = 0

    @staticmethod
    def allowed(url: str) -> bool:
        p = urlparse(url)
        return p.scheme == "https" and any(p.netloc.endswith(h) or p.netloc == h.lstrip(".") for h in IMAGE_HOSTS)

    @staticmethod
    def _download(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "clipdl"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError("画像が大きすぎます")
        return data

    def get(self, url: str | None) -> bytes | None:
        if not url or not self.allowed(url):
            return None
        path = self.root / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")
        if path.is_file():
            return path.read_bytes()
        try:
            data = self._fetch(url)
        except Exception as e:  # noqa: BLE001 — 画像が取れなくても書き出しは続ける
            log.info("画像を取得できませんでした（文字で表示します）: %s %s", urlparse(url).netloc, e)
            self.failed += 1
            return None
        path.write_bytes(data)
        return data


def _best_url(thumbs: list | None) -> str | None:
    thumbs = [t for t in thumbs or [] if isinstance(t, dict) and t.get("url")]
    if not thumbs:
        return None
    url = max(thumbs, key=lambda t: t.get("width") or 0)["url"]
    return "https:" + url if url.startswith("//") else url


def _runs_text(runs: dict | None) -> str:
    return "".join(r.get("text", "") for r in (runs or {}).get("runs", []))


# ------------------------------------------------------------------------ chatrender 用の JSON


class _Builder:
    def __init__(self, images: ImageStore | None):
        self.images = images
        self.emotes: dict[str, dict] = {}
        self.badges: dict[str, dict] = {}

    def emote(self, key: str, name: str, url: str | None) -> str | None:
        eid = "yt-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        if eid not in self.emotes:
            data = self.images.get(url) if self.images else None
            if data is None:
                return None
            self.emotes[eid] = {"id": eid, "imageScale": 2, "data": base64.b64encode(data).decode("ascii"),
                                "name": name, "width": 48, "height": 48}
        return eid

    def badge(self, key: str, title: str, data: bytes | None) -> str | None:
        bid = "yt-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        if bid not in self.badges:
            if data is None:
                return None
            self.badges[bid] = {"name": bid, "versions": {"1": {"title": title, "description": "",
                                                               "bytes": base64.b64encode(data).decode("ascii")}}}
        return bid


_ICON_BADGES = {"MODERATOR": "moderator.png", "OWNER": "owner.png", "VERIFIED": "verified.png"}


def _youtube_comment(m: ChatMessage, b: _Builder, t: float) -> dict | None:
    raw = m.raw or {}
    kind, r = next(iter(raw.items()), (None, None))
    if not isinstance(r, dict):
        return None
    frags: list[dict] = []
    if m.kind == "superchat" and m.amount:
        frags.append({"text": f"[{m.amount}] ", "emoticon": None})
    elif m.kind == "membership":
        head = _runs_text(r.get("headerSubtext")) or "メンバー"
        frags.append({"text": f"[{head}] ", "emoticon": None})
    elif m.kind == "sticker":
        st = r.get("sticker") or {}
        label = ((st.get("accessibility") or {}).get("accessibilityData") or {}).get("label") or "ステッカー"
        if m.amount:
            frags.append({"text": f"[{m.amount}] ", "emoticon": None})
        eid = b.emote("sticker:" + (_best_url(st.get("thumbnails")) or label), label, _best_url(st.get("thumbnails")))
        frags.append({"text": label, "emoticon": {"emoticon_id": eid}} if eid else {"text": label, "emoticon": None})
    msg_runs = (r.get("message") or {}).get("runs", [])
    for run in msg_runs:
        if "text" in run:
            frags.append({"text": run["text"], "emoticon": None})
        elif "emoji" in run:
            e = run["emoji"]
            name = (e.get("shortcuts") or [None])[0] or ""
            if not e.get("isCustomEmoji"):
                # 標準の絵文字は Unicode の文字で描く（chatrender 同梱の絵文字画像で表示される）
                frags.append({"text": e.get("emojiId", name), "emoticon": None})
                continue
            eid = b.emote(str(e.get("emojiId")), name, _best_url((e.get("image") or {}).get("thumbnails")))
            frags.append({"text": name, "emoticon": {"emoticon_id": eid}} if eid else {"text": name, "emoticon": None})
    if not frags:
        frags.append({"text": m.text or "", "emoticon": None})
    badges = []
    for bd in r.get("authorBadges", []):
        br = bd.get("liveChatAuthorBadgeRenderer", {})
        url = _best_url((br.get("customThumbnail") or {}).get("thumbnails"))
        if url:
            bid = b.badge("member:" + url, br.get("tooltip", ""), b.images.get(url) if b.images else None)
        else:
            icon = (br.get("icon") or {}).get("iconType", "")
            f = _ICON_BADGES.get(icon)
            bid = b.badge("icon:" + icon, br.get("tooltip", ""), (BADGE_DIR / f).read_bytes()) if f else None
        if bid:
            badges.append({"_id": bid, "version": "1"})
    name = (m.user or "").lstrip("@") or "?"
    uid = m.user_id or name
    return {"_id": r.get("id") or hashlib.sha1(f"{t}{uid}{m.text}".encode()).hexdigest(),
            "created_at": "1970-01-01T00:00:00Z", "channel_id": "0", "content_type": "video", "content_id": "0",
            "content_offset_seconds": t,
            "commenter": {"display_name": name, "_id": uid, "name": name.lower(), "bio": None,
                          "created_at": "1970-01-01T00:00:00Z", "updated_at": "1970-01-01T00:00:00Z", "logo": None},
            "message": {"body": "".join(f["text"] for f in frags), "bits_spent": 0, "fragments": frags,
                        "user_badges": badges, "user_color": readable_color(stable_color(uid)), "emoticons": []}}


def _twitch_comment(m: ChatMessage, t: float) -> dict | None:
    c = copy.deepcopy(m.raw) if m.raw else None
    if not c:
        return None
    c["content_offset_seconds"] = t
    who = c.setdefault("commenter", {})
    msg = c.setdefault("message", {})
    key = str(who.get("_id") or who.get("display_name") or m.user)
    msg["user_color"] = readable_color(msg.get("user_color") or stable_color(key))
    return c


def _add_breaks(comment: dict) -> None:
    """本文の日本語に折り返し位置を入れる（名前の横から続けて書き、途中で折り返せるように）。"""
    msg = comment.get("message") or {}
    frags = msg.get("fragments") or [{"text": msg.get("body") or "", "emoticon": None}]
    out, first = [], True
    for f in frags:
        if f.get("emoticon"):
            if first:   # エモートで始まる本文も、名前との間を空白 1 つ分にする
                out.append({"text": PAD, "emoticon": None})
            out.append(f)
        else:
            out.append({**f, "text": make_breakable(f.get("text") or "", lead=first)})
        first = False
    msg["fragments"] = out


def build_render_json(messages: list[ChatMessage], site: str, *, embedded: dict | None = None,
                      images: ImageStore | None = None, video_id: str = "", title: str = "",
                      start: float = 0.0, end: float = 0.0) -> tuple[dict, dict]:
    """chatrender に渡す JSON を作る。messages の time は動画の先頭からの秒（表示用にずらしたもの）。

    戻り値: (JSON, 集計 {"comments", "emotes", "badges", "image_failed"})
    """
    b = _Builder(images)
    comments = []
    for m in sorted(messages, key=lambda m: m.time):
        c = _twitch_comment(m, m.time) if site == "twitch" else _youtube_comment(m, b, m.time)
        if c is not None:
            _add_breaks(c)
            comments.append(c)
    if site == "twitch":
        emb = copy.deepcopy(embedded) if embedded else {}
    else:
        emb = {"thirdParty": [], "firstParty": list(b.emotes.values()),
               "twitchBadges": list(b.badges.values()), "twitchBits": []}
    root = {"FileInfo": {"Version": {"Major": 1, "Minor": 4, "Patch": 0}, "CreatedAt": "1970-01-01T00:00:00Z",
                         "UpdatedAt": "1970-01-01T00:00:00Z"},
            "streamer": {"name": "", "login": "", "id": 0}, "clipper": None,
            "video": {"title": title, "description": None, "id": video_id, "created_at": "1970-01-01T00:00:00Z",
                      "start": max(0.0, start), "end": max(end, start + 1), "length": max(end, start + 1),
                      "viewCount": 0, "game": None, "chapters": []},
            "comments": comments, "embeddedData": emb or None}
    stats = {"comments": len(comments), "emotes": len(emb.get("firstParty") or []) if emb else 0,
             "badges": len(emb.get("twitchBadges") or []) if emb else 0,
             "image_failed": images.failed if images else 0}
    return root, stats


def has_embedded_images(embedded: dict | None) -> bool:
    return bool(embedded) and any(embedded.get(k) for k in ("firstParty", "thirdParty", "twitchBadges"))


# ------------------------------------------------------------------------ 描画


def output_args(codec: str) -> str:
    """chatrender に渡す ffmpeg の出力指定（{save_path} は chatrender が置き換える）。"""
    if codec == "qtrle":
        args = ["-c:v", "qtrle", "-pix_fmt", "argb"]
    else:
        args = list(CODECS[codec]["args"])
    return " ".join(args) + ' "{save_path}"'


def geometry_for(geo: VideoGeometry, opts: TwitchStyleOptions) -> tuple[int, int, int]:
    """(幅, 高さ, 文字サイズ)。設定は高さ 1080 基準なので、クリップの高さに合わせて拡大縮小する。"""
    k = geo.height / 1080
    width = max(120, min(geo.width, round(opts.width * k / 2) * 2))
    return width, geo.height // 2 * 2, max(8, round(opts.font_size * k))


def _ms(sec: float) -> str:
    return f"{max(0, round(sec * 1000))}ms"


def render(json_path: Path, out_path: Path, geo: VideoGeometry, start: float, end: float, *,
           opts: TwitchStyleOptions | None = None, overlay: OverlayOptions | None = None, offline: bool = True,
           site: str = "twitch", on_progress=None, cancel: threading.Event | None = None) -> OverlayResult:
    """chatrender で [start, end)（動画の先頭からの秒）を描画し、アルファ付きの .mov にする。"""
    opts = opts or TwitchStyleOptions()
    overlay = overlay or OverlayOptions()
    exe = find_binary("TwitchDownloaderCLI")
    if not exe:
        raise RuntimeError("TwitchDownloaderCLI が見つかりません")
    width, height, font_size = geometry_for(geo, opts)
    fps = overlay.fps or geo.fps
    fps_int = max(1, round(float(parse_rate(str(fps)) or 30)))
    alpha = max(0, min(255, round(opts.bg_opacity * 255)))
    third = "true" if site == "twitch" else "false"
    tmp = Path(tempfile.mkdtemp(prefix="clipdl-chatrender-"))
    tmp_out = tmp / "out.mov"
    args = [exe, "chatrender", "-i", str(json_path), "-o", str(tmp_out),
            "-w", str(width), "-h", str(height), "-b", _ms(start), "-e", _ms(end),
            "--background-color", f"#{alpha:02X}000000", "--font", opts.font, "--font-size", str(font_size),
            "--message-fontstyle", "bold", "--username-fontstyle", "bold",
            "--outline", "true", "--outline-size", str(opts.outline), "--scale-vertical", f"{opts.scale_vertical:g}",
            "--scale-word-space", f"{WORD_SPACE_SCALE:g}",   # 折り返し位置の分の間隔を消す（make_breakable を参照）
            "--framerate", str(fps_int), "--bttv", third, "--ffz", third, "--stv", third,
            "--offline", "true" if offline else "false",
            "--ffmpeg-path", ffmpeg_path(), "--temp-path", str(tmp), "--collision", "Overwrite",
            "--banner", "false", "--log-level", "Status,Warning,Error",
            f"--output-args={output_args(overlay.codec)}"]
    log.info("chatrender: %s", subprocess.list2cmdline(args))
    tail: list[str] = []
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", creationflags=_NO_WINDOW, cwd=str(tmp))
        stop = threading.Event()

        def watch_cancel():
            while not stop.wait(0.3):
                if cancel is not None and cancel.is_set() and proc.poll() is None:
                    proc.kill()
                    return

        th = threading.Thread(target=watch_cancel, daemon=True)
        th.start()
        try:
            assert proc.stdout
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-30]
                mm = _PERCENT_RE.search(line)
                if mm and on_progress:
                    on_progress(min(100, int(mm.group(1))) / 100)
            proc.wait()
        finally:
            stop.set()
            th.join(timeout=1)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if proc.returncode != 0 or not tmp_out.is_file() or tmp_out.stat().st_size == 0:
            raise RuntimeError("chatrender が失敗しました:\n" + "\n".join(tail[-10:]))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), out_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # アプリが作った一時フォルダ
    res = OverlayResult(out_path, width, height, str(fps_int), end - start, geo.width - width, overlay.codec)
    px, py = res.premiere_position(geo.width, geo.height)
    log.info("Twitch配信風のオーバーレイ: %s（%dx%d %d fps %.2f 秒）Premiere の位置: X=%g Y=%g", out_path.name,
             width, height, fps_int, end - start, px, py)
    return res


def estimate_size_mb(duration: float, height: int = 1080, opts: TwitchStyleOptions | None = None,
                     overlay: OverlayOptions | None = None) -> float:
    o = opts or TwitchStyleOptions()
    ov = overlay or OverlayOptions()
    fps = float(parse_rate(str(ov.fps or 30)) or 30)
    per_sec = MEASURED_MB_PER_SEC.get(ov.codec, 1.0) * (fps / 30) * (o.width / 420)
    return per_sec * (height / 1080) ** 2 * max(0.0, duration)


def load_embedded(raw_path: Path) -> dict | None:
    """Twitch のチャット（キャッシュ）に埋め込まれた画像を読む。"""
    try:
        return json.loads(raw_path.read_text("utf-8")).get("embeddedData")
    except (OSError, ValueError):
        return None
