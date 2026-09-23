# T03 注册与账号会话

| 项 | 值 |
| --- | --- |
| 工作包 | T03（见 [06-delivery-plan.md](../development/06-delivery-plan.md)） |
| 负责人 | （待填，后端与数据） |
| 状态 | 进行中：第 4 节决策已全部确认，开始实现 |
| 更新日期 | 2026-09-23 |
| 相关 PR | #（待填，单个 PR，带 `contract-change` 标签） |

> 本文件是给**人和 AI 共同阅读**的任务说明书与交接材料。它描述**当前状态**，不是日志：更新时直接改写成最新内容。

## 1. 目标

交付首版账号体系：账号标识＋密码自由注册、登录、退出、会话撤销、修改密码、部署者本地命令辅助重置，以及可配置的注册开关。做完之后，T04 起的每个业务接口都能从服务端会话拿到"当前用户是谁"，不接受客户端传入的 user_id。

达成标准：[08-auth-design.md](../development/08-auth-design.md) 的验收场景与 06 的 T03 验收项（双用户越权、并发同名注册、敏感值脱敏）全部有对应测试，并在与生产同构的 SQLite 配置上通过。

**本工作包只做后端。** 登录页、注册页和会话保护路由属于 T10。

## 2. 范围

按决策 C1，契约与实现合在同一个 PR，打 `contract-change` 标签；两者保持为独立提交。

### 可修改路径

契约面（放在先行的提交里）：

```text
backend/migrations/versions/0001_T03_*.py
backend/src/goalflow/contracts/errors.py         （新增账号相关错误码，见 C11）
backend/src/goalflow/api/routes/auth.py          （路由、请求/响应模型）
backend/src/goalflow/api/app.py                  （仅挂载 auth 路由）
backend/src/goalflow/api/dependencies.py         （新增当前用户依赖）
backend/src/goalflow/core/config.py              （新增 GOALFLOW_PUBLIC_ORIGIN，见 C7）
.env.example                                     （同上；补 GOALFLOW_DATABASE_URL 示例值，见 C20）
openapi/goalflow.yaml                            （生成物）
frontend/src/shared/api/generated/               （生成物）
docs/development/03-data-model.md                （仅第 2 节账号四张表，与迁移对齐）
docs/development/08-auth-design.md               （把本卡确认的项回写为"已确认"）
docs/engineering/01-contracts-and-ownership.md   （仅第 5 节，写明账号接口的幂等例外，见 C12）
```

业务实现：

```text
backend/src/goalflow/auth/                       （新模块：业务规则、Interface、本地命令）
backend/src/goalflow/db/base.py                  （新文件：DeclarativeBase，见 C2）
backend/tests/auth/
backend/tests/e2e/
backend/pyproject.toml、backend/uv.lock          （新增 argon2-cffi）
docs/worklog/T03-auth-session.md
docs/worklog/README.md                           （仅索引表）
```

### 明确不可修改

```text
backend/src/goalflow/db/engine.py、session.py    （T16 已交付，需要改时回到数据负责人）
frontend/                                        （生成物以外）
scripts/、.github/
```

### 不在本次范围内

- 前端登录、注册页和路由守卫（T10）。
- 通用幂等存储 `idempotency_requests`（见 C12 与第 8 节）。
- `audit_events` 表（见 C15 与第 8 节）。
- `/api/admin/*` HTTP 接口。管理能力首版只做本地命令（见 C14）。
- 修改时区等个人设置的接口：注册时可以写入时区，之后的修改接口不在本次范围内（见第 8 节）。
- 邮件找回、OAuth、Passkey、验证码。
- 用户模型凭证（T14）。

## 3. 输入依据

