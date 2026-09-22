# 首版注册、登录与会话设计 v0.2

状态：产品方向已确认，具体安全参数待实现验证。目标是让个人用户在开源自部署或托管实例中无需邀请即可注册登录，并在无邮件服务时完整运行核心功能。

## 推荐方案

首版使用账号标识＋密码自由注册登录和服务端会话 Cookie，不设置邀请码或管理员审批，不依赖短信、邮件或第三方 OAuth。默认部署配置开启注册；部署者可通过显式配置关闭新注册，但这只是实例运维开关，不改变产品的注册模型。

账号标识建议允许用户名或邮箱格式的字符串，但首版不依赖邮箱验证。界面不得将未验证的邮箱当作可靠联系方式。后续可增加邮件验证、OAuth 或 Passkey，不改变用户和会话的核心归属模型。

## 用户流程

1. 首次部署：完成数据库迁移后即可打开注册页；管理能力如需启用，由部署者通过本地命令把指定账号提升为管理员，不能因为“首位注册”而隐式授予管理员权限。
2. 注册：用户设置账号标识与密码；服务端规范化标识、检查唯一性、创建凭证，并在成功后建立会话。
3. 登录：验证密码后创建随机的不透明会话令牌；浏览器只持有 Cookie，数据库保存令牌摘要和会话状态。
4. 退出：撤销当前会话；用户可以撤销其他设备会话。禁用用户或修改、重置密码时撤销现有会话。
5. 忘记密码：无邮件配置时由部署者本地命令签发一次性、短时有效的重置凭证；界面不得伪装成已发送邮件。后续可选接入邮件找回。

## 安全与隐私规则

- 密码使用 Argon2id 等现代自适应算法哈希，参数由配置和基准测试确定；不加密保存密码，也不记录密码或会话原文。
- 生产 Cookie 使用 Secure、HttpOnly、Path=/ 和合适的 SameSite；建议同域部署并采用 SameSite=Lax。写请求校验 Origin，并使用 CSRF 令牌或等效保护。
- 会话使用足够随机的不透明令牌，登录后创建新会话；设置空闲和绝对有效期，具体时长待定。服务端可立即撤销，不把长期 JWT 放入 localStorage。
- 注册、登录、密码重置和连接测试分别限流。注册接口同时限制 IP/网络段和账号标识，允许部署者启用验证码或关闭注册；错误提示避免泄露账号是否存在，操作写入不含敏感值的审计记录。
- 管理员默认只管理账号状态和运行指标，不在普通管理页面浏览用户目标、对话、模型密钥或附件。服务器进程运行时仍需要解密用户模型密钥，部署文档必须说明这一信任关系。
- 多用户资源读取与写入始终检查 owner_id；管理员身份不自动绕过业务数据隔离。紧急运维访问若未来加入，需要单独授权和审计。

## 数据模型调整

- users：id、account_identifier、role、status、timezone、created_at；account_identifier 采用规范化值并唯一。
- password_credentials：user_id、password_hash、hash_scheme、changed_at；与其他用户资料分开。
- sessions：id、user_id、token_hash、created_at、last_seen_at、expires_at、revoked_at、user_agent_summary、ip_prefix 可选。
- password_reset_tokens：id、user_id、token_hash、issued_by、expires_at、consumed_at。

原始重置令牌和会话令牌只在创建时返回一次。日志、异常、数据库导出样例和前端读接口只能出现摘要或脱敏状态。

## 接口建议

- POST /api/auth/login、POST /api/auth/logout、POST /api/auth/logout-all。
- GET /api/auth/session：返回当前用户和会话元数据，不返回会话令牌。
- POST /api/auth/register：创建账号并建立会话；注册关闭时返回明确的实例配置状态。
- POST /api/auth/password/change、POST /api/auth/password/reset-with-token。
- POST /api/admin/users/{id}/disable、POST /api/admin/users/{id}/password-reset-token。

所有改变状态的接口使用统一 CSRF、审计和错误结构。认证失败使用通用错误；管理员接口另检查 role，不仅依赖前端隐藏。

## 验收场景

- 同一账号标识并发注册，最多一个账号成功；重复提交不会产生多个账号或会话。
- 登录成功前后会话标识不同；退出、禁用、重置密码及退出所有设备后旧 Cookie 失效。
- 修改用户 ID 或会话 ID 不能操作其他用户资源；普通用户不能调用管理员接口。
- 数据库、日志和 API 响应中不出现密码、重置令牌、会话令牌或模型 Key 明文。
- 无 SMTP、短信及第三方登录配置时，注册、登录、修改密码和部署者辅助重置仍可完成。
- 默认配置可以直接注册；显式关闭注册后，现有用户仍可登录，注册接口和页面给出一致状态。
- HTTP 开发环境与 HTTPS 生产环境的 Cookie 配置明确区分；生产模式拒绝不安全默认值。

参考：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[OWASP CSRF 防护](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。
