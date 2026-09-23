# 工作包交接卡

本目录存放每个工作包的**交接卡**：一份同时面向人和 AI 的任务说明书与交接材料。

## 为什么需要

AI 会话的记忆会随会话结束消失，人的记忆会衰减，只有仓库里的文件是共享且持久的。交接卡把"这个任务要做什么、已经决定了什么、还剩什么"外置到仓库，使得换人、换工具、换会话都能继续，而不是重新推导一遍。

## 怎么用

1. 领取工作包时，复制 [TEMPLATE.md](TEMPLATE.md) 为 `T<NN>-<slug>.md`，例如 `T05-scheduling.md`。
2. **在写代码之前**填好"目标、范围、输入依据、验收场景"四节——这同时就是给 AI 的任务说明书。
3. 每次会话结束前更新"决策与假设、进展、验证结果、未决问题"。
4. PR 合并后收尾，把已完成的部分移出"进行中"，保留未决问题。

详细规则见 [AI 协作规范](../engineering/02-ai-collaboration.md)。

## 写法

交接卡描述**当前状态**，不是日志。更新时直接改写成最新内容，不要追加"第二次会话：……"这样的流水账——真正的历史在 Git 里。

"验证结果"一节只记录真实执行过的命令与真实输出。没运行过的不要写。

## 索引

开工前先看这张表，避免两个人的 AI 同时改同一个模块。

| 工作包 | 交接卡 | 负责人 | 状态 |
| --- | --- | --- | --- |
| T01 | [T01-sqlite-verification.md](T01-sqlite-verification.md) | 待填 | 已完成（39 条用例通过，结论已回写） |
| T02 | [T02-engineering-foundation.md](T02-engineering-foundation.md) | 待填 | 已完成 |
| T03 | [T03-auth-session.md](T03-auth-session.md) | 待填 | 评审中（PR #10） |
| T07 | [T07-job-execution.md](T07-job-execution.md) | 待填 | 进行中（PR-1 已合并 #11；PR-2 作业核心评审中） |
| T16 | [T16-data-layer-foundation.md](T16-data-layer-foundation.md) | 待填 | 已完成（连接装配与 Alembic 脚手架已合并） |

工作包定义见 [06-delivery-plan.md](../development/06-delivery-plan.md) 的 T01–T16。
