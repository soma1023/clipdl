"""gui_walkthrough.py が撮った画像を 1 枚にまとめる（Edge のヘッドレスモードで PNG 化）。

使い方: .venv\\Scripts\\python.exe docs\\demo\\build_sheet.py <撮影フォルダ> <出力PNG>
"""
import html
import subprocess
import sys
from pathlib import Path

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
shots = Path(sys.argv[1])
out = Path(sys.argv[2]).resolve()  # Edge は相対パスだと書き出さないことがある
items = [line.split("\t", 1) for line in (shots / "index.txt").read_text("utf-8").splitlines() if line.strip()]
pick = [i for i in items if not i[0].startswith(("01_", "02_"))]  # 操作の 4 段階
cells = "".join(
    f'<figure><figcaption>{html.escape(c)}</figcaption><img src="{(shots / n).as_uri()}"></figure>'
    for n, c in pick)
doc = f"""<!DOCTYPE html><meta charset="utf-8"><style>
body {{ margin:0; padding:24px; background:#3b3f45; font-family:"Yu Gothic UI",sans-serif; display:flex; gap:18px; }}
figure {{ margin:0; width:600px; }}
figcaption {{ color:#fff; font-size:15px; margin:0 0 8px; min-height:44px; }}
img {{ width:600px; border-radius:6px; border:1px solid #0006; display:block; }}
</style>{cells}"""
page = shots / "_sheet.html"
page.write_text(doc, "utf-8")
subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--screenshot={out}",
                f"--window-size={24 * 2 + 600 * len(pick) + 18 * (len(pick) - 1)},760",
                "--force-device-scale-factor=1", page.as_uri()], check=True, capture_output=True)
print(out)
