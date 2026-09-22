# 贡献指南

本文面向人。AI 工具的规则见 [AGENTS.md](AGENTS.md)，完整流程见 [docs/engineering/](docs/engineering/)。

## 环境准备

需要：Git、Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 24+、[pnpm](https://pnpm.io/)、Docker（用于 seekdb 与 Redis）。

```bash
git clone <仓库地址>
cd GoalFlow
bash scripts/install.sh
cp .env.example .env
```

`.env` 里填本地的数据库与 Redis 地址。**不要把 `.env` 提交上去。**

Windows 用 Git Bash 执行脚本；Linux / macOS 也可以用 `make install`。

## 常用命令

```bash
bash scripts/check.sh         # 格式化、lint、类型检查、锁文件与契约漂移
bash scripts/test.sh          # 全部测试
bash scripts/test.sh unit     # 仅单元测试
bash scripts/test.sh e2e      # 跨组件验收场景
bash scripts/api-generate.sh  # 导出 OpenAPI 并生成前端类型
```

**提交前 `scripts/check.sh` 必须通过，提 PR 前 `scripts/test.sh` 必须通过。**

## 开始一个任务

1. 从 [06-delivery-plan.md](docs/development/06-delivery-plan.md) 的 T01–T15 选一个依赖已满足的工作包。
2. 复制 `docs/worklog/TEMPLATE.md` 建交接卡，**在写代码之前**填好目标、范围、可改路径、验收场景。
3. 如果需要改接口、枚举、错误码、表结构，先单独提契约 PR 并合并。
4. 建分支开工。

完整路径见 [开发流程](docs/engineering/00-workflow.md)。

## 分支命名

```text
<类型>/T<工作包编号>-<简短描述>
```

例：`feat/T05-budget-conflict`、`fix/T11-checkin-duplicate`、`docs/T02-contract-guide`、`chore/T02-ci-cache`。

类型取 `feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `perf`。不属于任何工作包时省略 `T<NN>` 段。

`main` 是唯一长期分支，禁止直接推送。

## 提交信息

Conventional Commits，主题行简短并带模块 scope：

```text
feat(scheduling): 支持任务锁定
fix(jobs): 旧 Worker 晚返回不再重复提交
docs(rfcs): 收敛日期移动范围默认值
```

主题行用中文或英文都可以，但同一个 PR 内保持一致。不要写 `update`、`fix bug` 这类无信息量的主题。

**每个能通过 `scripts/check.sh` 的中间状态就提交一次**，不要攒成一个巨大提交——AI 单次产出量大，攒起来的提交没人能真正审。

## 提交 PR

按 [PR 模板](.github/pull_request_template.md) 填写，其中两项最容易被敷衍，但都是硬要求：

- **验证方式**：粘贴**真实执行过**的命令与输出。没运行过的命令不要写上去。
- **AI 使用声明**：写明工具与模型，以及人工复核了哪些部分。未使用写"未使用"。

提 PR 前对照 [完成定义](docs/engineering/04-definition-of-done.md) 逐条自查。

## 评审规则

| 变更类型 | 要求 |
| --- | --- |
| 公共契约（OpenAPI、迁移、共享枚举与错误码） | 必须他人批准，不得自审自合 |
| 跨模块行为、排期规则、权限与数据隔离 | 必须他人批准 |
| 单模块内部实现、测试、文档 | 可自审，但需在 PR 里写明自审理由 |
| 纯文案、样式、注释 | 可自审自合 |

无论哪一档，**CI 全绿都是硬前提**。人手紧张时可以放宽评审时效，不能放宽门禁。

评审关注点见 [评审清单](docs/engineering/04-definition-of-done.md)。

## 合并

squash merge 到 `main`，提交信息用 PR 标题，然后删除功能分支并更新交接卡。

如果这次收敛了某个"建议"状态的结论，**记得同步更新对应设计文档的状态标记**。

## 报告问题

用 [Issue 模板](.github/ISSUE_TEMPLATE/)：缺陷报告需要复现步骤、期望行为、实际行为、环境信息；任务提案需要说明属于哪个工作包、影响哪些模块。

安全问题（凭证泄漏、越权、数据隔离失效）不要公开提 Issue，直接联系维护者。
