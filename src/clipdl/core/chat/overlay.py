"""チャットのオーバーレイ動画（背景が透明な .mov）を書き出す。

ASS は Premiere / DaVinci Resolve で読み込めないため、ffmpeg（libass）で描画して
アルファチャンネル付きの動画にする。編集ソフトでクリップの上のトラックに置けばそのまま重なる。
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from ..cfr import nearest_standard_rate, parse_rate
from ..ffmpeg import ffmpeg_path, ffprobe_json, run_with_progress

log = logging.getLogger(__name__)

OVERLAY_PIX_FMT = "yuva444p10le"   # ProRes 4444 のアルファ付き（読み戻すと yuva444p12le と表示される）


@dataclass(frozen=True)
class VideoGeometry:
    width: int
    height: int
    fps: str          # ffmpeg 書式（"60" / "30000/1001"）
    duration: float

    @classmethod
    def from_file(cls, path: Path) -> "VideoGeometry":
        """クリップ本体の解像度・fps・長さを読む。"""
        info = ffprobe_json(path)
        v = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
        fps = v.get("r_frame_rate") or v.get("avg_frame_rate") or "30"
        if not parse_rate(fps):
            fps = "30"
        dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
        return cls(int(v["width"]), int(v["height"]), fps, dur)

    @classmethod
    def fallback(cls, width: int | None, height: int | None, fps: float | None,
                 duration: float) -> "VideoGeometry":
        """動画を保存しない（チャットだけ）場合に、動画情報から決める。"""
        w, h = width or 1920, height or 1080
        if h > 1080:  # 初期プリセット（編集用 1080p）に合わせる
            w, h = round(w * 1080 / h / 2) * 2, 1080
        return cls(w, h, nearest_standard_rate(fps), duration)


# 形式ごとの ffmpeg 設定（PLAN.md / README の比較表を参照）
#   pix: lavfi で描画するときの画素形式、args: エンコーダ指定
CODECS: dict[str, dict] = {
    # QuickTime Animation（RLE・差分方式）。変化した行だけを記録するので、チャットのように
    # 大半が変わらない映像はとても小さくなる。Premiere Pro 2026 でアルファ付き・途中シークとも確認済み
    "qtrle": {"pix": "argb", "args": ["-c:v", "qtrle", "-pix_fmt", "argb"], "label": "QuickTime Animation"},
    "png": {"pix": "rgba", "args": ["-c:v", "png", "-pix_fmt", "rgba", "-pred", "mixed"], "label": "PNG"},
    # 文字と縁取りだけなので、アルファ 8bit・qscale 8 でも見た目は変わらない
    "prores": {"pix": OVERLAY_PIX_FMT, "args": ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt",
                                                OVERLAY_PIX_FMT, "-alpha_bits", "8", "-qscale:v", "8",
                                                "-vendor", "apl0"], "label": "ProRes 4444"},
}
# 実測（1080p・Twitch のチャット多めの 20 秒）から求めた 1 秒あたりの MB。予想サイズの表示に使う
#   キー: (形式, 切り出し, fps)。チャットが少なければ実際はこれより小さい
MEASURED_MB_PER_SEC_1080P: dict[tuple[str, bool, int], float] = {
    ("qtrle", True, 30): 1.07, ("qtrle", True, 60): 1.64, ("qtrle", False, 30): 1.20, ("qtrle", False, 60): 1.90,
    ("png", True, 30): 4.48, ("png", True, 60): 8.95, ("png", False, 30): 5.24, ("png", False, 60): 10.47,
    ("prores", True, 30): 5.42, ("prores", True, 60): 10.83, ("prores", False, 30): 6.08,
    ("prores", False, 60): 12.15,
}
CROP_PAD = 8  # 縁取りが欠けないよう、チャット欄の左に足す余白（px）


@dataclass(frozen=True)
class OverlayOptions:
    codec: str = "qtrle"
    crop: bool = True          # チャット欄の幅だけ切り出す
    fps: str | None = "30"     # None ならクリップ本体に合わせる


@dataclass(frozen=True)
class OverlayResult:
    path: Path
    width: int
    height: int
    fps: str
    duration: float
    crop_x: int                # 元の画面での左端（切り出しなしなら 0）
    codec: str

    def premiere_position(self, seq_width: int, seq_height: int) -> tuple[float, float]:
        """Premiere の「位置」（クリップ中心の座標）。シーケンスがクリップ本体と同じ解像度の前提。"""
        return (self.crop_x + self.width / 2, seq_height / 2) if self.crop_x else (seq_width / 2, seq_height / 2)


def estimate_size_mb(duration: float, height: int = 1080, fps: float | str | None = None,
                     options: "OverlayOptions | None" = None) -> float:
    """オーバーレイ動画の予想サイズ（MB）。チャット多めの実測値が基準なので、目安として「約」で表示する。"""
    o = options or OverlayOptions()
    f = float(parse_rate(str(o.fps or fps or 30)) or 30)
    key = (o.codec, o.crop, 60 if f > 45 else 30)
    per_sec = MEASURED_MB_PER_SEC_1080P[key] * (f / key[2])
    return per_sec * (height / 1080) ** 2 * max(0.0, duration)  # チャット欄の面積は高さの 2 乗に比例


def format_size(mb: float) -> str:
    """表示用（例: 約 640 MB / 約 2.4 GB）。"""
    return f"約 {mb / 1024:.1f} GB" if mb >= 1000 else f"約 {max(1, round(mb, -1 if mb >= 100 else 0)):g} MB"


def crop_box(margin_l: int, width: int, pad: int = CROP_PAD) -> tuple[int, int]:
    """チャット欄の切り出し範囲 (x, 幅)。どちらも偶数にそろえる。"""
    x = max(0, (margin_l - pad) // 2 * 2)
    return x, (width - x) // 2 * 2


def ass_margin_l(ass_text: str) -> int:
    """ASS の Chat スタイルの MarginL（チャット欄の左端）を読む。"""
    fmt = next(l for l in ass_text.splitlines() if l.startswith("Format: Name"))
    sty = next(l for l in ass_text.splitlines() if l.startswith("Style: Chat"))
    cols = [c.strip() for c in fmt.split(":", 1)[1].split(",")]
    vals = [v.strip() for v in sty.split(":", 1)[1].split(",")]
    return int(vals[cols.index("MarginL")])


def render_overlay(ass_path: Path, out_path: Path, geo: VideoGeometry, on_progress=None,
                   cancel: threading.Event | None = None,
                   options: OverlayOptions | None = None) -> OverlayResult:
    """ASS を透明な背景に描画し、アルファ付きの .mov にする。"""
    opts = options or OverlayOptions()
    if geo.duration <= 0:
        raise ValueError("オーバーレイ動画の長さが 0 です")
    codec = CODECS[opts.codec]
    fps = opts.fps or geo.fps
    vf = "ass=chat.ass:alpha=1"
    crop_x, out_w = 0, geo.width
    if opts.crop:
        crop_x, out_w = crop_box(ass_margin_l(ass_path.read_text("utf-8-sig")), geo.width)
        vf += f",crop={out_w}:{geo.height}:{crop_x}:0"
    # ffmpeg のフィルタ指定ではパスの「:」「\」「'」などを特別扱いするため、
    # 単純な名前で一時フォルダに置き、そのフォルダを作業フォルダにして渡す
    tmp = Path(tempfile.mkdtemp(prefix="clipdl-ovl-"))
    try:
        shutil.copyfile(ass_path, tmp / "chat.ass")
        src = (f"color=c=black@0.0:s={geo.width}x{geo.height}:r={fps}:d={geo.duration:.3f},"
               f"format={codec['pix']}")
        args = [ffmpeg_path(), "-f", "lavfi", "-i", src, "-vf", vf, *codec["args"], "-an", "tmp_out.mov"]
        run_with_progress(args, geo.duration, on_progress, cancel, cwd=tmp)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp / "tmp_out.mov"), out_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # アプリが作った一時フォルダ
    res = OverlayResult(out_path, out_w, geo.height, fps, geo.duration, crop_x, opts.codec)
    px, py = res.premiere_position(geo.width, geo.height)
    log.info("オーバーレイ動画: %s（%s %dx%d %s fps %.2f 秒）%s", out_path.name, codec["label"], out_w,
             geo.height, fps, geo.duration,
             f" Premiere の位置: X={px:g} Y={py:g}（シーケンス {geo.width}x{geo.height}）" if crop_x else "")
    return res
