"""公共契约：错误码、统一错误结构、跨模块共享的请求与响应形状。

本包**只被依赖，不依赖任何业务模块**（见 docs/engineering/01-contracts-and-ownership.md
第 2 节）。这条方向约束由 tests/contracts/test_package_isolation.py 守着。

共享业务枚举放在本包的 `enums.py`，不要把枚举散落到各业务模块里。
"""
