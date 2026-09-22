#!/usr/bin/env bash
# 从 FastAPI 导出 OpenAPI，并生成前端 TypeScript 类型。
#
# 产物均为生成物，禁止手工编辑：
#   openapi/goalflow.yaml
#   frontend/src/shared/api/generated/schema.d.ts
#
# 改了路由或 Pydantic 模型后执行本脚本；忘记执行会被 scripts/check.sh 的契约漂移检查拦下。
# 用法：bash scripts/api-generate.sh

set -euo pipefail
cd "$(dirname "$0")/.."

SPEC=openapi/goalflow.yaml
TYPES=frontend/src/shared/api/generated/schema.d.ts

section() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

section "导出 OpenAPI"
if [ ! -d backend ]; then
  printf '\033[31m    backend/ 尚未创建，无法导出\033[0m\n'
  exit 1
fi
mkdir -p "$(dirname "$SPEC")"
uv run --project backend python -m goalflow.tools.export_openapi > "$SPEC"
printf '    已写入 %s\n' "$SPEC"

section "生成前端类型"
if [ ! -d frontend ]; then
  printf '    跳过：frontend/ 尚未创建\n'
  printf '\n\033[32m完成\033[0m\n'
  exit 0
fi
mkdir -p "$(dirname "$TYPES")"
pnpm --dir frontend exec openapi-typescript "../$SPEC" -o "../$TYPES"
printf '    已写入 %s\n' "$TYPES"

section "提醒"
cat <<'EOF'
    契约变更必须单独成 PR，不要与业务实现混在一起。
    见 docs/engineering/01-contracts-and-ownership.md 第 3 节。
EOF

printf '\n\033[32m完成\033[0m\n'
