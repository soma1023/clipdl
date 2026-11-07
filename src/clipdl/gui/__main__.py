# 更新版の yt-dlp を使うかどうかは、yt-dlp を import する前に決める（core/bootstrap.py）
from clipdl.core.bootstrap import startup

startup()

from clipdl.gui.app import main  # noqa: E402

raise SystemExit(main())
