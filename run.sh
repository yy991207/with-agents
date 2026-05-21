#!/usr/bin/env bash
# multi-chat 一键启动脚本
# 子命令 mongo / dev / build / start / stop / restart / log / status
set -u

# 切到脚本所在目录 保证相对路径稳定
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# 可被环境变量覆盖的 python 二进制 默认假设 PATH 里有 python
# 复用 conda 时不要硬编码 conda 路径 先 conda activate multi-chat 再跑
PYTHON_BIN=${PYTHON_BIN:-python}

# 后端 uvicorn 进程信息
PID_FILE=".run.pid"
LOG_FILE="run.log"
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8002}
APP_FACTORY="multichat.main:create_app"

# 颜色辅助 仅控制台 不写文件
c_green=$(printf '\033[32m')
c_yellow=$(printf '\033[33m')
c_red=$(printf '\033[31m')
c_reset=$(printf '\033[0m')

info()  { echo "${c_green}[info]${c_reset} $*"; }
warn()  { echo "${c_yellow}[warn]${c_reset} $*"; }
err()   { echo "${c_red}[err]${c_reset} $*" >&2; }

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

# 启动前的必要前置 校验 mongo 校验端口
preflight() {
  if ! ping_mongo; then
    err "MongoDB (127.0.0.1:27017) 不可达 先执行 ./run.sh mongo 起本地 mongo"
    return 1
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
  ( cd backend && nohup "$PYTHON_BIN" -m uvicorn "$APP_FACTORY" --factory --host "$HOST" --port "$PORT" >> "../$LOG_FILE" 2>&1 & echo $! > "../$PID_FILE" )

  sleep 1.5
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
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

usage() {
  cat <<EOF
用法 ./run.sh <command>

子命令
  mongo       用 docker compose 起本地 MongoDB (映射 27017)
  mongo-stop  停 docker compose 中的 mongo
  dev         双进程开发 vite (5173) + uvicorn --reload (8002) 前台
  build       构建前端 web/dist
  start       生产模式后台起 uvicorn (需先 build) 写 .run.pid
  stop        优雅停止 5 秒不退则 kill -9
  restart     stop 后再 start
  log         tail -f run.log
  status      显示 PID 与端口

环境变量
  PYTHON_BIN  默认 python  conda activate multi-chat 后即生效
  HOST        默认 0.0.0.0
  PORT        默认 8002
EOF
}

case "${1:-}" in
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
