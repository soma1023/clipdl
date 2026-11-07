"""GUI モックの HTML（ライト・ダーク）を組み立て、Edge のヘッドレスモードで PNG にする。

使い方: .venv\\Scripts\\python.exe docs\\mockup\\build_mockup.py
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

WINDOW = """
<div>
<p class="caption">{caption}</p>
<div class="win {theme}">
 <div class="title">clipdl<div class="ctl">─ ☐ ✕</div></div>
 <div class="body">

  <div class="sec">
   <p class="lab"><span class="step">1</span>URL を貼る（貼り付けると自動で取得）</p>
   <input type="text" class="mono" style="width:100%" value="https://www.twitch.tv/videos/0000000000">
   <div class="row" style="margin-top:10px;gap:12px">
    <div class="thumb"><span>6:25:51</span></div>
    <div style="min-width:0">
     <p class="ell" style="font-size:14px">サンプル配信アーカイブ（デモ用）</p>
     <p class="small">sample_channel ・ Twitch ・ 2026/11/01 ・ チャットあり</p>
    </div>
   </div>
  </div>

  <div class="sec">
   <p class="lab"><span class="step">2</span>範囲（空なら動画全体）　つまみをドラッグ、または時刻を入力</p>
   <div class="bar">
    <div class="track"></div>
    <span class="tick" style="left:0">0:00:00</span><span class="tick" style="right:0">6:25:51</span>
    <div class="seg" style="left:15.56%;width:0.65%"></div>
    <div class="knob" style="left:15.56%"></div><div class="knob" style="left:16.21%"></div>
    <div class="seg sel" style="left:23.34%;width:1.81%"></div>
    <div class="knob" style="left:23.34%"></div><div class="knob" style="left:25.15%"></div>
   </div>
   <div class="clip"><span class="n">1</span><div class="field mono">01:00:00</div><span style="color:var(--muted)">〜</span><div class="field mono">01:02:30</div><span class="note">2:30</span><span></span><span class="ib">✕</span></div>
   <div class="clip sel"><span class="n">2</span><div class="field mono">01:30:00</div><span style="color:var(--muted)">〜</span><div class="field mono">01:37:00</div><span class="note">7:00</span><span></span><span class="ib">✕</span></div>
   <div style="margin-top:6px"><span class="btn">＋ 範囲を追加</span></div>
  </div>

  <div class="sec">
   <div class="row" style="gap:18px">
    <span class="lab" style="margin:0">画質</span><div class="field" style="width:190px;justify-content:space-between">編集用 1080p MP4 <span style="color:var(--muted)">⌄</span></div>
    <span class="chk"><span class="box on">✓</span>チャットも保存</span>
   </div>
   <div class="row" style="margin-top:10px">
    <span class="lab" style="margin:0;width:38px">保存先</span><input type="text" style="flex:1" value="C:\\clipdl-demo"><span class="btn">変更</span>
   </div>
   <div class="row" style="margin-top:12px;justify-content:space-between">
    <span style="color:var(--sub)">⌄ 詳細設定</span>
    <span class="btn primary"><span class="step" style="background:var(--onaccent);color:var(--accent)">3</span>ダウンロード</span>
   </div>
   <div class="adv">
    <div><p class="lab">カット</p><div class="field">高速（キーフレーム単位）<span style="color:var(--muted)">⌄</span></div></div>
    <div><p class="lab">CFR 変換</p><div class="field">自動<span style="color:var(--muted)">⌄</span></div></div>
    <div style="grid-column:1 / span 2"><p class="lab">チャット形式</p>
     <div class="row" style="gap:16px">
      <span class="chk"><span class="box on">✓</span>JSON</span><span class="chk"><span class="box">&nbsp;</span>CSV</span>
      <span class="chk"><span class="box on">✓</span>ASS</span>
      <span class="chk"><span class="box on">✓</span>オーバーレイ動画（.mov）</span>
      <span class="note">予想サイズ 約 610 MB（2 範囲・9:30）</span>
     </div></div>
    <div><p class="lab">ASS の表示</p><div class="field">縦流れ（Twitch 風）<span style="color:var(--muted)">⌄</span></div></div>
    <div><p class="lab">ファイル名</p><div class="field mono">{{date}}_{{channel}}_{{title}}</div></div>
    <div><p class="lab">Cookie（メンバー限定動画）</p><div class="field">使わない<span style="color:var(--muted)">⌄</span></div></div>
    <div><p class="lab">言語 ／ サポート</p><div class="row"><div class="field" style="width:96px;justify-content:space-between">日本語<span style="color:var(--muted)">⌄</span></div><span class="btn">ログを書き出す</span><span class="btn">更新を確認</span></div></div>
   </div>
  </div>

  <div class="sec">
   <p class="lab" style="margin-bottom:0">キュー</p>
   <div class="q"><div style="min-width:0"><p class="ell">サンプル配信アーカイブ（デモ用） ・ クリップ 1/2</p><p class="small">エンコード中（h264_nvenc）</p></div><div><div class="pb"><i style="width:62%"></i></div><p class="small" style="text-align:right">62%</p></div><div class="row" style="justify-content:flex-end;gap:4px"><span class="ib">❚❚</span><span class="ib">✕</span></div></div>
   <div class="q"><div style="min-width:0"><p class="ell">サンプル動画 2（デモ用）</p><p class="small">一時停止中</p></div><div><div class="pb"><i style="width:30%;background:var(--muted)"></i></div></div><div class="row" style="justify-content:flex-end;gap:4px"><span class="ib">▶</span><span class="ib">✕</span></div></div>
   <div class="q"><div style="min-width:0"><p class="ell">Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film</p><p class="small" style="color:var(--ok)">完了 ・ 3 ファイル</p></div><div></div><div class="row" style="justify-content:flex-end"><span class="ib">📁</span></div></div>
   {confirm}
  </div>
 </div>
 <div class="status">⚠ <span style="flex:1">メンバー限定動画です。詳細設定の「Cookie」でログイン済みのブラウザを指定してください。</span><span class="btn">詳しく</span></div>
</div>
</div>
"""

CONFIRM = """<div class="confirm"><span style="flex:1">3 件をキューに追加（動画全体・現在の設定）</span>
<span class="btn" style="height:26px;background:var(--accent);color:var(--onaccent);border-color:var(--accent)">追加</span>
<span class="btn" style="height:26px">やめる</span></div>"""


def main() -> None:
    html = (HERE / "main_window.html").read_text("utf-8")
    body = (WINDOW.format(caption="ライト（URL を複数貼ったときの確認行をキューの下に表示）", theme="light",
                          confirm=CONFIRM)
            + WINDOW.format(caption="ダーク（OS の設定に追従）", theme="dark", confirm=""))
    out_html = HERE / "_render.html"
    out_html.write_text(html.replace("<!--TEMPLATE-->", body), "utf-8")
    png = HERE / "main_window.png"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--screenshot={png}",
                    "--window-size=1760,1280", "--force-device-scale-factor=1", out_html.as_uri()],
                   check=True, capture_output=True)
    print(png)


if __name__ == "__main__":
    main()
