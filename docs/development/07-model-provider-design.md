# 用户模型接入设计 v0.1

依据：用户已确认开源定位、个人模型配置及同时兼容 OpenAI/Anthropic SDK；自研工作流保持不变。采用官方 Python openai 与 anthropic 包，具体版本实施前锁定。兼容范围是本产品的模型调用能力，不是实现一个对外兼容所有 SDK 端点的代理服务。

## 数据与调用

### 协议与能力契约（建议）

首版双 SDK 已确认。建议内部划分 openai_responses、openai_chat_completions、anthropic_messages 三个适配路径，分别调用对应 SDK 的原生接口。供应商预设是配置数据，协议适配器是执行代码；禁止根据模型名称猜测协议或失败后静默改用另一个端点。API 子集为设计建议，当前未运行真实调用。

统一输入包含指令、规范化消息、输出 schema、超时和输出限额；适配器分别映射消息/内容块、结束原因、用量和流事件。只转发模型明确支持的参数。统一输出保留文本、结构化候选、标准化错误及原始原因代码；不将推理内容或工具块误作为用户可见回答。业务规则校验对两条 SDK 路径相同。

SDK 内置重试与 Celery 重试使用统一调用预算，避免乘法放大；流中断后不直接把重试文本拼到半截回答上，最终消息单独原子发布。SDK 客户端按配置版本隔离，不通过全局环境变量切换不同用户的密钥。

能力采用 supported/unsupported/unknown，并保存测试时间和配置版本。基本生成是最低门槛；原生 schema 不可用时可有限解析加规则校验，不伪报原生能力。流式不可用时显示等待后完整返回；工具调用和向量不是首版规划必需能力。

测试使用固定无私人内容的短提示，限制输出、重试和超时；分别展示鉴权/权限、路径/模型、限流、网络和响应协议错误，脱敏供应商错误。模型列表失败不能直接判定生成不可用。

作业创建时绑定配置 ID 和 revision。更换默认值只影响新作业；旧配置仍有效时原作业沿用绑定，修改/撤销配置则按下述规则停止。用户确认后恢复属于新运行，可绑定当前默认模型，并记录选择和重新校验业务版本。

验收分别覆盖 OpenAI SDK 和 Anthropic SDK 的非流式、流式、错误、用量与结构化输出路径；再验证自定义兼容服务和本地服务。包含无鉴权、缺失流式、非法 JSON、未知用量、配置变更及出站限制。不能以 OpenAI 路径通过代替 Anthropic 路径验证。

官方核对资料（2026-09-20）：[DeepSeek 接入](https://api-docs.deepseek.com/zh-cn/)、[Ollama 部分接口兼容](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx)、[Claude 原生 API](https://platform.claude.com/docs/en/api/overview)。官方说明不等于目标模型或部署环境已实测。

### 凭证与运行快照

model_configs 增加 sdk_type 与 api_mode，代替含糊的单一 protocol 字段；检查合法组合，修改 API 模式视为配置版本变化。官方 SDK 依据：[OpenAI SDK](https://developers.openai.com/api/docs/libraries)、[Anthropic Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python)。

建议增加 model_configs（id、owner_id、name、protocol、endpoint、model_id、credential_ciphertext、encryption_key_version、revision、enabled、capabilities_json、last_test_at）及用户默认配置引用。服务端密钥加密主密钥由部署环境提供，不与密文放在同一业务表；禁止明文进入日志、作业载荷、导出样例或前端读接口。

作业保存配置 ID、版本和非敏感模型快照，不保存明文 Key。Worker 按 owner_id 读取配置；执行前校验是否有效。排队期间配置被改动则停止并要求重新发起，避免静默切换供应商；已发出的请求不能通过删除配置收回，但后续重试必须检查禁用/删除状态。

模型调用 Interface 接收服务端解析后的配置、必要上下文和输出 schema，返回内容、用量、错误及能力信息。协议适配器处理供应商差异，不把请求路径、鉴权头和特殊参数散落到业务模块。用户输入的 URL 或模型名不能变成可执行代码。

## 自定义地址

多用户托管时，自定义服务地址需做服务端出站访问控制：限制协议、端口和地址范围，校验 DNS 解析及重定向，阻止访问云元数据与未授权内网，禁止把凭证转发到未批准的重定向目标。

自部署访问本地模型是合理需求，使用实例管理员设置的明确允许列表开放本地/内网端点；普通用户不能修改该策略。自定义地址兼容性需通过实际协议测试，不能仅凭端点字段存在就宣称兼容。

## 接口建议

- POST /api/model-configs：创建个人配置，返回脱敏元数据。
- GET /api/model-configs：仅查询当前用户配置。
- PATCH /api/model-configs/{id}：带 revision 修改，Key 未提交时保持原值，不能用掩码字符串覆盖密钥。
- POST /api/model-configs/{id}/test：明确用户触发，限制频率、超时和最大输出，返回能力及错误。
- PUT /api/model-configs/default：指定当前用户有效配置。
- DELETE /api/model-configs/{id}：禁用并删除凭证，保留作业所需的非敏感历史信息；默认配置失效时提示重新选择。

测试覆盖：用户隔离、日志脱敏、模型服务错误、配置版本竞争、删除后禁止重试、自定义地址限制及本地模型允许列表。供应商费用与实际网络访问在用户选择后验证，未执行任何真实调用。
