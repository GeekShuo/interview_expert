"""手动迁移：旧 JSON 存储（history / mistakes / live_sessions）→ SQLite。

应用启动时会自动执行同一迁移（幂等），本脚本用于部署时手动执行与结果验证。

用法（在 backend/ 目录下）：
    .venv\\Scripts\\python.exe scripts\\migrate_json_to_sqlite.py   # Windows
    python scripts/migrate_json_to_sqlite.py                        # macOS/Linux
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import db  # noqa: E402


def main():
    db.init_db()
    db.migrate_legacy_json()
    stats = {
        "users": db.query_one("SELECT COUNT(*) AS n FROM users")["n"],
        "interviews": db.query_one("SELECT COUNT(*) AS n FROM interviews")["n"],
        "mistakes": db.query_one("SELECT COUNT(*) AS n FROM mistakes")["n"],
        "live_sessions": db.query_one("SELECT COUNT(*) AS n FROM live_sessions")["n"],
    }
    print("迁移完成，当前库内数据量：", stats)


if __name__ == "__main__":
    main()
