#!/usr/bin/env bash
# multi-chat 一键启动脚本
# 子命令 mongo / dev / build / start / stop / restart / log / status / up
# 启动前会自动 conda activate multi-chat 与自动起本地 mongo
set -u

# 切到脚本所在目录 保证相对路径稳定 也允许从任意位置调用
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# 目标 conda 环境名 与 README 一致
CONDA_ENV=${CONDA_ENV:-multi-chat}

# 颜色辅助 仅控制台 不写文件 提前定义供 ensure_conda_env 使用
c_green=$(printf '\033[32m')
c_yellow=$(printf '\033[33m')
c_red=$(printf '\033[31m')
c_reset=$(printf '\033[0m')

info()  { echo "${c_green}[info]${c_reset} $*"; }
warn()  { echo "${c_yellow}[warn]${c_reset} $*"; }
err()   { echo "${c_red}[err]${c_reset} $*" >&2; }

# 自动激活 conda 环境 已在目标 env 时跳过
ensure_conda_env() {
  if [ "${CONDA_DEFAULT_ENV:-}" = "$CONDA_ENV" ]; then
    return 0
  fi
  # 在常见路径里找 conda init 脚本
  local conda_sh=""
  for p in \
    "$HOME/miniconda3/etc/profile.d/conda.sh" \
    "$HOME/anaconda3/etc/profile.d/conda.sh" \
    "/opt/miniconda3/etc/profile.d/conda.sh" \
    "/opt/anaconda3/etc/profile.d/conda.sh" \
    "/usr/local/miniconda3/etc/profile.d/conda.sh"; do
    if [ -f "$p" ]; then conda_sh="$p"; break; fi
  done
  if [ -z "$conda_sh" ] && command -v conda >/dev/null 2>&1; then
    # 退路 用 conda info 定位
    local base
    base=$(conda info --base 2>/dev/null || true)
    if [ -n "$base" ] && [ -f "$base/etc/profile.d/conda.sh" ]; then
      conda_sh="$base/etc/profile.d/conda.sh"
    fi
  fi
  if [ -z "$conda_sh" ]; then
    warn "找不到 conda init 脚本 跳过自动激活 依赖 PYTHON_BIN 指向正确的 python"
    return 0
  fi
  info "激活 conda 环境 $CONDA_ENV"
  # shellcheck disable=SC1090
  source "$conda_sh"
  conda activate "$CONDA_ENV" || {
    err "conda activate $CONDA_ENV 失败 请确保该环境已创建 (conda create -n $CONDA_ENV python=3.11)"
    return 1
  }
}
ensure_conda_env || exit 1

# 可被环境变量覆盖的 python 二进制
# 优先尝试目标 conda env 的 python 找不到再退到 PATH 上的 python
DEFAULT_PYTHON="$HOME/miniconda3/envs/$CONDA_ENV/bin/python"
[ -x "$DEFAULT_PYTHON" ] || DEFAULT_PYTHON="python"
PYTHON_BIN=${PYTHON_BIN:-$DEFAULT_PYTHON}

# 后端 uvicorn 进程信息
PID_FILE=".run.pid"
LOG_FILE="run.log"
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8002}
APP_FACTORY="multichat.main:create_app"

# 探测端口是否被占用 返回占用 PID 或空
port_pid() {
  local port=$1
  lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -n1 || true
}

