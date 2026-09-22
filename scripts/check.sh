#!/usr/bin/env bash
# 提交前门禁：格式化、lint、类型检查、锁文件漂移、契约漂移、凭证粗筛。
#
# 代码骨架尚未创建时，对应检查自动跳过并打印说明，因此本脚本从仓库第一天起就可以跑通。
# 用法：bash scripts/check.sh

set -euo pipefail
cd "$(dirname "$0")/.."

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

# ---------- 后端 ----------
section "后端检查"
if [ -d backend ]; then
  if ! command -v uv >/dev/null 2>&1; then
    printf '\033[31m    未找到 uv，请先执行 bash scripts/install.sh\033[0m\n'
    FAILED=1
  else
    run uv lock --project backend --check
    run uv run --project backend ruff format --check backend
    run uv run --project backend ruff check backend
    # mypy 只在当前工作目录找配置，而这里的 cwd 是仓库根。不显式指向
    # backend/pyproject.toml 的话，[tool.mypy] 的 strict 会被静默忽略。
    run uv run --project backend mypy --config-file backend/pyproject.toml backend/src
  fi
else
  skip "backend/ 尚未创建"
fi

# ---------- 前端 ----------
section "前端检查"
if [ -d frontend ]; then
  if ! command -v pnpm >/dev/null 2>&1; then
    printf '\033[31m    未找到 pnpm，请先执行 bash scripts/install.sh\033[0m\n'
    FAILED=1
  else
    run pnpm --dir frontend install --frozen-lockfile --ignore-scripts
    run pnpm --dir frontend run lint
    run pnpm --dir frontend exec tsc --noEmit
    run pnpm --dir frontend exec prettier --check src
  fi
else
  skip "frontend/ 尚未创建"
fi

# ---------- 契约漂移 ----------
# openapi/goalflow.yaml 是 HTTP 契约的事实源，必须与 FastAPI 当前定义一致。
# 手工编辑它、或改了路由却忘记重新导出，都会在这里被拦下。
section "契约漂移检查"
if [ -d backend ] && [ -f openapi/goalflow.yaml ]; then
  TMP_SPEC="$(mktemp)"
  trap 'rm -f "$TMP_SPEC"' EXIT
  if uv run --project backend python -m goalflow.tools.export_openapi > "$TMP_SPEC"; then
    if diff -u openapi/goalflow.yaml "$TMP_SPEC"; then
      printf '    契约一致\n'
    else
      printf '\033[31m    openapi/goalflow.yaml 已过期，请执行 bash scripts/api-generate.sh\033[0m\n'
      FAILED=1
    fi
  else
    printf '\033[31m    导出 OpenAPI 失败\033[0m\n'
    FAILED=1
  fi
else
  skip "openapi/goalflow.yaml 或 backend/ 尚未创建"
fi

# ---------- 凭证粗筛 ----------
# 只做一层粗筛，拦住最常见的误提交；不能替代 code review。
section "凭证粗筛"
PATTERN='(sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})'
if git ls-files -z 2>/dev/null | xargs -0 -r grep -InE "$PATTERN" -- 2>/dev/null; then
  printf '\033[31m    发现疑似凭证，请移除后改用 .env\033[0m\n'
  FAILED=1
else
  printf '    未发现疑似凭证\n'
fi

# ---------- 汇总 ----------
section "结果"
if [ ${#SKIPPED[@]} -gt 0 ]; then
  printf '    已跳过 %d 项：\n' "${#SKIPPED[@]}"
  for item in "${SKIPPED[@]}"; do printf '      - %s\n' "$item"; done
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\033[31m检查未通过\033[0m\n'
  exit 1
fi
printf '\033[32m检查通过\033[0m\n'
