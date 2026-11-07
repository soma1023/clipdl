"""clipdl 本体の入れ替え（clipdl-updater.exe の中身）。標準ライブラリだけで書く。

アプリが「計画ファイル」（update-plan.json）を書いてから、このプログラムを一時フォルダのコピーで起動して終了する。
このプログラムは:
  1. アプリのプロセスが終わるのを待つ（待ちきれなければ何もしない）
  2. clipdl → clipdl.old、clipdl.new → clipdl と名前を変える（どれも同じドライブ内の名前の変更）
  3. 新しい clipdl.exe を起動し、「起動できた」印（update-ok.json）を待つ
  4. 印が来なければ新しい版を clipdl.failed に退け、clipdl.old を clipdl に戻して古い版を起動する
結果は update-result.json に書く（古い版が起動したときに画面下部へ表示する）。

1 つ前の版（clipdl.old）は残す。次の更新のときに、clipdl のフォルダであることを確かめてから消す。
（使用中で消しきれなかった控えは clipdl.old-<数字> に退け、その次の更新で消す。控えは増えていかない）
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("clipdl.swap")

PLAN_FORMAT = 1
EXE_NAME = "clipdl.exe"
VERSION_FILE = "VERSION"


@dataclass
class Plan:
    install_dir: Path
    staging_dir: Path
    backup_dir: Path
    failed_dir: Path
    pid: int
    old_version: str
    new_version: str
    ok_marker: Path
    result: Path
    log: Path | None = None
    wait_exit: float = 60.0
    wait_ok: float = 60.0

    @classmethod
    def load(cls, path: Path) -> "Plan":
        d = json.loads(Path(path).read_text("utf-8"))
        if d.get("format") != PLAN_FORMAT:
            raise ValueError(f"計画ファイルの形式が違います: {d.get('format')}")
        p = {k: Path(d[k]) for k in ("install_dir", "staging_dir", "backup_dir", "failed_dir", "ok_marker", "result")}
        return cls(**p, pid=int(d["pid"]), old_version=str(d["old_version"]), new_version=str(d["new_version"]),
                   log=Path(d["log"]) if d.get("log") else None,
                   wait_exit=float(d.get("wait_exit", 60)), wait_ok=float(d.get("wait_ok", 60)))

    def dump(self) -> dict:
        d = {k: str(getattr(self, k)) for k in ("install_dir", "staging_dir", "backup_dir", "failed_dir",
                                                 "ok_marker", "result")}
        d.update(format=PLAN_FORMAT, pid=self.pid, old_version=self.old_version, new_version=self.new_version,
                 log=str(self.log) if self.log else "", wait_exit=self.wait_exit, wait_ok=self.wait_ok)
        return d


class Ops:
    """OS とのやりとり（テストでは差し替える）。"""

    def wait_pid_exit(self, pid: int, timeout: float) -> bool:
        if os.name != "nt":
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except OSError:
                    return True
                time.sleep(0.2)
            return False
        import ctypes
        SYNCHRONIZE = 0x00100000
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return True   # すでに終わっている
        try:
            return k32.WaitForSingleObject(h, int(timeout * 1000)) == 0   # WAIT_OBJECT_0
        finally:
            k32.CloseHandle(h)

    def launch(self, exe: Path):
        if os.name != "nt":
            return subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
        # このプログラム（PyInstaller の 1 ファイル exe）は、終了時に子プロセスごと終わらせる「ジョブ」の中で動く。
        # 起動した clipdl まで巻き込まれて終了しないよう、ジョブから外して起動する（外せない環境ではそのまま起動）
        base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        try:
            proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                                    creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB)  # type: ignore[attr-defined]
            log.info("起動しました（ジョブから外して起動）: %s pid=%s", exe, proc.pid)
            return proc
        except OSError as e:
            log.info("ジョブから外して起動できなかったため、そのまま起動します（問題ありません）: %s", e)
        proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=base, close_fds=True)
        log.info("起動しました: %s pid=%s", exe, proc.pid)
        return proc

    def sleep(self, sec: float) -> None:
        time.sleep(sec)

    def now(self) -> float:
        return time.monotonic()


def _retry(fn, ops: Ops, tries: int = 20, delay: float = 0.5):
    """ウイルス対策ソフトの検査などで一瞬だけ使用中になることがあるので、少し待ってやり直す。"""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            ops.sleep(delay)


def looks_like_clipdl(d: Path) -> bool:
    return (d / EXE_NAME).is_file() and (d / "_internal").is_dir()


def _remove_dir(d: Path) -> None:
    """clipdl のフォルダであることを確かめてから消す。消せなければ脇に退ける。"""
    if not d.exists():
        return
    if not looks_like_clipdl(d):
        raise RuntimeError(f"{d} は clipdl のフォルダではないため消しません")
    shutil.rmtree(d, ignore_errors=True)
    if d.exists():   # 一部のファイルが使用中で消せなかった
        d.rename(d.with_name(f"{d.name}-{int(time.time())}"))


def _sweep_set_aside(d: Path) -> None:
    """_remove_dir が脇に退けた「<名前>-<数字>」のフォルダを消す（控えが増えていかないように）。

    名前が完全に一致するものだけを対象にする。消せないものは、そのまま次の更新に回す。
    """
    for p in d.parent.glob(f"{d.name}-*"):
        if p.is_dir() and p.name[len(d.name) + 1:].isdigit():
            shutil.rmtree(p, ignore_errors=True)
            if p.exists():
                log.info("前の版の控えの残りを消せませんでした（次の更新で消します）: %s", p)


def _write_result(plan: Plan, status: str, message: str) -> None:
    data = {"status": status, "message": message, "old_version": plan.old_version,
            "new_version": plan.new_version, "time": time.time()}
    try:
        plan.result.parent.mkdir(parents=True, exist_ok=True)
        plan.result.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        log.exception("結果を書けませんでした")
    log.info("結果: %s %s", status, message)


def _wait_ok(plan: Plan, proc, ops: Ops) -> bool:
    deadline = ops.now() + plan.wait_ok
    while ops.now() < deadline:
        if plan.ok_marker.is_file():
            try:
                if json.loads(plan.ok_marker.read_text("utf-8")).get("version") == plan.new_version:
                    return True
            except (OSError, ValueError):
                pass
        if proc is not None and proc.poll() is not None:
            log.warning("新しい版が起動の途中で終了しました（終了コード %s）", proc.returncode)
            return False
        ops.sleep(0.5)
    log.warning("新しい版から起動できたという印が %s 秒以内に来ませんでした", plan.wait_ok)
    return False


def run(plan: Plan, ops: Ops | None = None) -> str:
    """入れ替えを行い、結果（ok / rolled_back / not_started / broken）を返す。"""
    ops = ops or Ops()
    log.info("更新を開始: %s → %s（%s）", plan.old_version, plan.new_version, plan.install_dir)
    if not ops.wait_pid_exit(plan.pid, plan.wait_exit):
        _write_result(plan, "not_started", "clipdl が終了しなかったため、更新しませんでした。")
        return "not_started"

    old_exe = plan.install_dir / EXE_NAME
    if not looks_like_clipdl(plan.staging_dir):
        _write_result(plan, "not_started", "新しい版のファイルがそろっていないため、更新しませんでした。")
        ops.launch(old_exe)
        return "not_started"
    _sweep_set_aside(plan.backup_dir)
    try:
        _remove_dir(plan.backup_dir)
    except (OSError, RuntimeError) as e:
        _write_result(plan, "not_started", f"前の版の控え（{plan.backup_dir.name}）を片付けられませんでした: {e}")
        ops.launch(old_exe)
        return "not_started"

    # 1. 今の版を控えに回す
    try:
        _retry(lambda: plan.install_dir.rename(plan.backup_dir), ops)
    except OSError as e:
        _write_result(plan, "not_started", f"clipdl のフォルダが使用中のため、更新しませんでした: {e}")
        ops.launch(old_exe)
        return "not_started"
    # 2. 新しい版を置く
    try:
        _retry(lambda: plan.staging_dir.rename(plan.install_dir), ops)
    except OSError as e:
        log.exception("新しい版を置けませんでした")
        return _restore(plan, ops, f"新しい版を置けませんでした: {e}")

    # 3. 新しい版を起動して、起動できたことを確かめる
    try:
        plan.ok_marker.unlink()
    except FileNotFoundError:
        pass
    proc = None
    try:
        proc = ops.launch(plan.install_dir / EXE_NAME)
    except OSError as e:
        log.exception("新しい版を起動できませんでした")
        return _restore(plan, ops, f"新しい版を起動できませんでした: {e}")
    if _wait_ok(plan, proc, ops):
        _write_result(plan, "ok", f"clipdl {plan.new_version} に更新しました。")
        return "ok"
    if proc is not None and proc.poll() is None:
        proc.kill()
        ops.wait_pid_exit(proc.pid, 15)
    return _restore(plan, ops, "新しい版が正しく起動しませんでした。")


def _restore(plan: Plan, ops: Ops, reason: str) -> str:
    """新しい版を退けて、控えておいた元の版に戻す。"""
    try:
        if plan.install_dir.exists():
            if plan.failed_dir.exists():
                shutil.rmtree(plan.failed_dir, ignore_errors=True)
            _retry(lambda: plan.install_dir.rename(plan.failed_dir), ops)
        _retry(lambda: plan.backup_dir.rename(plan.install_dir), ops)
    except OSError as e:
        log.exception("元の版に戻せませんでした")
        _write_result(plan, "broken",
                      f"{reason} 元の版にも戻せませんでした（{e}）。{plan.backup_dir} を {plan.install_dir.name} に"
                      "名前を変えると元の版を使えます。")
        return "broken"
    shutil.rmtree(plan.failed_dir, ignore_errors=True)
    _write_result(plan, "rolled_back", f"{reason} 元の版（{plan.old_version}）に戻しました。")
    try:
        ops.launch(plan.install_dir / EXE_NAME)
    except OSError:
        log.exception("元の版を起動できませんでした")
    return "rolled_back"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        return 2
    plan = Plan.load(Path(argv[0]))
    if plan.log:
        plan.log.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(plan.log), encoding="utf-8", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        status = run(plan)
    except Exception as e:  # noqa: BLE001 — 何があっても結果を残す
        log.exception("入れ替えの途中で想定外のエラー")
        _write_result(plan, "broken", f"想定外のエラー: {e}")
        return 1
    return 0 if status in ("ok", "rolled_back") else 1


if __name__ == "__main__":
    raise SystemExit(main())
