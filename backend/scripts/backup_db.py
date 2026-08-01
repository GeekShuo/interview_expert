"""SQLite 数据库备份：data/app.db → data/backups/app-YYYYMMDD_HHMMSS.db，保留最近 14 份。

用法（在 backend/ 目录下）：
    .venv\\Scripts\\python.exe scripts\\backup_db.py   # Windows
    python scripts/backup_db.py                        # macOS/Linux

建议每日执行：Windows 用「任务计划程序」，Linux 用 cron：
    0 3 * * * cd /path/to/backend && .venv/bin/python scripts/backup_db.py
"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import db  # noqa: E402

KEEP = 14


def main():
    backup_dir = os.path.join(db.DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    dst = os.path.join(backup_dir, f"app-{time.strftime('%Y%m%d_%H%M%S')}.db")
    src = sqlite3.connect(db.DB_PATH)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)  # SQLite 在线备份 API：运行中的库也可安全备份
    src.close()
    out.close()
    files = sorted(f for f in os.listdir(backup_dir) if f.startswith("app-") and f.endswith(".db"))
    for f in files[:-KEEP]:
        os.remove(os.path.join(backup_dir, f))
    print(f"备份完成: {dst}（共 {min(len(files), KEEP)} 份，保留最近 {KEEP} 份）")


if __name__ == "__main__":
    main()
