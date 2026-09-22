#!/usr/bin/env bash
# 运行测试。
#
# 用法：
#   bash scripts/test.sh            全部
#   bash scripts/test.sh unit       仅模块行为测试
#   bash scripts/test.sh e2e        仅跨组件验收场景
#   bash scripts/test.sh backend    仅后端
#   bash scripts/test.sh frontend   仅前端
#
# 注意：数据库相关验收必须在真实 seekdb 上执行，SQLite 通过不算通过。
# 见 docs/engineering/03-code-and-test-standards.md。

set -euo pipefail
cd "$(dirname "$0")/.."

SCOPE="${1:-all}"
FAILED=0
SKIPPED=()

section() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
skip() { printf '    跳过：%s\n' "$1"; SKIPPED+=("$1"); }
run() {
  printf '    $ %s\n' "$*"
  if ! "$@"; then
    printf '\033[31m    失败：%s\033[0m\n' "$*"
    FAILED=1
  fi
}

want_backend() { [ "$SCOPE" = all ] || [ "$SCOPE" = unit ] || [ "$SCOPE" = e2e ] || [ "$SCOPE" = backend ]; }
want_frontend() { [ "$SCOPE" = all ] || [ "$SCOPE" = unit ] || [ "$SCOPE" = frontend ]; }

if want_backend; then
  section "后端测试（$SCOPE）"
  if [ -d backend ]; then
    case "$SCOPE" in
      unit) run uv run --project backend pytest backend/tests --ignore=backend/tests/e2e ;;
      e2e)  run uv run --project backend pytest backend/tests/e2e ;;
      *)    run uv run --project backend pytest backend/tests ;;
    esac
  else
    skip "backend/ 尚未创建"
  fi
fi

if want_frontend; then
  section "前端测试（$SCOPE）"
  if [ -d frontend ]; then
    run pnpm --dir frontend exec vitest run
  else
    skip "frontend/ 尚未创建"
  fi
fi

section "结果"
if [ ${#SKIPPED[@]} -gt 0 ]; then
  printf '    已跳过 %d 项：\n' "${#SKIPPED[@]}"
  for item in "${SKIPPED[@]}"; do printf '      - %s\n' "$item"; done
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\033[31m测试未通过\033[0m\n'
  exit 1
fi
printf '\033[32m测试通过\033[0m\n'