| 来源 | 引用内容 | 状态 |
| --- | --- | --- |
| [PRD](../product/PRD.md) | R01 注册登录、Q01 数据隔离 | 已确认 |
| [PRD](../product/PRD.md) 决策表 | D07：账号标识＋密码自由注册、服务端会话 Cookie、无邀请码、无邮件服务可运行 | 已确认 |
| [08-auth-design.md](../development/08-auth-design.md) | 用户流程 1–5；不隐式授予首位注册者管理员；禁用、改密、重置时撤销会话 | 产品方向已确认 |
| [08-auth-design.md](../development/08-auth-design.md) | Argon2id 参数、会话空闲与绝对有效期、SameSite=Lax、CSRF 手段、限流阈值 | 原为建议 / 待定，已由第 4 节确认 |
| [08-auth-design.md](../development/08-auth-design.md) | "接口建议"一节的端点清单 | 原为建议，已由 C14、C16 确认 |
| [03-data-model.md](../development/03-data-model.md) 第 1 节 | UUID 存 TEXT、事件时间存 ISO 8601 UTC、revision 并发控制 | 物理约定已确认（T01），字段为建议 |
| [03-data-model.md](../development/03-data-model.md) 第 2 节 | users、password_credentials、sessions、password_reset_tokens | 原为设计建议，与 08 有出入，已由 C18 确认 |
| [01-contracts 第 3、5 节](../engineering/01-contracts-and-ownership.md) | 契约单独 PR；写操作携带 Idempotency-Key；身份只从服务端会话获得 | 已确认 |
| [T16 交接卡](T16-data-layer-foundation.md) 第 5 节 | `DeclarativeBase` 位置、请求级会话依赖留给 T03 定 | 已由 C2、C3 确认 |
| [T02 交接卡](T02-engineering-foundation.md) 决策 A4 与第 8 节 | 去重存储属 T03 / T07；`GOALFLOW_DATABASE_URL` 须在 T03 前给出 | 已确认 / 未决 |
| `core/config.py` | 已有 `allow_registration`（默认开）与 `session_secret`（生产必填） | 已交付（T02） |

## 4. 决策与假设

本节全部由仓库负责人于 2026-09-23 确认。标为"契约"的项落在契约面的提交里，并回写 08 / 03 / 01。

### 流程与结构

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| C1 | PR 切分 | **契约与实现合为一个 PR**，打 `contract-change` 标签。PR 内契约面（迁移、错误码、路由与 schema、环境变量、生成物）与业务实现（`auth/`）保持为独立提交，契约提交在前 | 偏离 AGENTS.md 红线 1，由仓库负责人决定。`api/` 属于契约面（RFC 0002），实现必然要改路由处理函数，拆开只会多一个处理函数全是桩的中间 PR。标签只放行 CI，不改变评审要求：契约部分仍须契约负责人以外至少 1 人批准（00-workflow 第 6 节）。与 T16 决策 B6 的"仅限本次"相比，本条同样**只适用于 T03** | 工程流程 | 已确认 |
| C2 | `DeclarativeBase` 放哪 | 放 `db/base.py`：只定义 `Base` 和约束命名约定（`naming_convention`），不 import 任何业务模型。各模块的模型继承它。迁移手写，不依赖 autogenerate；另写一条用例，比对"ORM 元数据"和"迁移后真实库的反射结果"，防止两者漂移 | 放各模块会有多个 metadata；让 `env.py` import 全部业务模型会使迁移依赖业务代码。命名约定必须在第一张表之前定，否则 batch 重建时约束名不稳定 | 全部业务表 | 已确认 |
| C3 | 请求级会话依赖 | **不提供请求级 DB 会话依赖。** 事务边界归 auth 模块的 Interface 函数，每个函数自己开 `db.read()` / `db.write()`。路由只做参数校验和调用。另提供 `current_user` 依赖：在读事务里校验会话 | 请求级会话会让事务跨越整个请求（包括哈希计算和序列化），和"短写事务"协议冲突。读写分两个依赖的方案，要求路由作者判断该用哪个，容易选错 | 全部后续业务模块照此模式 | 已确认 |

