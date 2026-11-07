"""VFR（可変フレームレート）の判定と CFR（固定フレームレート）化。

判定は 2 つを組み合わせる:
  1. ffprobe の r_frame_rate と avg_frame_rate の差
  2. ffmpeg の vfrdet フィルタ（出力例: "VFR:0.012345 (12/987)"）
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .ffmpeg import _run, ffmpeg_path, ffprobe_json, run_with_progress, video_encode_args

log = logging.getLogger(__name__)

# 編集でよく使うフレームレート
STANDARD_RATES: tuple[tuple[str, float], ...] = (
    ("24000/1001", 24000 / 1001), ("24", 24.0), ("25", 25.0), ("30000/1001", 30000 / 1001), ("30", 30.0),
    ("48", 48.0), ("50", 50.0), ("60000/1001", 60000 / 1001), ("60", 60.0),
    ("120", 120.0), ("144", 144.0),
)

VFR_RATIO_THRESHOLD = 0.01   # vfrdet の比率がこれを超えたら VFR
RATE_MISMATCH = 0.01         # r_frame_rate と avg_frame_rate が 1% 以上違えば VFR の疑い

_VFRDET_RE = re.compile(r"VFR:\s*([0-9.]+|nan)\s*\((\d+)/(\d+)\)")


def parse_rate(text: str | None) -> float | None:
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        v = float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None
    return v if v > 0 else None


def nearest_standard_rate(fps: float | None) -> str:
    """実測 fps に最も近い標準フレームレート（ffmpeg の書式）を返す。不明なら 30。"""
    if not fps or fps <= 0:
        return "30"
    best = min(STANDARD_RATES, key=lambda r: abs(r[1] - fps))
    return best[0]


def parse_vfrdet(stderr: str) -> tuple[float | None, int, int]:
    """vfrdet の最後の出力から (比率, VFR と判定されたフレーム数, 全フレーム数) を取り出す。"""
    matches = _VFRDET_RE.findall(stderr)
    if not matches:
        return None, 0, 0
    ratio, vfr, total = matches[-1]
    vfr_n, total_n = int(vfr), int(total)
    if ratio == "nan" or total_n == 0:
        return None, vfr_n, total_n
    return float(ratio), vfr_n, total_n


@dataclass
class FrameRateReport:
    r_frame_rate: float | None
    avg_frame_rate: float | None
    vfr_ratio: float | None
    is_vfr: bool
    target: str            # CFR 化するときの目標 fps

    def summary(self) -> str:
        def f(v):
            return "?" if v is None else f"{v:.3f}"
        return (f"r={f(self.r_frame_rate)} avg={f(self.avg_frame_rate)} vfrdet={f(self.vfr_ratio)} "
                f"→ {'VFR' if self.is_vfr else 'CFR'}（目標 {self.target}）")


def judge(r_rate: float | None, avg_rate: float | None, vfr_ratio: float | None) -> bool:
    """どちらか一方でも VFR を示せば VFR とみなす。

    vfrdet は「フレーム間隔が変わった回数」を数えるため、途中で 1 回だけ fps が変わる動画では
    比率が小さく出る。その場合は公称（r_frame_rate）と平均（avg_frame_rate）のずれで拾う。
    """
    if vfr_ratio is not None and vfr_ratio > VFR_RATIO_THRESHOLD:
        return True
    if r_rate and avg_rate:
        return abs(r_rate - avg_rate) / r_rate > RATE_MISMATCH
    return False


def target_rate(r_rate: float | None, avg_rate: float | None) -> str:
    """CFR 化の目標。フレームを落とさないよう公称値（最大側）を優先する。異常値なら平均を使う。"""
    if r_rate and r_rate <= 240:
        return nearest_standard_rate(r_rate)
    return nearest_standard_rate(avg_rate or r_rate)


def analyze(path: str | Path, sample_seconds: float = 120.0) -> FrameRateReport:
    info = ffprobe_json(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        return FrameRateReport(None, None, None, False, "30")
    r_rate, avg_rate = parse_rate(v.get("r_frame_rate")), parse_rate(v.get("avg_frame_rate"))
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0",
            "-t", str(sample_seconds), "-vf", "vfrdet", "-an", "-f", "null", "-"]
    r = _run(args, timeout=600)
    ratio, _, _ = parse_vfrdet(r.stderr)
    is_vfr = judge(r_rate, avg_rate, ratio)
    report = FrameRateReport(r_rate, avg_rate, ratio, is_vfr, target_rate(r_rate, avg_rate))
    log.info("フレームレート判定 %s: %s", Path(path).name, report.summary())
    return report


def convert_to_cfr(src: Path, dst: Path, fps: str, duration: float | None, on_progress=None,
                   cancel: threading.Event | None = None, encoder=None) -> None:
    args = [ffmpeg_path(), "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
            *video_encode_args(fps, container=dst.suffix.lstrip(".").lower(), encoder=encoder), str(dst)]
    run_with_progress(args, duration, on_progress, cancel)
