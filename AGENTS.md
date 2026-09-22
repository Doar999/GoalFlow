# 仓库指南（人与 AI 的统一入口）

本文件是 GoalFlow 所有开发参与者和 AI 工具的第一份输入。任何编码会话开始前先读本文件；本文件与其他文档冲突时，以本文件指向的事实源为准。

适用对象包括 Claude Code、Codex、Cursor 等任意 AI 编码工具，以及不使用 AI 的人工开发。

## 项目状态

产品与开发设计已完成并可评审，**应用代码尚未开始实现**。seekdb 兼容性未验证，无任何迁移已生成。

不要假设仓库里存在尚未创建的目录、模块或接口。当前只有文档和工程规范。

## 必读顺序

1. [CONTEXT.md](CONTEXT.md) —— 领域术语。讨论和代码命名必须使用这里的术语，不得自造同义词。
2. [docs/product/PRD.md](docs/product/PRD.md) —— 已确认需求、验收标准、未决项。
3. 与当前任务对应的 [docs/development/](docs/development/) 设计文档。
4. [docs/development/05-module-contracts.md](docs/development/05-module-contracts.md) —— 模块契约与接口。
5. [docs/development/06-delivery-plan.md](docs/development/06-delivery-plan.md) —— 工作包 T01–T15 与验收。
6. 当前工作包的交接卡 `docs/worklog/T<NN>-*.md`（存在时）。

只读到本文件就动手写业务代码，属于流程错误。

## 文档状态标记

设计文档的每个结论带状态，**不同状态的约束力不同**：

| 标记 | 含义 | 可否作为实现依据 |
| --- | --- | --- |
| 已确认 | 产品或架构决策已定 | 可以，且不得擅自偏离 |
| 建议 / 推荐 | 设计稿提出的默认值 | 不可以，实现前需按 [决策规程](docs/engineering/00-workflow.md) 确认 |
| 待定 / 未决 | 尚无结论 | 不可以，遇到即停下提问 |

**AI 最容易犯的错误是把"建议"当成"已确认"直接实现。** 引用文档结论时必须连同状态一起引用。

## 仓库结构

```text
CONTEXT.md              领域术语（事实源）
AGENTS.md               本文件
CLAUDE.md               指向本文件
CONTRIBUTING.md         人工贡献流程
docs/
  product/              产品需求与 PRD（产品事实源）
  development/          模块与引擎设计
  engineering/          开发规范（流程、契约、AI 协作、代码与测试、完成定义）
  rfcs/                 改变已确认决策的提案
  worklog/              工作包交接卡
backend/                Python + FastAPI（尚未创建）
  src/goalflow/
  migrations/
  tests/
frontend/               React + TypeScript + Vite（尚未创建）
  src/
openapi/goalflow.yaml   HTTP 契约事实源（尚未创建）
scripts/                统一门禁脚本
```

## 技术基线（已确认）

前端 React + TypeScript + Vite + shadcn/ui；后端 Python 3.11+ + FastAPI；数据库 seekdb；数据访问与迁移 SQLAlchemy + Alembic；后台任务 Celery + Redis + Beat；作业事件 SSE；部署 Docker Compose + Nginx。

Agent 编排采用 LangGraph，模型接入采用 LangChain（`langchain-openai` / `langchain-anthropic`），支持 OpenAI 与 Anthropic 两个 provider。**图为短生命周期：一次作业内跑完即结束，不启用 checkpointer。** 等待用户、版本校验、预算与计划变更判级仍在 seekdb 业务表和业务模块，不下放给框架。不使用 `create_agent` 预制循环、LangChain memory / retriever / vectorstore，也不把数据库写入包装成模型可调用的 tool。LangSmith 追踪默认关闭。首版前端不引入 Redux / Zustand。

包管理：后端 `uv`，前端 `pnpm`。锁文件必须提交。

## 统一命令

所有人和所有 AI 使用同一组命令，不要自创等价命令：

```bash
bash scripts/install.sh      # 安装依赖与 Git 钩子
bash scripts/check.sh        # 格式化、lint、类型检查、契约漂移检查
bash scripts/test.sh         # 全部测试
bash scripts/test.sh unit    # 仅单元测试
bash scripts/test.sh e2e     # 跨组件验收场景
bash scripts/api-generate.sh # 从 FastAPI 导出 OpenAPI 并生成前端类型
```

Linux / macOS 上等价的 `make install` / `make check` / `make test` 可用。Windows 用 Git Bash 直接执行脚本。

