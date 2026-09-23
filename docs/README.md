# 项目文档导航

当前阶段：产品与开发设计，工程骨架与数据库行为验证已完成。尚未开始业务代码实现。

开始编码前先读 [AGENTS.md](../AGENTS.md)（人与 AI 的统一仓库指南）与 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 产品入口

- [首版 PRD](product/PRD.md)：已确认需求、验收标准、建议和未决项。
- [探索记录](product/00-product-discovery.md)：讨论背景与初步模型。
- [今日执行页](product/01-today-page.md)
- [多目标协调](product/02-multi-goal-coordination.md)
- [方案预览与选择](product/03-route-selection.md)
- [开源产品与用户范围](product/04-open-source-product.md)
- [首版领域范围](product/05-domain-scope.md)
- [开源与模型配置](product/06-model-configuration.md)
- [目标创建与需求澄清](product/07-goal-clarification.md)
- [执行计划生成与预览](product/08-plan-generation.md)
- [任务反馈、验证与计划调整](product/09-feedback-and-adjustment.md)
- [Agent 辅助能力与证据验证范围](product/10-agent-assistance-and-verification.md)
- [三类领域专业规则与责任边界](product/11-domain-rules.md)
- [目标形态、生命周期与结束规则](product/12-goal-lifecycle.md)
- [目标关联、依赖与解除规则](product/13-goal-links.md)

## 开发入口

- [规划与约束](development/00-development-planning.md)
- [前端选型](development/01-frontend-options.md)
- [后端架构](development/02-backend-proposal.md)
- [核心数据模型](development/03-data-model.md)
- [Agent 工作流](development/04-agent-workflows.md)
- [模块契约](development/05-module-contracts.md)
- [开发任务与验收](development/06-delivery-plan.md)
- [用户模型接入设计](development/07-model-provider-design.md)
- [注册、登录与会话设计](development/08-auth-design.md)
- [前端工程架构](development/09-frontend-architecture.md)
- [目标澄清引擎](development/10-clarification-engine.md)
- [路线生成与比较引擎](development/11-route-engine.md)
- [计划生成、滚动细化与启用引擎](development/12-plan-engine.md)
- [多目标排期与冲突引擎](development/13-scheduling-engine.md)
- [执行反馈、验证与调整引擎](development/14-feedback-adjustment-engine.md)
- [辅助能力、材料解析与验证实现基线](development/15-assistance-and-verification-design.md)
- [领域策略包与约束校验实现基线](development/16-domain-policy-design.md)
- [目标生命周期与结束实现基线](development/17-goal-lifecycle-design.md)
- [目标关联与依赖解除实现基线](development/18-goal-link-design.md)

## 工程规范入口

- [开发流程与决策规程](engineering/00-workflow.md)：文档分层、结论状态、任务路径、评审规则、里程碑门。
- [契约先行与边界所有权](engineering/01-contracts-and-ownership.md)：公共契约清单、变更流程、迁移规程、模块边界。
- [AI 协作规范](engineering/02-ai-collaboration.md)：会话启动清单、交接卡、任务粒度、验证责任、禁止项。
- [代码与测试规范](engineering/03-code-and-test-standards.md)：目录结构、语言规范、测试原则与必测场景。
- [完成定义与评审清单](engineering/04-definition-of-done.md)：DoD、评审关注点、打回标准。
- [RFC](rfcs/README.md)：改变已确认决策的提案。
- [工作包交接卡](worklog/README.md)：每个任务的当前状态与交接材料。

多人协作阅读顺序：PRD → 对应模块设计 → 模块契约 → 开发任务。标注为建议或待定的内容不能当作已确认产品规则。术语见根目录 CONTEXT.md。
