# 前端工程架构 v0.1

状态：推荐实施方案，技术栈 React + TypeScript + Vite + shadcn/ui 已确认；下面的配套库和目录约定尚待代码初始化验证。

## 配套选型

| 责任 | 推荐 | 使用规则 |
| --- | --- | --- |
| 页面路由 | React Router，Vite SPA 模式 | 路由负责页面与布局，不承担业务事实存储 |
| 服务端状态 | TanStack Query | 请求缓存、作业状态和 mutation 后刷新；不把缓存当数据库 |
| HTTP 契约 | openapi-typescript + openapi-fetch | 从 FastAPI OpenAPI 生成类型，禁止手写重复响应类型 |
| 表单 | React Hook Form + Zod | 处理前端交互与即时提示；后端 Pydantic 和业务规则仍是最终校验 |
| UI | shadcn/ui + 项目设计令牌 | 公共组件源码纳入项目，由指定负责人维护接口 |
| 测试 | Vitest + Testing Library；必要时 MSW | 覆盖关键交互和契约，不镜像实现细节 |

首版不引入 Redux 或 Zustand。页面临时状态用组件状态，跨页面的服务端数据由 TanStack Query 管理，身份从 `/api/auth/session` 获取。出现无法由这两类清晰表达的真实共享客户端状态后再评估全局状态库。

官方依据：[React Router 模式](https://reactrouter.com/start/modes)、[TanStack Query](https://tanstack.com/query/latest/docs/framework/react/reference/index)、[openapi-fetch](https://openapi-ts.dev/openapi-fetch/)、[React Hook Form](https://www.react-hook-form.com/)、[Zod](https://zod.dev/)。

## 页面与导航建议

- `/register`、`/login`：公开账号流程；注册是否开放由服务端实例配置返回。
- `/today`：登录后的默认首页。
- `/goals`、`/goals/:goalId`：目标列表、档案与当前计划。
- `/goals/:goalId/planning`：澄清、路线比较及计划草稿检查。
- `/review`：历史日志和阶段回顾。
- `/settings/availability`：每周时间额度。
- `/settings/models`：OpenAI/Anthropic 模型配置。
- `/settings/account`：账号、密码及会话。

认证布局负责会话缺失时导航到登录页；权限仍由后端逐请求检查。未登录重定向保留站内目标路径，登录后返回。注册成功后直接建立会话，并引导用户配置模型和默认可用时间。

## 目录约定

```text
frontend/src/
  app/                 # 启动、路由、providers、全局错误页
  features/
    auth/
    today/
    goals/
    planning/
    review/
    model-settings/
  shared/
    api/               # 生成类型、fetch client、错误映射
    ui/                # shadcn/ui 与项目公共组件
    lib/               # 日期、格式化等无业务归属工具
    test/
```

业务规则跟随 feature，避免创建按技术类型划分的全局 components/hooks/services 大杂烩。feature 不直接修改另一 feature 的内部缓存；共享操作通过公开 hook 或 API 模块完成。

## API 和状态规则

FastAPI 输出 OpenAPI 文档，CI 生成并检查 TypeScript 类型是否过期。`openapi-fetch` 统一设置同域 Cookie、CSRF 头、request ID 和错误映射；组件不能散落裸 `fetch`。

TanStack Query 的 key 至少包含资源类型、用户可见 ID、日期或版本。mutation 成功后用服务器返回的 revision 更新或精确失效相关查询。计划启用、时间预算、关联和重大变更不做假成功的乐观更新；简单反馈可以显示“提交中”，收到服务器确认后再成为正式记录。

SSE 使用独立 JobEventClient，按 job_id 连接并保存最后事件序号；断线后使用 Last-Event-ID 补读，再调用作业查询接口确认最终结果。事件只用于刷新状态，不能仅凭临时 token 流把计划标记成功。

## 表单与时间

Zod schema 用于浏览器输入和环境配置，不复制全部后端业务规则。服务器返回字段级错误与业务冲突时，分别展示在字段和页面冲突区域。

前端发送用户 IANA 时区和明确的本地日期；日期字符串不隐式转换成浏览器 UTC。预计分钟数用整数。今日页使用服务器返回的有效额度、已投入和剩余容量，不在多个组件重复计算。

## 响应式与可访问性

桌面端支持方案并列比较和对话侧栏；窄屏改为逐张查看、底部导航或抽屉。操作不能只依赖悬停和颜色，任务状态提供文本；对话流、作业进度和错误使用合适的焦点及实时区域策略。具体视觉稿后续形成。

## 验收重点

- 未登录不能渲染私有数据；401 后清理当前用户缓存，不能短暂显示上一位用户内容。
- 修改目标或日期后 query key 不串数据；登出时清空所有用户相关缓存和 SSE 连接。
- 重复点击开始执行只产生一次业务结果，冲突和过期版本可理解。
- SSE 断开重连后不会重复追加最终消息；刷新页面可以从 HTTP 状态恢复。
- 桌面和常见手机宽度可以完成登录、今日任务反馈、方案选择和模型配置。