### 会话与 Cookie

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| C4 | 有效期 | 空闲 7 天，绝对 30 天。`last_seen_at` **距上次更新超过 5 分钟才写**，在一次单独的短写事务里完成 | 08 写的是"具体时长待定"。个人效率工具，风险等级低于金融类应用。写节流是必须的，见第 9 节第 2 条；代价是空闲超时的实际精度只到 5 分钟 | 用户体验、写负载 | 已确认 |
| C5 | 令牌与摘要 | 会话令牌和重置令牌都由 `secrets.token_urlsafe(32)` 生成。库里存 `HMAC-SHA256(session_secret, token)`，只在创建时返回原文一次 | 令牌是 256 位随机数，不需要慢哈希。用 HMAC 而不是裸 SHA-256，是为了让已有的 `GOALFLOW_SESSION_SECRET` 有明确用途；代价是轮换这个密钥会让全部会话失效，需要写进部署文档 | 部署、安全 | 已确认 |
| C6 | Cookie 属性 | 生产环境：名字用 `__Host-goalflow_session`，带 `Secure; HttpOnly; SameSite=Lax; Path=/`，不设 Domain。local / test 环境：名字用 `goalflow_session`，不带 Secure。`Max-Age` 等于绝对有效期的剩余时间 | 08 要求"开发与生产明确区分、生产拒绝不安全默认值"。`__Host-` 前缀由浏览器强制要求 Secure 且不带 Domain，属于额外一层保护 | 前端联调、部署 | 已确认 |
| C7 | CSRF | 不用同步令牌。所有非 GET 请求都校验 Origin：必须等于 `GOALFLOW_PUBLIC_ORIGIN`；没有 Origin 时改看 `Sec-Fetch-Site`，必须是 `same-origin`；两者都没有就拒绝。再叠加 SameSite=Lax | 08 允许"CSRF 令牌或等效保护"。同域部署下，Origin 严格比对加 SameSite 属于 OWASP 认可的纵深组合，而且不需要前端管理令牌。**需要新增环境变量，属契约** | 部署、前端、所有写接口 | 已确认 |

### 密码与账号标识

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| C8 | 密码哈希 | 用 `argon2-cffi`，Argon2id，m=19456 KiB、t=2、p=1（OWASP 给出的最低推荐配置）。实现时在目标机型上做基准测试并记录耗时。登录成功时如果参数已变，就重新哈希 | 08 写的是"参数由配置和基准测试确定"。内存参数乘以并发登录数就是峰值内存，小机器上不宜用 argon2-cffi 的默认 64 MiB。参数写成模块常量，不新增环境变量 | 依赖、资源 | 已确认 |
| C9 | 密码规则 | 长度 12–128 个字符，不设字符组成规则，不强制定期更换，不允许与账号标识相同 | 08 没有规定。OWASP 建议至少 8 位，NIST SP 800-63B-4 要求单因素口令至少 15 位，12 位是两者之间的折中。影响用户可见行为，由仓库负责人确认 | 产品行为 | 已确认 |
| C10 | 账号标识规范化 | 依次做 NFKC → 去掉首尾空白 → casefold。长度 3–254；不得包含空白和控制字符；库里只存规范化后的值，并对它建唯一约束 | 08 只说"建议允许用户名或邮箱格式"。254 是为了容纳邮箱格式。只存规范化值会让界面显示小写形式，代价小于同时维护两列 | 数据、产品行为 | 已确认 |

### 接口与错误

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| C11 | 新增错误码（契约） | `INVALID_CREDENTIALS`（401）、`ACCOUNT_IDENTIFIER_UNAVAILABLE`（409）、`REGISTRATION_CLOSED`（403）、`RATE_LIMITED`（429，可重试，`details.retry_after_seconds` 与 `Retry-After` 头同值）。账号不存在、密码错误、账号已禁用，登录时一律返回 `INVALID_CREDENTIALS` | 不复用 `UNAUTHENTICATED`：那个码的含义是"没有有效会话，去登录"，前端据此跳转；登录失败复用它会引起误跳转。**注册查重不可避免地会暴露账号是否存在**，这点接受，用 C13 的限流缓解 | 契约、前端 | 已确认 |
| C12 | 幂等 | 账号接口**不要求** `Idempotency-Key`。重复注册由唯一约束兜底，第二次返回 `ACCOUNT_IDENTIFIER_UNAVAILABLE`，不建账号也不建会话。退出和撤销本身就是幂等的。通用的 `idempotency_requests` 存储不在 T03 做 | `idempotency_requests` 按 `owner_id` 划分，注册时用户还不存在，无法照搬。这是对 01-contracts 第 5 节"写操作携带 Idempotency-Key"的例外，由仓库负责人确认；例外范围回写到 01 第 5 节 | 全局契约约定 | 已确认 |
| C16 | 退出所有设备 | `POST /api/auth/logout-all` 撤销该用户的**全部**会话，包括当前会话 | 语义和名字一致，也覆盖了 08 的"用户可以撤销其他设备会话"。只撤销其他设备需要额外的会话列表接口，首版没有页面消费它 | 前端 | 已确认 |
| C17 | 改密、重置、禁用后的会话处理 | 修改密码：撤销其他会话，当前会话换发新令牌。重置密码：撤销全部会话，不自动登录。禁用账号：撤销全部会话 | 08 要求"禁用用户或修改、重置密码时撤销现有会话"，没有写当前会话怎么处理。重置密码不自动登录，是为了避免持有重置令牌就等于持有会话 | 产品行为 | 已确认 |
| C18 | 表结构（契约） | `users` 按 08 的字段，另加 `revision`、`updated_at`；`status` 取 active / disabled；`role` 取 user / admin；`timezone` 在注册时可选传入 IANA 名称，用 zoneinfo 校验，缺省为 `UTC`。`sessions` 按 08 的全字段，`ip_prefix` 存 IPv4 的 /24 或 IPv6 的 /48。重置令牌 30 分钟过期；签发新令牌时作废该用户未使用的旧令牌 | 03 与 08 的字段不一致（03 缺 created_at 等），以 08 为准并回写 03。现在就加 `revision`，是因为之后改时区、改状态都要走 `expected_revision`，事后再加得重建表 | 数据模型 | 已确认 |

