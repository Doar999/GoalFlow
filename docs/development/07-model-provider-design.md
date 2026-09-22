# 用户模型接入设计 v0.1

依据：用户已确认开源定位、个人模型配置及同时支持 OpenAI/Anthropic 两个 provider；模型接入采用 LangChain 统一 chat model 抽象（见 [RFC 0001](../rfcs/0001-adopt-langchain-langgraph.md)）。采用 `langchain-core`、`langchain-openai`、`langchain-anthropic`，具体版本实施前锁定。兼容范围是本产品的模型调用能力，不是实现一个对外兼容所有供应商端点的代理服务。

## 数据与调用

### provider 与能力契约（建议）

首版双 provider 已确认：`openai` 与 `anthropic`，由 `init_chat_model(model=..., model_provider=..., api_key=..., base_url=..., timeout=..., max_retries=...)` 构造实例。OpenAI 的 Responses / Chat Completions 选择由 `langchain-openai` 的 `use_responses_api` 参数承担，不再是两条自有代码路径。供应商预设是配置数据；禁止根据模型名称猜测 provider，也禁止调用失败后静默改用另一个 provider 或端点。当前未运行真实调用。

统一输入包含指令、规范化消息、输出 schema、超时和输出限额，由本产品的模型调用 Interface 映射为 LangChain 的消息对象与参数。只转发模型明确支持的参数。统一输出从 `AIMessage` 取文本、结构化候选（`with_structured_output(..., include_raw=True)` 的 `parsed`）、`usage_metadata`、结束原因与标准化错误，并保留供应商原始原因代码；不将推理内容或工具块误作为用户可见回答。业务规则校验对两个 provider 相同。

chat model 的 `max_retries` **必须显式设置**——默认值为 6，与 Celery 重试叠加会产生乘法放大；两者共用同一调用预算。流中断后不直接把重试文本拼到半截回答上，最终消息单独原子发布。chat model 实例按 owner_id 与配置版本隔离构造，不复用跨用户实例，也不通过全局环境变量切换不同用户的密钥。

`LANGSMITH_TRACING` 默认关闭，`.env.example` 与部署文档不得开启：用户目标与执行记录属于私人数据，不默认外发到第三方追踪服务。

能力采用 supported/unsupported/unknown，并保存测试时间和配置版本。基本生成是最低门槛；原生 schema 不可用时可有限解析加规则校验，不伪报原生能力——LangChain 在部分 provider 上会用提示工程模拟结构化输出，这种情况记为 unsupported 而不是 supported。流式不可用时显示等待后完整返回；工具调用和向量不是首版规划必需能力。

测试使用固定无私人内容的短提示，限制输出、重试和超时；分别展示鉴权/权限、路径/模型、限流、网络和响应协议错误，脱敏供应商错误。模型列表失败不能直接判定生成不可用。

作业创建时绑定配置 ID 和 revision。更换默认值只影响新作业；旧配置仍有效时原作业沿用绑定，修改/撤销配置则按下述规则停止。用户确认后恢复属于新运行，可绑定当前默认模型，并记录选择和重新校验业务版本。

更换默认配置**不改写已生成任务的 `verification_policy`**，也不改写已生成的计划。换到能力更少的模型时按能力门控如实降级，受影响的未开始任务在开始前提示当前模型不满足其提交形式，规则见 [辅助能力、材料解析与验证实现基线](15-assistance-and-verification-design.md) 第 4 节。

验收分别覆盖 `openai` 与 `anthropic` 两个 provider 的非流式、流式、错误、用量与结构化输出路径；再验证自定义兼容服务和本地服务。包含无鉴权、缺失流式、非法 JSON、未知用量、配置变更及出站限制。不能以一个 provider 通过代替另一个验证，也不能以 LangChain 的抽象存在为由跳过真实调用。

官方核对资料（2026-09-20）：[DeepSeek 接入](https://api-docs.deepseek.com/zh-cn/)、[Ollama 部分接口兼容](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx)、[Claude 原生 API](https://platform.claude.com/docs/en/api/overview)。官方说明不等于目标模型或部署环境已实测。

### 凭证与运行快照

model_configs 用 `model_provider` 与 `api_mode` 代替含糊的单一 protocol 字段：`model_provider` 取 `openai` / `anthropic`，直接作为 `init_chat_model` 的 `model_provider`；`api_mode` 仅在 `model_provider=openai` 时有效，取 `responses` / `chat_completions`，映射 `use_responses_api`，`anthropic` 下必须为空。检查合法组合，修改 API 模式视为配置版本变化。依据：[LangChain chat model 参数](https://docs.langchain.com/oss/python/langchain/models)、[langchain-openai](https://docs.langchain.com/oss/python/integrations/providers/openai)、[langchain-anthropic](https://docs.langchain.com/oss/python/integrations/providers/anthropic)。

建议增加 model_configs（id、owner_id、name、model_provider、api_mode、base_url、model_id、credential_ciphertext、encryption_key_version、revision、enabled、capabilities_json、last_test_at）及用户默认配置引用。服务端密钥加密主密钥由部署环境提供，不与密文放在同一业务表；禁止明文进入日志、作业载荷、导出样例或前端读接口。

作业保存配置 ID、版本和非敏感模型快照，不保存明文 Key。Worker 按 owner_id 读取配置；执行前校验是否有效。排队期间配置被改动则停止并要求重新发起，避免静默切换供应商；已发出的请求不能通过删除配置收回，但后续重试必须检查禁用/删除状态。

模型调用 Interface 接收服务端解析后的配置、必要上下文和输出 schema，返回内容、用量、错误及能力信息。供应商差异由 LangChain 承担，chat model 的构造集中在该 Interface 内，不把 provider 参数、鉴权头和特殊参数散落到业务模块或图节点里。用户输入的 URL 或模型名不能变成可执行代码。

## 自定义地址

多用户托管时，自定义服务地址需做服务端出站访问控制：限制协议、端口和地址范围，校验 DNS 解析及重定向，阻止访问云元数据与未授权内网，禁止把凭证转发到未批准的重定向目标。这层校验在 `base_url` 交给 LangChain **之前**执行，框架不提供该保护，不得因为改用框架而省略。

自部署访问本地模型是合理需求，使用实例管理员设置的明确允许列表开放本地/内网端点；普通用户不能修改该策略。自定义地址兼容性需通过实际协议测试，不能仅凭端点字段存在就宣称兼容。

## 接口建议

- POST /api/model-configs：创建个人配置，返回脱敏元数据。
- GET /api/model-configs：仅查询当前用户配置。
- PATCH /api/model-configs/{id}：带 revision 修改，Key 未提交时保持原值，不能用掩码字符串覆盖密钥。
- POST /api/model-configs/{id}/test：明确用户触发，限制频率、超时和最大输出，返回能力及错误。
- PUT /api/model-configs/default：指定当前用户有效配置。
- DELETE /api/model-configs/{id}：禁用并删除凭证，保留作业所需的非敏感历史信息；默认配置失效时提示重新选择。

测试覆盖：用户隔离、日志脱敏、模型服务错误、配置版本竞争、删除后禁止重试、自定义地址限制及本地模型允许列表。供应商费用与实际网络访问在用户选择后验证，未执行任何真实调用。
