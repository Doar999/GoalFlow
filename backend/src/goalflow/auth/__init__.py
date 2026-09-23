"""账号与会话（T03）。

对外只有 `goalflow.auth.service` 里的 `AuthService` 与它的数据类；其余模块是内部实现，
其他业务模块不要直接 import `models` 去读写账号表（01-contracts 第 8 节）。
"""
