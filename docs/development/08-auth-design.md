# 首版注册、登录与会话设计 v0.3

状态：已确认。产品方向见 PRD 决策 D07；安全参数、接口与表结构由 [T03 交接卡](../worklog/T03-auth-session.md) 第 4 节确认，本文为其最终结论。目标是让个人用户在开源自部署或托管实例中无需邀请即可注册登录，并在无邮件服务时完整运行核心功能。

## 方案

首版使用账号标识＋密码自由注册登录和服务端会话 Cookie，不设置邀请码或管理员审批，不依赖短信、邮件或第三方 OAuth。默认部署配置开启注册；部署者可通过 `GOALFLOW_ALLOW_REGISTRATION=false` 关闭新注册，这只是实例运维开关，不改变产品的注册模型。

账号标识允许用户名或邮箱格式的字符串，首版不依赖邮箱验证，界面不得将未验证的邮箱当作可靠联系方式。标识依次经过 NFKC 规范化、去除首尾空白、casefold 后保存并建唯一约束；规范化后长度 3–254，不得包含空白或控制字符（含零宽字符）。后续可增加邮件验证、OAuth 或 Passkey，不改变用户和会话的核心归属模型。

## 用户流程

1. 首次部署：完成数据库迁移后即可打开注册页。管理员只能由部署者用本地命令 `python -m goalflow.auth.cli promote <账号标识>` 授予，不因"首位注册"隐式授予。
2. 注册：用户设置账号标识与密码；服务端规范化标识、检查唯一性、创建凭证，并在成功后建立会话。
3. 登录：验证密码后创建随机的不透明会话令牌；浏览器只持有 Cookie，数据库保存令牌摘要和会话状态。已登录时再次登录会撤销旧会话。
4. 退出：撤销当前会话；"退出全部设备"撤销该用户的全部会话，包括当前会话。
5. 修改密码：撤销全部旧会话，并为当前设备换发新会话。
6. 忘记密码：无邮件配置时由部署者用本地命令 `issue-reset-token` 签发一次性、30 分钟有效的重置令牌，签发新令牌时旧的未用令牌即刻作废。凭令牌重置成功后该用户全部会话失效，不自动登录。界面不得伪装成已发送邮件。
7. 禁用账号：部署者用本地命令 `disable`，同时撤销该账号全部会话；禁用账号无法登录，也无法凭令牌重置。

## 安全与隐私规则

- 密码：长度 12–128 个字符，不设字符组成规则，不强制定期更换，不得与账号标识相同。以 Argon2id（m=19456 KiB、t=2、p=1）哈希；参数变化后，用户下次登录成功时按新参数重新哈希。不加密保存密码，也不记录密码或令牌原文。
- 哈希计算与校验一律在数据库写事务之外进行：SQLite 写锁是库级的，几十毫秒的哈希放进写事务会让全站写操作排队。
- 会话令牌与重置令牌均为 256 位随机数，库里只存 `HMAC-SHA256(GOALFLOW_SESSION_SECRET, 令牌)`。轮换该密钥会让全部会话立即失效。
- 会话空闲 7 天失效、绝对 30 天失效。`last_seen_at` 距上次写入超过 5 分钟才再写，避免每个请求都变成写事务；空闲失效的实际精度因此为 5 分钟。
- Cookie：`HttpOnly; SameSite=Lax; Path=/`。生产环境名为 `__Host-goalflow_session` 并带 `Secure`，不设 Domain；本地与测试环境名为 `goalflow_session`、不带 `Secure`。不把长期 JWT 放入 localStorage。
- CSRF：所有非 GET/HEAD/OPTIONS 请求校验来源——有 `Origin` 时必须与 `GOALFLOW_PUBLIC_ORIGIN` 逐字相等；没有 `Origin` 时要求 `Sec-Fetch-Site: same-origin`；两者都没有则拒绝。再叠加 SameSite=Lax。生产环境必须配置 https 的 `GOALFLOW_PUBLIC_ORIGIN`，否则拒绝启动。
- 限流（进程内固定窗口，部署层 Nginx `limit_req` 兜底）：登录每账号标识 10 次/15 分钟、每 IP 网段 30 次/15 分钟；注册每 IP 网段 5 次/小时；凭令牌重置每 IP 网段 10 次/15 分钟；修改密码每用户 10 次/15 分钟。不做账号锁定。超限返回 `RATE_LIMITED` 与 `Retry-After`。
- 登录时账号不存在、密码错误、账号已禁用返回同一错误 `INVALID_CREDENTIALS`，且都做一次同等代价的哈希校验，响应时间不泄露账号是否存在。注册查重不可避免地暴露标识已被占用，由限流缓解。
- 账号事件写入结构化日志（不含令牌、密码、账号标识与完整 IP）。审计表 `audit_events` 的归属另行确定。
- 管理员默认只管理账号状态和运行指标，不在普通管理页面浏览用户目标、对话、模型密钥或附件。服务器进程运行时仍需要解密用户模型密钥，部署文档必须说明这一信任关系。
- 多用户资源读取与写入始终检查 owner_id；管理员身份不自动绕过业务数据隔离。紧急运维访问若未来加入，需要单独授权和审计。

