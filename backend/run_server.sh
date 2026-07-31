#!/bin/bash
# AI 模拟面试系统 启动脚本 (macOS / Linux)
# 等价于 Windows 的 run_server.bat
set -e

# 切换到脚本所在目录（即 backend/）
cd "$(dirname "$0")"

# 本地回环不走代理，避免某些环境下 LLM 调用被代理拦截
export NO_PROXY=localhost,127.0.0.1
export no_proxy=localhost,127.0.0.1

# 若没有 .env，则从模板复制一份（之后需手动填 LLM_API_KEY）
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env，请编辑并填入真实的 LLM_API_KEY 后再启动。"
  echo "  vim .env   # 或 open -e .env"
  exit 1
fi

# 确保 uv 在 PATH 中（本机 uv 可能装在 ~/.hermes/bin 等非标准位置）
if ! command -v uv >/dev/null 2>&1; then
  for d in "$HOME/.hermes/bin" "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
    if [ -x "$d/uv" ]; then
      export PATH="$d:$PATH"
      break
    fi
  done
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "未检测到 uv，正在安装（需要网络）..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 优先使用已同步好的虚拟环境（.venv），避免 uv run 在本机受限网络下联网校验卡死
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  # 确保 uv 在 PATH 中
  if ! command -v uv >/dev/null 2>&1; then
    for d in "$HOME/.hermes/bin" "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
      if [ -x "$d/uv" ]; then export PATH="$d:$PATH"; break; fi
    done
  fi
  uv sync
  PY="uv run"
fi

# 启动服务（前台运行，Ctrl+C 停止）
# WORKERS 控制 uvicorn worker 进程数（多进程并发处理请求，利用多核）。
# 说明：
#  - 会话状态已落盘（live_sessions/），任意 worker 都能恢复，多 worker 不会串会话；
#  - history.json / mistakes.json 已用跨进程文件锁保护，多 worker 并发写安全；
#  - 默认 2 个 worker；可调大（如 4）以支撑更多并发面试，注意 LLM API 限速。
#  - Windows 不支持 fcntl 文件锁，请保持 WORKERS=1。
exec $PY -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WORKERS:-2}"