### 限流、管理与审计

| 编号 | 事项 | 推荐 | 依据与取舍 | 影响范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| C13 | 限流 | 在每个 API 进程内按固定窗口计数，另由部署层的 Nginx `limit_req` 兜底（T13）。阈值：登录每账号标识 10 次 / 15 分钟、每 IP 前缀 30 次 / 15 分钟；注册每 IP 前缀 5 次 / 小时；凭令牌重置每 IP 前缀 10 次 / 15 分钟。不做账号锁定 | 另外两个方案：**Redis**，多进程计数准确，但 API 目前不依赖 Redis（作业走 outbox），会新增一个运行时依赖，而且测试需要真实的 Redis；**SQLite 表**，一致性好，但每次登录失败都要抢库级写锁，暴力破解会顺带把全站写操作拖慢。进程内计数的代价是：多 worker 时阈值按进程数放大，重启后清零 | 安全、部署 | 已确认 |
| C14 | 管理能力 | 首版只提供本地命令 `python -m goalflow.auth.cli`，子命令为 `promote` / `disable` / `issue-reset-token`。**不做** `/api/admin/*` HTTP 接口 | 08 同时列了本地命令和 HTTP 接口，后者是"接口建议"。首版没有管理页面消费 HTTP 接口，每多一个接口就多一块攻击面 | 产品行为、契约 | 已确认 |
| C15 | 审计 | 账号相关事件先写成结构化日志（不含任何令牌、密码、完整 IP），**不建 `audit_events` 表** | `audit_events` 的字段（before/after_revision、entity）以计划变更为中心设计，由 T03 首先建表等于替后续模块定下 schema。表归谁见第 8 节 | 运维、合规 | 已确认 |
| C19 | 客户端 IP | T03 只读 `request.client.host`。在反向代理之后取真实 IP，由部署时 uvicorn 的 `--proxy-headers --forwarded-allow-ips` 负责（T13） | 在应用里自己解析 `X-Forwarded-For` 容易被伪造 | 部署 | 已确认 |
| C20 | `GOALFLOW_DATABASE_URL` 示例值（契约） | `.env.example` 填 `sqlite+pysqlite:///./data/goalflow.db`，并把 `data/` 加进 `.gitignore` | T02 第 8 节要求"T03 之前必须给出"；T16 已经在启动时拒绝空值 | 本地开发 | 已确认 |

## 5. 验收场景

每条对应一个测试。数据库相关的一律使用真实库文件、WAL 模式和 T16 的连接装配。

