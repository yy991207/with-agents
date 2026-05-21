#!/usr/bin/env bash
# 环境检查脚本
# 校验 python 3.11+ node 18+ mongo 可达 config.yaml 关键字段
# 退出码 0 全部通过 1 至少一项不通过
set -u

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

PYTHON_BIN=${PYTHON_BIN:-python}

c_green=$(printf '\033[32m')
c_red=$(printf '\033[31m')
c_yellow=$(printf '\033[33m')
c_reset=$(printf '\033[0m')

ok()    { echo "${c_green}[OK ]${c_reset} $*"; }
fail()  { echo "${c_red}[NO ]${c_reset} $*"; FAIL=1; }
warn()  { echo "${c_yellow}[?? ]${c_reset} $*"; }

FAIL=0

# 1 python 3.11+
if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  py_ver=$("$PYTHON_BIN" -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>/dev/null || echo "")
  py_major_minor=$("$PYTHON_BIN" -c "import sys;print('%d%02d'%sys.version_info[:2])" 2>/dev/null || echo "0")
  if [ "$py_major_minor" -ge 311 ]; then
    ok "python $py_ver ($PYTHON_BIN)"
  else
    fail "python $py_ver 版本过低 需要 3.11+ (PYTHON_BIN=$PYTHON_BIN)"
  fi
else
  fail "找不到 $PYTHON_BIN  conda activate multi-chat 后再跑"
fi

# 2 node 18+
if command -v node >/dev/null 2>&1; then
  node_ver=$(node -v 2>/dev/null | sed 's/^v//')
  node_major=$(echo "$node_ver" | cut -d. -f1)
  if [ "${node_major:-0}" -ge 18 ]; then
    ok "node $node_ver"
  else
    fail "node $node_ver 版本过低 需要 18+"
  fi
else
  fail "找不到 node 命令"
fi

# 3 mongo 可达 27017
if "$PYTHON_BIN" - <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(1.0)
try:
    s.connect(("127.0.0.1", 27017))
except Exception:
    sys.exit(1)
PY
then
  ok "MongoDB 127.0.0.1:27017 可连接"
else
  fail "MongoDB 127.0.0.1:27017 不可达 试试 ./run.sh mongo"
fi

# 4 config.yaml 存在并含关键字段
CONFIG="$PROJECT_ROOT/config.yaml"
if [ ! -f "$CONFIG" ]; then
  fail "config.yaml 不存在 cp config.example.yaml config.yaml 后填 key"
else
  ok "config.yaml 存在"

  # 关键字段检查 用 python yaml 解析
  if "$PYTHON_BIN" - "$CONFIG" <<'PY' 2>/dev/null
import sys
try:
    import yaml
except ImportError:
    print("[skip] PyYAML 未安装 跳过字段校验", file=sys.stderr)
    sys.exit(0)
with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f) or {}
missing = []
for k in ("key", "base_url", "agents", "judge", "mongo"):
    if k not in cfg:
        missing.append(k)
if cfg.get("key") in (None, "", "REPLACE_WITH_YOUR_API_KEY"):
    missing.append("key (未填真实值)")
agents = cfg.get("agents") or {}
expected = {"DeepSeek", "GLM", "Kimi", "Qwen"}
if not expected.issubset(set(agents.keys())):
    missing.append(f"agents 段缺少 {expected - set(agents.keys())}")
judge = cfg.get("judge") or {}
if judge.get("agent") not in agents:
    missing.append(f"judge.agent={judge.get('agent')} 不在 agents 列表中")
if missing:
    print("MISSING:" + "|".join(missing))
    sys.exit(2)
PY
  then
    ok "config.yaml 关键字段齐全"
  else
    fail "config.yaml 关键字段校验未通过 见上方提示"
  fi
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  ok "全部检查通过"
  exit 0
else
  fail "存在不通过项 修复后再启动"
  exit 1
fi
