"""把 FastAPI 的 OpenAPI 文档导出为 YAML，写到标准输出。

由 scripts/api-generate.sh 与 scripts/check.sh 的契约漂移检查调用。

**必须写 sys.stdout.buffer。** Windows 上文本模式的 stdout 会把 `\n` 转成 `\r\n`，
而仓库 .gitattributes 强制 LF，结果是漂移检查在 Windows 开发机上永远失败、
在 CI 上却通过。见 T02 交接卡决策 A7。
"""

import sys

import yaml

from goalflow.api.app import create_app


def render_openapi_yaml() -> str:
    """生成 YAML 文本。同一份代码必须产出逐字节一致的结果。"""
    document = create_app().openapi()
    return yaml.safe_dump(
        document,
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
        width=100,
    )


def main() -> None:
    sys.stdout.buffer.write(render_openapi_yaml().encode("utf-8"))


if __name__ == "__main__":
    main()