- [ ] 默认配置下可以注册；注册成功后返回会话 Cookie，`GET /api/auth/session` 返回本人信息，不含令牌
- [ ] 同一账号标识（含大小写和全角/半角差异）并发注册 N 次，最终只有一个账号、一个会话，其余返回 `ACCOUNT_IDENTIFIER_UNAVAILABLE`
- [ ] 关闭注册后：注册返回 `REGISTRATION_CLOSED`；已有用户仍可登录
- [ ] 登录成功前后会话标识不同；已登录状态下再次登录会撤销旧会话
- [ ] 账号不存在、密码错误、账号已禁用三种情况的响应完全一致，耗时处于同一数量级
- [ ] 退出、退出全部、禁用、改密、重置之后，旧 Cookie 均返回 `UNAUTHENTICATED`
- [ ] 超过空闲期或绝对期的会话失效（用可注入时钟测试，不 sleep）
- [ ] 重置令牌只能使用一次；过期或已被新令牌作废的返回失败；使用后全部会话失效
- [ ] 首位注册者不是管理员；只有 `promote` 命令能授予 admin
- [ ] 数据隔离：用户 B 的 Cookie 无法读取或改动用户 A 的会话元数据；伪造的会话 ID、篡改的 Cookie 均被拒绝
- [ ] Origin 不符、缺少 Origin 且 `Sec-Fetch-Site` 不是 `same-origin` 的写请求被拒绝
- [ ] 超过限流阈值返回 `RATE_LIMITED` 与 `Retry-After`
- [ ] 脱敏：跑完全部用例后，扫描库文件、捕获的日志和所有响应体，不出现任何密码、会话令牌或重置令牌原文
- [ ] `GOALFLOW_ENV=production` 时 Cookie 带 Secure 和 `__Host-` 前缀；`GOALFLOW_PUBLIC_ORIGIN` 缺失时拒绝启动
- [ ] 迁移 0001 升级 → 降级 → 再升级，结果一致；ORM 元数据与迁移后的反射结果一致
- [ ] 无 SMTP、无第三方配置时，以上全部场景可完成

## 6. 进展

- 已完成：交接卡起草。
- 已完成：第 4 节决策全部确认。
- 进行中：契约面提交。
- 未开始：业务实现提交。

## 7. 验证结果

尚未执行任何命令。

## 8. 未决问题

| 问题 | 影响 | 需要谁决策 |
| --- | --- | --- |
| 通用幂等存储 `idempotency_requests` 归谁做。T02 A4 写的是"T03 / T07"，本卡建议归 T07（它和"重复异步请求返回原作业"共享语义），T04 的第一个版本化写接口之前必须落地 | T04 起的写接口依赖它 | 集成负责人 |
| `audit_events` 表归谁建、字段是否要兼顾账号事件 | 08 要求账号操作写审计，C15 先用日志代替 | 数据负责人与集成负责人 |
| 修改时区、查看会话列表等个人设置接口归谁 | PRD 第 61 行"设置：每周额度、时区、账号及偏好"没有对应工作包 | 集成负责人 |
| `idempotency_requests.owner_id` 与注册这类无主请求的关系 | C12 在 T03 里绕开了，但 01 第 5 节的规则需要写明例外 | 集成负责人 |
| [T02 交接卡](T02-engineering-foundation.md) 头部状态仍是"评审中"，索引表写的是"已完成" | 文档不一致，不影响行为 | T02 负责人 |
| 部署者可选开启验证码（08 的安全规则） | 首版不做 | 产品决策人 |

## 9. 给接手者

1. **不要在写事务里计算或校验 Argon2。** 一次哈希几十毫秒，SQLite 的写锁是库级的，放进事务就会让全站写操作排队。正确的顺序是：注册时先在事务外算好哈希，再进 `db.write()` 插入；登录时先在 `db.read()` 里取出凭证，在事务外校验，再进 `db.write()` 重新检查账号状态并创建会话。
2. **`last_seen_at` 不能每个请求都写。** 每个带登录态的请求都会校验会话，如果每次都更新，所有读请求都会变成写请求，WAL 模式"读不阻塞写"的好处就没了。见 C4 的节流规则。
3. **账号不存在时也要做一次假校验。** 用一个固定的假哈希调用一次 verify，否则响应时间会泄露账号是否存在。
4. **脱敏靠测试守住，不靠自觉。** 第 5 节的脱敏用例要真正扫描库文件、日志和响应体，不能只断言某个字段不在响应模型里。
5. 其余沿用 [T16 交接卡](T16-data-layer-foundation.md) 第 6 节：不要绕过 `create_database_engine()`，也不要在写事务里发 HTTP 请求。
