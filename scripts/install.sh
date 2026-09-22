#!/usr/bin/env bash
# 安装开发依赖与 Git 钩子。
#
# 前置：Git、Python 3.11+、uv、Node.js 20+、pnpm。
# 用法：bash scripts/install.sh

set -euo pipefail
cd "$(dirname "$0")/.."

section() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

section "检查前置工具"
MISSING=0
for tool in git uv; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '    %-6s %s\n' "$tool" "$(command -v "$tool")"
  else
    printf '\033[31m    缺少 %s\033[0m\n' "$tool"
    MISSING=1
  fi
done
if ! command -v pnpm >/dev/null 2>&1; then
  printf '\033[33m    缺少 pnpm（前端开发需要，可稍后安装：npm i -g pnpm）\033[0m\n'
fi
if [ "$MISSING" -ne 0 ]; then
  printf '\n安装指引：uv https://docs.astral.sh/uv/getting-started/installation/\n'
  exit 1
fi

section "后端依赖"
if [ -d backend ]; then
  uv sync --project backend
else
  printf '    跳过：backend/ 尚未创建\n'
fi

section "前端依赖"
if [ -d frontend ] && command -v pnpm >/dev/null 2>&1; then
  pnpm --dir frontend install
else
  printf '    跳过：frontend/ 尚未创建或缺少 pnpm\n'
fi

section "Git 钩子"
if [ -f .pre-commit-config.yaml ]; then
  if command -v pre-commit >/dev/null 2>&1; then
    pre-commit install
  else
    uv tool run pre-commit install
  fi
else
  printf '    跳过：无 .pre-commit-config.yaml\n'
fi

section "本地配置"
if [ -f .env ]; then
  printf '    .env 已存在，未覆盖\n'
elif [ -f .env.example ]; then
  cp .env.example .env
  printf '    已从 .env.example 生成 .env，请填入本地数据库与 Redis 地址\n'
  printf '\033[33m    .env 不会被提交，也不要把真实凭证写进 .env.example\033[0m\n'
else
  printf '    跳过：无 .env.example\n'
fi

printf '\n\033[32m安装完成。下一步：bash scripts/check.sh\033[0m\n'
