# RFC 0002：修正契约 PR 门禁与 OpenAPI 生成流程的冲突

| 项 | 值 |
| --- | --- |
| 提案名称 | `contract_pr_gate` |
| 提出日期 | 2026-09-22 |
| 提出人 | Doar |
| 影响范围 | 工程流程（CI 门禁）、公共契约的判定边界 |
| 状态 | 草案 |
| 关联 PR | #（待填） |

## 摘要

`.github/workflows/ci.yml` 的 `pr-hygiene` 作业与同一份 CI 里的 `check` 作业互相矛盾：任何新增或修改 HTTP 接口的 PR 都无法同时通过两者。本提案把"公共契约"的门禁判定从**按路径粗分**改为**区分契约面与业务模块**，并修正判定所用的 Git diff 形式。

## 动机

[契约与边界所有权](../engineering/01-contracts-and-ownership.md) 第 9 节规定的前后端并行路径是：契约 PR 合并 → `openapi/goalflow.yaml` 就位 → 前端据此生成类型。而 `openapi/goalflow.yaml` 是**生成物**，由 `bash scripts/api-generate.sh` 从 FastAPI 的路由与 Pydantic 模型导出——它的源就在 `backend/src/goalflow/api/` 下。

当前门禁把生成物判为 `contract`，把它的源判为 `impl`，两者同时出现即报错。于是每一个改接口的 PR 都落进死锁：

| 做法 | `check` 作业（契约漂移检查） | `pr-hygiene` 作业 |
| --- | --- | --- |
| 改路由，不跑 `api-generate.sh` | 失败：`openapi/goalflow.yaml` 已过期 | 通过 |
| 改路由，跑 `api-generate.sh` | 通过 | 失败：contract 与 impl 混合 |

这不是某个工作包的特殊情况，而是 T02 之后**所有**涉及接口的工作包都会撞上的结构性问题。当前它尚未暴露，只是因为 `backend/` 还没有被创建。

同一作业还存在一个独立的判定缺陷：它用两点式 `git diff BASE HEAD` 取改动文件，比较的是基线分支**当前 tip** 与 PR head 的树差异。当 `main` 在 PR 开出之后又合入了其他人的提交，那些改动会以反向 diff 的形式出现在结果里，被算到本次 PR 头上。"已合并的迁移不得被修改"那一步受此影响更重——别人**新增**的迁移会被反向判成本次 PR **删除**了迁移，从而误报。

## 现状

