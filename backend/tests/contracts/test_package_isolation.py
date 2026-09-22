"""contracts 包只被依赖，不依赖业务模块。

见 docs/engineering/01-contracts-and-ownership.md 第 2 节。这条方向一旦被破坏，
"共享契约"就变成了另一个业务模块，所有人都会被迫跟着它的变更走。
"""

import ast
from pathlib import Path

import goalflow.contracts

_CONTRACTS_DIR = Path(goalflow.contracts.__file__).parent


def _imported_goalflow_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module)
    return {name for name in imported if name == "goalflow" or name.startswith("goalflow.")}


def test_contracts_package_has_modules():
    # 防止本文件在 contracts 被挪走后静默通过。
    assert list(_CONTRACTS_DIR.glob("*.py"))


def test_contracts_imports_nothing_outside_contracts():
    offenders: dict[str, set[str]] = {}
    for module_path in _CONTRACTS_DIR.glob("*.py"):
        imported = _imported_goalflow_modules(module_path.read_text(encoding="utf-8"))
        outside = {name for name in imported if not name.startswith("goalflow.contracts")}
        if outside:
            offenders[module_path.name] = outside

    assert offenders == {}, f"contracts 不得依赖业务模块：{offenders}"