# 探测 mongo 是否可达 用 python 简单 connect 一下
ping_mongo() {
  "$PYTHON_BIN" - <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(1.0)
try:
    s.connect(("127.0.0.1", 27017))
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

# 启动前的必要前置 校验 mongo 校验端口 mongo 不可达时尝试自动起
preflight() {
  if ! ping_mongo; then
    warn "MongoDB (127.0.0.1:27017) 不可达 尝试自动用 docker compose 起"
    if ! command -v docker >/dev/null 2>&1; then
      err "找不到 docker 命令 请手动起 mongo 或装 Docker Desktop"
      return 1
    fi
    if ! docker info >/dev/null 2>&1; then
      err "Docker daemon 未运行 请先启动 Docker Desktop"
      return 1
    fi
    cmd_mongo
    # 等 mongo 就绪 最多 15 秒
    local i=0
    while [ "$i" -lt 15 ] && ! ping_mongo; do
      sleep 1
      i=$((i + 1))
    done
    if ! ping_mongo; then
      err "等了 15 秒 mongo 仍不可达 请手动检查 docker compose logs mongo"
      return 1
    fi
    info "mongo 已就绪"
  fi

  local existing
  existing=$(port_pid "$PORT")
  if [ -n "$existing" ]; then
    err "端口 $PORT 已被进程 PID=$existing 占用 可能是孤儿后端进程"
    err "确认后用 kill $existing 清理 或改 PORT 环境变量启动"
    return 1
  fi
  return 0
}

cmd_mongo() {
  info "用 docker compose 起 mongo (映射 27017)"
  docker compose up -d mongo
}

cmd_mongo_stop() {
  info "停 docker compose 中的 mongo"
  docker compose stop mongo
}

cmd_dev() {
  # 双进程开发模式 vite + uvicorn --reload
  # 前台运行 Ctrl+C 同时杀俩
  preflight || return 1

  info "启动 vite dev (web/) 与 uvicorn --reload (backend/) 双进程"
  info "前端 http://localhost:5173  后端 http://localhost:$PORT"
  info "Ctrl+C 退出"

  # 起 vite 后台
  ( cd web && npm run dev ) &
  local vite_pid=$!

  # 起 uvicorn 前台 退出时主动收尸 vite
  trap 'info "退出 dev 模式 杀 vite PID=$vite_pid"; kill "$vite_pid" 2>/dev/null || true' INT TERM EXIT

  ( cd backend && "$PYTHON_BIN" -m uvicorn "$APP_FACTORY" --factory --reload --host "$HOST" --port "$PORT" )

  # 退出后清掉 trap 让 trap 里的 cleanup 执行
  trap - INT TERM EXIT
  kill "$vite_pid" 2>/dev/null || true
  wait "$vite_pid" 2>/dev/null || true
}

cmd_build() {
  info "构建前端产物 web/dist"
  ( cd web && npm install && npm run build )
}

cmd_start() {
  preflight || return 1

  if [ ! -d "web/dist" ]; then
    warn "web/dist 不存在 生产模式需要先 ./run.sh build"
    return 1
  fi

  info "后台启动 uvicorn (生产模式 单端口) host=$HOST port=$PORT"
  # 不用 subshell 避免 $! 写不进 PID_FILE  cd 后显式切回
  cd backend
  nohup "$PYTHON_BIN" -m uvicorn "$APP_FACTORY" --factory --host "$HOST" --port "$PORT" >> "../$LOG_FILE" 2>&1 &
  local pid=$!
  cd "$SCRIPT_DIR"
  echo "$pid" > "$PID_FILE"

  sleep 1.5
  if ! kill -0 "$pid" 2>/dev/null; then
    err "uvicorn 启动失败 看 $LOG_FILE"
    rm -f "$PID_FILE"
    return 1
  fi
  info "已启动 PID=$pid 日志 $LOG_FILE"
}

cmd_stop() {
  if [ ! -f "$PID_FILE" ]; then
    warn "没有 $PID_FILE 服务可能未通过 run.sh 启动"
    # 尝试根据端口找孤儿
    local existing
    existing=$(port_pid "$PORT")
    if [ -n "$existing" ]; then
      warn "端口 $PORT 上发现进程 PID=$existing 可能是孤儿 手动 kill 处理"
    fi
    return 0
  fi

  local pid
  pid=$(cat "$PID_FILE")
  if ! kill -0 "$pid" 2>/dev/null; then
    warn "PID=$pid 已不存在 清理 $PID_FILE"
    rm -f "$PID_FILE"
    return 0
  fi

  info "优雅停止 PID=$pid"
  kill "$pid" 2>/dev/null || true

  # 5 秒优雅退出窗口 不行就 -9
  local i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 5 ]; do
    sleep 1
    i=$((i + 1))
  done

  if kill -0 "$pid" 2>/dev/null; then
    warn "5 秒未退出 强杀 PID=$pid"
    kill -9 "$pid" 2>/dev/null || true
  fi

  rm -f "$PID_FILE"
  info "已停止"
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_log() {
  if [ ! -f "$LOG_FILE" ]; then
    warn "$LOG_FILE 不存在"
    return 1
  fi
  info "tail -f $LOG_FILE  Ctrl+C 退出不影响服务"
  tail -f "$LOG_FILE"
}

cmd_status() {
  local pid=""
  if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE")
  fi

  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    info "uvicorn 运行中 PID=$pid 端口 $PORT"
  else
    warn "uvicorn 未运行 (无 $PID_FILE 或进程已退出)"
  fi

  local port_owner
  port_owner=$(port_pid "$PORT")
  if [ -n "$port_owner" ]; then
    info "端口 $PORT 被 PID=$port_owner 占用"
  else
    info "端口 $PORT 空闲"
  fi
}

# 一键全自动启动
# 顺序: 自动 build (web/dist 不存在时) -> mongo 自动起 -> uvicorn 后台
# 启动后给一个访问 URL 提示
cmd_up() {
  if [ ! -d "web/dist" ]; then
    info "未发现 web/dist 自动 build 前端"
    cmd_build || return 1
  fi
  cmd_start || return 1
  echo
  info "✓ 服务就绪 浏览器访问 http://localhost:$PORT"
  info "  日志 ./run.sh log    停止 ./run.sh stop    状态 ./run.sh status"
}

usage() {
  cat <<EOF
用法 ./run.sh <command>

子命令(✨ 推荐用 up 一键全自动)
  up          ✨ 全自动 conda activate + mongo 自动起 + 缺失则 build + 启动
  dev         双进程开发 vite (5173) + uvicorn --reload (8002) 前台 Ctrl+C 同停
  start       生产模式后台起 uvicorn (要求 web/dist 已存在 否则报错)
  stop        优雅停止 5 秒不退则 kill -9
  restart     stop 后再 start
  log         tail -f run.log
  status      显示 PID 与端口
  build       构建前端 web/dist
  mongo       手动用 docker compose 起本地 mongo (up/dev/start 已自动起)
  mongo-stop  停 docker compose 中的 mongo

环境变量
  CONDA_ENV   默认 multi-chat  自动 activate 该 conda 环境
  PYTHON_BIN  默认指向 \$HOME/miniconda3/envs/\$CONDA_ENV/bin/python
  HOST        默认 0.0.0.0
  PORT        默认 8002

最小启动 (任意目录任意 shell):
  /Users/yang/multi-chat/run.sh up
EOF
}

case "${1:-}" in
  up)          cmd_up ;;
  mongo)       cmd_mongo ;;
  mongo-stop)  cmd_mongo_stop ;;
  dev)         cmd_dev ;;
  build)       cmd_build ;;
  start)       cmd_start ;;
  stop)        cmd_stop ;;
  restart)     cmd_restart ;;
  log)         cmd_log ;;
  status)      cmd_status ;;
  ""|-h|--help|help) usage ;;
  *)
    err "未知命令 $1"
    usage
    exit 2
    ;;
esac