提交前 `bash scripts/check.sh` 必须通过。声称"已完成"前 `bash scripts/test.sh` 必须通过，且必须贴出真实输出。

## 契约唯一事实源

| 对象 | 事实源 | 规则 |
| --- | --- | --- |
| HTTP API | `openapi/goalflow.yaml` | 由 FastAPI 导出；前端类型由它生成 |
| 前端 API 类型 | `frontend/src/shared/api/generated/` | 生成物，**禁止手工编辑** |
| 数据库结构 | `backend/migrations/` | 只能通过 Alembic 迁移变更 |
| 共享枚举与错误码 | `backend/src/goalflow/contracts/` | 单点定义，前端由生成类型消费 |
| 领域术语 | `CONTEXT.md` | 命名以此为准 |

改动上述任一项属于**公共契约变更**，流程见 [契约与所有权](docs/engineering/01-contracts-and-ownership.md)。公共契约变更不允许与业务实现混在同一个 PR 里。

## 代码风格

Python 3.11+，PEP 8：模块与函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`。Ruff 负责格式化与 lint，行宽 120。类型注解覆盖公共函数签名。

TypeScript 严格模式，禁止用 `any` 和非空断言 `!` 绕过类型。组件 `PascalCase`，hook `useXxx`，文件随导出主体命名。业务规则跟随 feature 目录，不建按技术类型划分的全局 `components/hooks/services` 大杂烩。

标识符、数据库字段、API 字段一律使用 CONTEXT.md 的英文术语（如 `goal_profile`、`task_batch`、`checkin`），不得混用同义词。多词术语用单下划线小写形式，已在设计文档中固定拼写的以设计文档为准（执行记录写 `checkin`，不写 `check_in`）。

注释只解释非显然的意图，不复述代码。

## 测试

pytest（`test_*.py` / `test_*`）与 Vitest + Testing Library。测试必须保护**外部可观察行为**或某个具体回归，复杂度本身不是加测试的理由。

重点覆盖真实规则：数据库事务与回滚、版本竞争（`expected_revision`）、时间预算冲突、任务依赖与环检测、用户数据隔离、模型调用异常与恢复。

数据库验收使用真实 seekdb，**不允许用 SQLite 通过的结果代替**。文案或低影响样式改动不需要补镜像测试。

详见 [代码与测试规范](docs/engineering/03-code-and-test-standards.md)。

## 提交与 PR

提交信息用 Conventional Commits：`feat(scheduling): 支持任务锁定`。分支名 `<类型>/T<工作包>-<简述>`，如 `feat/T05-budget-conflict`。

PR 必须关联工作包或 Issue、说明变更理由与范围、列出实际执行过的验证命令、标注用户可见变更与破坏性变更，并填写 **AI 使用声明**。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## AI 协作硬性约束

以下为红线，任何 AI 会话不得违反：

1. **不得擅自改动公共契约。** OpenAPI、迁移、共享枚举与错误码的变更需先取得负责人确认并单独提 PR。
2. **不得把"建议 / 待定"当作已确认需求实现。** 遇到未决项停下来提问，或在交接卡中明确记为假设。
3. **不得编造验证结果。** 只能报告真实执行过的命令及其真实输出；未运行就写"测试通过"是严重问题。
4. **不得手工编辑生成物**（`frontend/src/shared/api/generated/`、导出的 OpenAPI）。改契约走生成流程。
5. **不得跨越模块边界直接改对方状态表**，必须走 05-module-contracts.md 定义的 Interface。
6. **不得提交任何凭证**：模型 API Key、数据库口令、会话密钥。示例一律写进 `.env.example` 并使用占位符。
7. **不得扩大任务范围。** 只改当前工作包声明的可修改路径；发现范围外问题记录到交接卡的"未决问题"，不顺手改。
8. **长任务必须更新交接卡。** 见 [AI 协作规范](docs/engineering/02-ai-collaboration.md)。

## 交付物写法

设计文档与交付说明写成**自包含的最终状态**，直接体现当前结论。不要写"第二版改为……""根据评审意见调整……"这类过程叙述——决策历史属于 RFC 和 Git 历史，不属于设计文档正文。

中文直接用中文字符，不使用 Unicode 转义。

## 安全与配置

不提交密钥、本地虚拟环境、缓存、`dist/`、`node_modules/`。依赖变更必须同步锁文件，`scripts/check.sh` 会检查锁文件漂移。

用户模型凭证按 [模型接入设计](docs/development/07-model-provider-design.md) 加密存储，日志与错误信息中必须脱敏。
