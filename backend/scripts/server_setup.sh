#!/bin/bash
# 服务器一键初始化（Ubuntu 22.04 腾讯云）：Docker + uv + git 部署仓库 + systemd 服务
#
# 用法（首次部署时在服务器上执行）：
#   bash server_setup.sh
#
# 之后的日常部署：本地 git push prod main 即可（post-receive 钩子自动检出并重启服务）。
set -e

APP_DIR=/opt/interview
REPO_DIR=/opt/repos/interview.git
SUDO=""
[ "$(whoami)" != "root" ] && SUDO="sudo"

echo "===== 1/6 安装 Docker（腾讯云内网 apt 源）====="
if ! command -v docker >/dev/null 2>&1; then
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq ca-certificates curl gnupg
  $SUDO install -m 0755 -d /etc/apt/keyrings
  # 优先腾讯云内网镜像（免流量、最快），失败回退清华源
  MIRROR=https://mirrors.cloud.tencent.com/docker-ce/linux/ubuntu
  curl -fsSL --connect-timeout 5 $MIRROR/gpg -o /tmp/docker.gpg || {
    MIRROR=https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu
    curl -fsSL $MIRROR/gpg -o /tmp/docker.gpg
  }
  $SUDO gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg /tmp/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] $MIRROR $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io
fi
# Docker Hub 拉镜像加速（腾讯云内网 registry 镜像）
if [ ! -f /etc/docker/daemon.json ]; then
  echo '{"registry-mirrors":["https://mirror.ccs.tencentyun.com"]}' | $SUDO tee /etc/docker/daemon.json >/dev/null
fi
$SUDO systemctl enable --now docker
docker --version

echo "===== 2/6 安装 uv ====="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf --connect-timeout 8 https://astral.sh/uv/install.sh | sh || {
    $SUDO apt-get install -y -qq python3-pip
    python3 -m pip install --user uv -i https://pypi.tuna.tsinghua.edu.cn/simple
  }
  export PATH="$HOME/.local/bin:$PATH"
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

echo "===== 3/6 建立 git 裸仓库（git push 部署入口）====="
$SUDO mkdir -p $REPO_DIR $APP_DIR
$SUDO chown -R $(whoami):$(whoami) $REPO_DIR $APP_DIR
[ ! -d "$REPO_DIR/objects" ] && git init --bare $REPO_DIR

echo "===== 4/6 安装 post-receive 部署钩子 ====="
cat > $REPO_DIR/hooks/post-receive <<'HOOK'
#!/bin/bash
# git push 后自动：检出最新代码 → 同步依赖 → 重建判题镜像（如 Dockerfile 变更）→ 重启服务
set -e
APP_DIR=/opt/interview
REPO_DIR=/opt/repos/interview.git
export PATH="$HOME/.local/bin:$PATH"

cd $APP_DIR
git --git-dir=$REPO_DIR --work-tree=$APP_DIR checkout -f main
git --git-dir=$REPO_DIR --work-tree=$APP_DIR clean -fd -e backend/data -e backend/.env -e backend/.venv

cd $APP_DIR/backend
uv sync
# 判题镜像：首次或 Dockerfile 变更时自动构建（runner.py 也有兜底构建）
if ! docker image inspect interview-judge:latest >/dev/null 2>&1; then
  docker build -t interview-judge:latest -f docker/Dockerfile.judge docker/
fi
sudo systemctl restart interview
echo ">>> 部署完成，服务已重启"
HOOK
chmod +x $REPO_DIR/hooks/post-receive

echo "===== 5/6 安装 systemd 服务 ====="
UV_BIN=$(command -v uv || echo "$HOME/.local/bin/uv")
$SUDO tee /etc/systemd/system/interview.service >/dev/null <<UNIT
[Unit]
Description=AI Interview Expert (FastAPI)
After=network.target docker.service

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$APP_DIR/backend
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$UV_BIN run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
$SUDO systemctl daemon-reload
$SUDO systemctl enable interview

echo "===== 6/6 完成 ====="
echo "后续步骤："
echo "  1. 上传环境配置：scp backend/.env tencent:$APP_DIR/backend/.env"
echo "  2. （可选）上传历史数据：scp backend/data/app.db tencent:$APP_DIR/backend/data/app.db"
echo "  3. 本地添加远程：git remote add prod tencent:$REPO_DIR"
echo "  4. 部署：git push prod main"
echo "  5. 腾讯云控制台安全组放行 8000 端口（或配置 Nginx+域名后放行 80/443）"
