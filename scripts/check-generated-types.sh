#!/usr/bin/env bash
# 前端生成类型必须与 openapi/goalflow.yaml 逐字一致。由 pre-commit 钩子调用。
#
# 按当前契约重新生成一份，与工作区里的 schema.d.ts 比对：
#   - 手工编辑了生成物 → 不一致，拦下；
#   - 改了契约却没重新生成 → 不一致，拦下；
#   - 由 scripts/api-generate.sh 正常生成 → 一致，放行。
# 生成命令必须与 scripts/api-generate.sh 保持一致，否则这里会误报。
#
# 临时文件放在 frontend/node_modules/ 下而不是 /tmp：Windows 上的 node 不认 Git Bash 的 /tmp 路径。

set -euo pipefail
cd "$(dirname "$0")/.."

SPEC="openapi/goalflow.yaml"
TYPES="frontend/src/shared/api/generated/schema.d.ts"
FRESH_REL="node_modules/.goalflow-schema-check.d.ts"
FRESH="frontend/$FRESH_REL"

if [ ! -d frontend/node_modules ]; then
  echo "错误：frontend/node_modules 不存在，无法校验生成物。请先执行 bash scripts/install.sh"
  exit 1
fi

trap 'rm -f "$FRESH"' EXIT
pnpm --dir frontend exec openapi-typescript "../$SPEC" -o "$FRESH_REL" >/dev/null

if ! diff -q "$FRESH" "$TYPES" >/dev/null; then
  echo "错误：$TYPES 与 $SPEC 生成的结果不一致。"
  echo "不要手工编辑生成物；改后端定义后执行 bash scripts/api-generate.sh，并把两个文件一起提交。"
  echo "见 docs/engineering/01-contracts-and-ownership.md 第 2 节。"
  exit 1
fi