## 数据模型

见 [核心数据模型](03-data-model.md) 第 2 节的 users、password_credentials、sessions、password_reset_tokens，由迁移 0001 建立。会话表只存 IP 的 /24（IPv4）或 /48（IPv6）网段与截断到 200 字符的 User-Agent。

原始重置令牌和会话令牌只在创建时出现一次：会话令牌只经 Cookie 下发，重置令牌只打印在部署者的本地命令输出里。日志、异常、数据库和前端读接口只能出现摘要或脱敏状态。

## 接口

| 方法与路径 | 说明 |
| --- | --- |
| GET /api/auth/registration | 注册是否开放，登录与注册页据此展示入口 |
| POST /api/auth/register | 创建账号并建立会话，201；注册关闭时 `REGISTRATION_CLOSED`，标识被占用时 `ACCOUNT_IDENTIFIER_UNAVAILABLE` |
| POST /api/auth/login | 建立会话 |
| POST /api/auth/logout | 撤销当前会话，204；未登录时同样 204 |
| POST /api/auth/logout-all | 撤销该用户全部会话，204 |
| GET /api/auth/session | 当前用户与会话元数据，不返回令牌 |
| POST /api/auth/password/change | 修改密码并换发会话 |
| POST /api/auth/password/reset-with-token | 凭一次性令牌设置新密码，204 |

这组接口不要求 `Idempotency-Key`，是 [01-contracts 第 5 节](../engineering/01-contracts-and-ownership.md) 写明的唯一例外。首版不提供 `/api/admin/*` HTTP 接口，管理操作只有本地命令 `promote`、`disable`、`issue-reset-token`。所有改变状态的接口使用统一错误结构；认证失败使用通用错误。

## 验收场景

- 同一账号标识并发注册，最多一个账号成功；重复提交不会产生多个账号或会话。
- 登录成功前后会话标识不同；退出、禁用、重置密码及退出所有设备后旧 Cookie 失效。
- 修改用户 ID 或会话 ID 不能操作其他用户资源；普通用户无法获得管理能力。
- 数据库、日志和 API 响应中不出现密码、重置令牌、会话令牌或模型 Key 明文。
- 无 SMTP、短信及第三方登录配置时，注册、登录、修改密码和部署者辅助重置仍可完成。
- 默认配置可以直接注册；显式关闭注册后，现有用户仍可登录，注册接口和页面给出一致状态。
- HTTP 开发环境与 HTTPS 生产环境的 Cookie 配置明确区分；生产模式拒绝不安全默认值。

参考：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[OWASP CSRF 防护](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。