| 来源 | 当前结论 | 状态 |
| --- | --- | --- |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 2 节 | `openapi/goalflow.yaml`、`backend/migrations/versions/`、`contracts/enums.py`、`contracts/errors.py` 属于公共契约 | 已确认 |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 3 节 | 契约变更独立成 PR，只含 schema、枚举、错误码、迁移、生成物和契约测试，不夹带业务实现 | 已确认 |
| [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 9 节 | 契约 PR 合并后 `openapi/goalflow.yaml` 就位，前端据此生成类型 | 已确认 |
| `.github/workflows/ci.yml` `pr-hygiene` 作业 | contract 与 impl 按上述路径集合粗分，同时命中即失败 | 已实现，与上述三条冲突 |

第 3 节写明了这条约束的目的：**"任何人 rebase 后一眼能看出'契约变了'还是'别人的业务逻辑变了'。"** 目的是**可区分性**，不是物理隔离。本提案保留这个目的，改变达成它的手段。

## 提案

### 契约面与业务模块

把后端代码划分为两个面，门禁只拦截"两面同时出现在一个 PR 里"：

| 面 | 路径 | 含义 |
| --- | --- | --- |
| 契约面 | `openapi/`、`backend/migrations/`、`backend/src/goalflow/contracts/`、`backend/src/goalflow/api/` | 对外可见的接口形状、错误码、枚举与数据库结构 |
| 业务模块 | `backend/src/goalflow/{auth,goals,planning,scheduling,agent,jobs}/`、`frontend/src/features/` | 业务规则与功能实现 |
| 中性 | `backend/src/goalflow/{core,db}/`、`frontend/src/{app,shared}/`、`backend/tests/`、`scripts/`、`docs/` | 两面都不算 |

`backend/src/goalflow/api/` 进入契约面，是因为它就是 `openapi/goalflow.yaml` 的源。把生成物和它的源放在同一面，死锁随之消失：一个"契约 PR"自然地包含路由声明、Pydantic 模型、导出的 OpenAPI 以及重新生成的前端类型。

`core/` 与 `db/` 归为中性，是因为配置、日志、请求上下文、数据库引擎与基类既不表达对外契约，也不表达业务规则。把它们算进任何一面，都会让门禁在"建工程骨架"这类必然横跨多处的改动上产生无意义的拦截。

这一划分依赖 [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 8 节已确认的约束——api 层只做权限校验与调用模块 Interface，业务规则在模块内部。api 层如果夹带业务编排，门禁确实拦不住；这由 CODEOWNERS 与 [00-workflow.md](../engineering/00-workflow.md) 第 6 节"公共契约必须由契约负责人以外的至少 1 人批准"兜底。

### 显式放行出口

确有需要同时改两面时（典型场景：新建工程骨架、一次性重构模块边界），PR 打 `contract-change` 标签即放行。作业在 GitHub Step Summary 中列出本次的契约面改动文件清单，使其在评审页面上一眼可见。

放行是**显式且留痕**的，比让人去改门禁本身要好。它不削弱 00-workflow 第 6 节的评审要求——标签只影响 CI 是否拦截，不影响谁有权批准。

### diff 形式修正

`pr-hygiene` 的两个步骤统一改用三点式 `git diff --name-only "$BASE...$HEAD"`，即从 merge-base 起算，只包含本 PR 自己引入的改动。

## 技术细节

判定逻辑改为：

```bash
CHANGED="$(git diff --name-only "$BASE...$HEAD")"

contract=0
impl=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    frontend/src/shared/api/generated/*)
      # 生成物随契约一起变更，不单独判定
      ;;
    openapi/*|backend/migrations/*|backend/src/goalflow/contracts/*|backend/src/goalflow/api/*)
      contract=1 ;;
    backend/src/goalflow/auth/*|backend/src/goalflow/goals/*|backend/src/goalflow/planning/*|backend/src/goalflow/scheduling/*|backend/src/goalflow/agent/*|backend/src/goalflow/jobs/*|frontend/src/features/*)
      impl=1 ;;
  esac
done <<< "$CHANGED"
```

`frontend/src/shared/api/generated/*` 的豁免分支必须排在前面，否则会被 `frontend/src/features/*` 之外的规则误判——当前实现已经是这个顺序，保持不变。

本提案不改变：

- [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 2 节的公共契约清单，`openapi/goalflow.yaml` 仍是 HTTP 契约的事实源。
- 第 4 节的统一错误结构与 `contracts/errors.py` 单点定义。
- 第 5 节的身份、幂等、`expected_revision` 三条全局约束。
- 第 6 节的迁移规程，包括"已合并的迁移不可修改"——该检查本身保留，只修正其 diff 形式。
- `check.sh` 的契约漂移检查。它保证 `openapi/goalflow.yaml` 与代码始终一致，是本提案能够放宽路径判定的前提。

不涉及数据模型与迁移，无破坏性变更。

## 替代方案

**把 `api/` 留在业务侧，接口实现一律拆两个 PR。** 语义上最贴近"契约与实现分离"，但第一个 PR 只有路由声明而没有业务实现，测试无法通过，`main` 会短暂处于不可运行状态——这违反 [00-workflow.md](../engineering/00-workflow.md) 第 7 节"`main` 始终保持可运行、CI 全绿"。2–3 人规模下每个接口都付出这个代价，收益也不成比例。已排除。

**把 `openapi/` 移出门禁判定，contract 只保留 `migrations/` 与 `contracts/`。** 改动最小，死锁同样消失，理由是这两者才是手写的单点定义，而 openapi 的一致性已由 `check.sh` 保证。缺点是接口形状的变更彻底失去机械保障，完全依赖人工评审。在"AI 会话大量参与实现"的前提下，机械保障的价值高于它带来的约束成本。未采纳，但如果本提案的路径划分在实践中被证明维护成本过高，这是首选的退路。

**只加标签放行，不动路径判定。** 死锁场景下每个 PR 都要打标签，标签从"例外声明"退化成"例行动作"，很快就没人真的看它了。已排除。

**什么都不做。** T02 交付后，任何涉及接口的 PR 都无法通过 CI。考虑到 [00-workflow.md](../engineering/00-workflow.md) 第 6 节把 CI 称为"这套流程里唯一不依赖人力的质量下限"，让它长期处于必然失败状态，实际后果是大家学会忽略它。不可接受。

## 影响与迁移

- 受影响文件：`.github/workflows/ci.yml`、[01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 3 节。
- 无需任何人 rebase，无需重新生成前端类型。
- 尚无已合并的业务 PR 受此判定影响——`backend/` 与 `frontend/` 均未创建。
- 对工作包的影响：T02 据此可以在一个 PR 内交付工程骨架与契约基线；T03 之后的工作包在改接口时不再需要拆分 PR，除非同时改到业务模块。

## 未决问题

- 合并前需确认：`contract-change` 标签由谁创建与维护，以及是否需要在 `.github/` 下补一份标签定义。本提案倾向于在仓库设置中手工创建一次即可，不引入标签同步工具。
- 刻意排除在本 RFC 范围外：`check.sh`、`install.sh`、`test.sh` 的内容，以及 CI 是否需要接入真实 seekdb 服务——后者属于 T01 的结论范围。
- 后续需要自己的 RFC：如果将来 api 层被证明承担了过多业务编排，`api/` 归属契约面的前提就不再成立，届时需重新评估。

## 接受后的回写清单

- [ ] 更新 `.github/workflows/ci.yml` 的 `pr-hygiene` 作业
- [ ] 更新 [01-contracts-and-ownership.md](../engineering/01-contracts-and-ownership.md) 第 3 节，写明契约面与业务模块的划分以及 `contract-change` 标签的用法
- [ ] 在仓库中创建 `contract-change` 标签
- [ ] 更新 [docs/rfcs/README.md](README.md) 索引
- [ ] 不涉及 `CONTEXT.md` 术语变更
