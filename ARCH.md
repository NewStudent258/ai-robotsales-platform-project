# ARCH：AI 机器人销售平台 MVP 架构

## 0. 当前实现基线

- 后端语言：Python 3.12；Web 框架：FastAPI；数据校验：Pydantic v2。
- 数据访问：SQLAlchemy 2.x Async + `asyncmy`；主数据库：MySQL 8.0+；迁移：Alembic。
- 缓存、限流和幂等辅助：Redis；异步任务可使用 Celery/Arq；前端通过 REST + SSE/WebSocket 访问 API。
- Agent Provider 通过 `app/agents/` 抽象；默认使用 Mock Provider，生产环境替换为真实 LLM 适配器。
- MySQL 保存产品、价格、报价、订单和审计事实；语义检索索引可独立部署，不能替代 MySQL 主数据。
- 当前客户路径：产品目录→选择产品和数量→服务端报价→客户确认→幂等创建订单；前端直接使用同源 `/api/v1` 接口。
- `/sales` 由 FastAPI 提供独立销售页；商品清单从 `GET /api/v1/products` 获取，浏览器端搜索/筛选/排序及报价单仅是展示与选品状态。报价单只在浏览器 `localStorage` 保存商品 ID 和数量；联系人与访问令牌不持久化。浏览器端预估小计不是报价事实，多商品正式金额由 `POST /api/v1/quotes` 决定。
- 当前价格策略为 `base-price-v1`：产品基础价乘数量，税额为 0；客户端税率字段被拒绝，不同币种不能合并报价。税费、折扣和交付费用规则尚未实现。
- **Agent 编排已接入（P0）**：`app/services/assistant_service.py` 为 Provider 中立的编排器，负责多轮上下文、工具循环、步数/工具预算、审计与转人工；`app/agents/provider.py` 定义 `AgentProvider` 协议、`ToolRegistry` 白名单与 `AgentTurn` 决策结构。当前默认实现为 `MockAgentProvider`（`app/agents/mock_provider.py`），是**确定性实现**，真实 LLM 适配器只需实现同一协议，业务链路无需改动。
- **会话与需求已持久化**：`conversations` 表保存 `session_id`、结构化需求快照、需求版本与转人工标记；`conversation_messages` 保存 user/assistant 消息、意图、置信度与工具轨迹。`session_id` 由服务端生成并在后续轮次复用，不再是一次性返回值。
- **工具白名单**：`search_products`（只读）、`create_quote`（写本地，复用 `CommerceService`，金额服务端复算）、`prepare_order`（`write_commit`，**只准备动作、不创建订单**）。未注册工具名一律返回 `TOOL_NOT_ALLOWED`；单轮受 `MAX_AGENT_STEPS`/`MAX_TOOL_CALLS` 约束，超限安全停止并转人工。下单必须由客户在页面勾选条款后提交，Agent 无自主下单能力。
- **报价令牌按需签发**：`POST /api/v1/assistant/quote-token` 在客户点击确认时才签发令牌，且仅当该报价确由本会话生成（依据助手消息的工具轨迹中的 `quote_id`）才返回，否则 404。令牌不随对话响应回传，也不会写入审计轨迹。
- **需求抽取与注入防护为确定性实现**：`app/agents/requirement.py` 的 `RequirementExtractor` 做场景/数量/预算抽取与冲突检测，`detect_injection` 标记提示注入。二者均不依赖模型，可被单元测试稳定回归；命中注入时只披露与拒绝，不改写任何金额或状态。
- 新建报价生成 64 位十六进制随机访问令牌。读取/确认报价、创建/读取订单须通过 `X-Quote-Token` 提交该令牌；它不是用户身份认证，正式多租户鉴权仍需补齐。访问令牌**仅在创建报价的响应中返回一次**，`GET /quotes/{id}` 与确认响应不再回显（`access_token: null`）；本地调试可用 `EXPOSE_QUOTE_TOKEN=true` 临时恢复回显。
- 报价过期在确认和创建订单两条路径上都会校验：命中过期时报价落为 `EXPIRED` 并返回 `QUOTE_EXPIRED`。报价表使用 `ORDER_CREATED` 作为“已建单”状态，该状态仅表示订单已生成，不参与订单状态机。
- 幂等键采用“先抢占后执行”：抢占在独立事务中提交，并发同键请求由唯一约束裁决，落败方等待并回放胜出方的结果，因此不会重复建单也不会返回 5xx。键被复用于不同报价时返回 `IDEMPOTENCY_KEY_REUSED`。
- 订单状态机实现见 `app/services/commerce_service.py` 的 `ORDER_TRANSITIONS`，与 §3 表格一致，`COMPLETED`、`CANCELLED`、`EXPIRED` 为终态（出度为空集）。
- 全部错误响应统一为 `{data: null, error: {code, message, retryable, handoff_required}, trace_id}`，由 `app/core/errors.py` 统一注入；每个请求都返回 `X-Trace-Id` 响应头，合法的上游 trace_id 会被沿用。参数校验错误额外给出 `error.fields`。
- `POST /products` 需运营后台令牌 `X-Admin-Token`（配置项 `ADMIN_API_TOKEN`）。未配置令牌时一律拒绝，不再依赖 `DEBUG` 开关；正式 RBAC/OIDC 见 §7。
- 已补充 GitHub Actions 门禁（`.github/workflows/ci.yml`）：`ruff check`、`ruff format --check`、迁移可用性、种子脚本与 `pytest`。

## 1. 架构原则与模块边界

采用“体验层—Agent 编排层—领域服务层—资产与基础设施层”分层。Agent 负责理解、检索、推荐和解释；确定性服务负责金额、资格、权限、订单状态和副作用。

| 模块 | 输入 | 输出 | 责任边界 |
|---|---|---|---|
| 客户体验层 | 表单、对话、确认操作 | 页面状态、结构化提交、展示文案 | 不计算价格、不直接写订单 |
| 运营后台 | 线索、审核、资产编辑 | 审核决定、发布命令、人工接管 | 不绕过领域服务写核心表 |
| Agent 编排器 | 会话上下文、用户意图、策略 | Skill 调用计划、中间结果、解释 | 路由/预算/超时/审计，不持有业务规则 |
| Skill 运行时 | Schema 输入、版本资产 | Schema 输出、引用、错误 | 只调用白名单工具，禁止隐式副作用 |
| 需求服务 | 结构化需求、会话 | 需求版本、缺口、冲突 | 校验与版本化需求 |
| 目录/知识服务 | 查询、租户、版本 | 产品、能力、引用证据 | 提供可追溯事实 |
| 价格服务 | 产品、数量、周期、客户资格、规则版本 | 报价明细、金额、有效期、版本 | 唯一价格真源，纯计算优先 |
| 报价服务 | 需求、方案、价格结果 | 报价版本、快照、确认资格 | 管理报价生命周期 |
| 订单服务 | 已确认报价、客户、幂等键 | 订单、状态迁移、领域事件 | 唯一订单写入和状态真源 |
| 资产管理服务 | 知识/模板/样本/规则草稿 | 审核、发布、版本 | 发布门禁和回滚 |
| 审计与可观测性 | 请求、工具、领域事件 | 审计记录、指标、追踪 | 不修改业务事实 |

Agent 与价格/订单服务通过版本化 API 隔离。Agent 不得接受“自算金额”“直接改状态”或未声明字段；所有写入需由服务重新鉴权、校验和幂等处理。

## 2. S1-S9 Skill

| Skill | 输入 | 输出 | 失败策略 |
|---|---|---|---|
| S1 需求采集 | 用户文本、表单草稿 | `RequirementProfile`、缺口、冲突 | 追问；超过轮次转人工 |
| S2 机器人选型 | `RequirementProfile`、目录/知识版本 | 候选方案、匹配理由、限制、引用 | 无匹配返回原因和替代路径 |
| S3 报价编排 | 方案、数量、周期、客户资格 | `QuoteDraft`（由价格服务生成金额） | 规则冲突/缺资格阻断 |
| S4 报价确认 | 报价 ID/版本、客户确认、条款 | `QuoteConfirmed` | 过期、版本不一致需刷新 |
| S5 创建订单 | 已确认报价快照、客户、幂等键 | 订单摘要与订单号 | 重试返回原订单；失败转人工 |
| S6 人工接管 | 异常上下文、建议动作 | 接管任务、备注、恢复动作 | 权限不足拒绝并告警 |
| S7 知识检索 | 查询、租户、过滤条件 | 带来源和版本的证据片段 | 无命中不得编造，要求补充 |
| S8 规则解释 | 规则结果、输入快照 | 面向用户的解释和审批原因 | 仅解释，不改变规则结果 |
| S9 质检与回归 | 会话/事件、评测集 | 评分、风险标签、回归报告 | 严重风险阻止发布 |

所有 Skill 均遵守 AGENTS.md 的结构化 Schema、版本、工具白名单、预算、审计和失败规范。

## 3. 订单有限状态机

### 状态

`DRAFT` 草稿、`PENDING_CONFIRMATION` 待确认、`CONFIRMED` 已确认、`CREATING` 创建中、`CREATED` 已创建、`PROCESSING` 处理中、`COMPLETED` 已完成、`CANCELLED` 已取消、`EXPIRED` 已过期、`FAILED` 创建失败。

### 合法迁移

| 当前状态 | 允许迁移 | 触发 |
|---|---|---|
| DRAFT | PENDING_CONFIRMATION、EXPIRED | 报价生成/有效期到期 |
| PENDING_CONFIRMATION | CONFIRMED、EXPIRED、DRAFT | 客户确认/到期/修改报价 |
| CONFIRMED | CREATING、CANCELLED | 创建请求/有权取消 |
| CREATING | CREATED、FAILED | 外部创建成功/失败 |
| CREATED | PROCESSING、CANCELLED | 履约开始/规则允许取消 |
| PROCESSING | COMPLETED、FAILED、CANCELLED | 履约结果/异常/有权取消 |
| FAILED | CREATING、CANCELLED | 幂等重试/终止 |
| COMPLETED、CANCELLED、EXPIRED | 无 | 终态 |

非法迁移返回 `ORDER_INVALID_TRANSITION`，不得通过 Agent 绕过。每次迁移使用 compare-and-set 或数据库锁，写入前后状态、操作者、原因、报价版本和事件 ID。

## 4. 幂等、事件审计与异常处理

- 所有写 API 要求 `Idempotency-Key`；键按租户、操作类型和业务主体唯一，保存请求摘要、响应和过期时间。
- 价格计算使用输入快照和规则版本，可重复得到同一结果；报价确认绑定报价版本和哈希。
- 订单创建采用“幂等记录→状态迁移→领域事件→异步副作用”顺序；事件投递使用 outbox，消费者按事件 ID 去重。
- 网络超时只允许安全重试；未知结果先查询幂等记录，禁止盲目再次创建。
- 错误分为可重试（限流、暂时不可用）、需补充（缺字段）、需人工（规则冲突/高风险）和不可恢复（权限/数据损坏），统一返回 `code`、`message`、`retryable`、`trace_id`。
- 事件审计至少记录租户、主体、操作者/Agent、Skill 与模型版本、资产版本、输入摘要、输出摘要、时间、IP/设备摘要、结果和关联 ID；敏感值脱敏或哈希。

## 5. 数据模型（核心字段）

| 实体 | 关键字段 |
|---|---|
| Tenant/User/Role | `tenant_id`、主体 ID、角色、权限版本、状态 |
| Conversation | `conversation_id`、客户 ID、渠道、状态、trace、摘要、保留期 |
| ConversationMessage | 会话 ID、角色、内容、意图、置信度、工具轨迹、trace |
| RequirementVersion | `requirement_id`、版本、结构化字段、缺口、来源、创建人 |
| Product/Capability | 产品 ID、能力、约束、目录版本、有效期、租户 |
| AssetVersion | 类型、内容/Schema、版本、状态、来源、审核人、生效时间 |
| Quote | 报价 ID、版本、需求/方案快照、金额明细、币种、税、有效期、规则版本、状态、哈希 |
| Order | 订单 ID、客户、确认报价快照、金额、状态、幂等键、创建时间 |
| OrderEvent | 事件 ID、聚合 ID、序号、前后状态、操作者、原因、时间、载荷摘要 |
| IdempotencyRecord | 租户、键、请求哈希、状态、响应、过期时间 |
| AuditLog | trace、主体、动作、资源、结果、策略版本、脱敏详情 |

金额使用最小货币单位整数和明确币种；时间统一 UTC 存储、界面按时区展示；软删除和版本快照满足审计与恢复。

## 6. API 契约

外部 API 使用 `/api/v1`，JSON 编码，统一响应 `{data, error, trace_id}`。核心端点包括：

- `POST /conversations`、`POST /requirements/validate`、`GET /products/recommendations`；
- `POST /quotes/preview`、`POST /quotes`、`POST /quotes/{id}/confirm`、`GET /quotes/{id}`；
- `POST /orders`、`GET /orders/{id}`、`POST /orders/{id}/transition`；
- `GET/POST /admin/assets/{type}`、`POST /admin/assets/{type}/{id}/publish`、`GET /admin/audit-events`。

**已实现的 Agent 端点**：`POST /assistant/messages` 为多轮对话入口，返回 `intent`、`answer`、`missing_fields`、`recommendations`、`quote`、`pending_action`、`handoff_required` 与 `requirement` 快照；`POST /assistant/quote-token` 按需签发报价访问令牌（`{session_id, quote_id}` → `{quote_id, access_token}`）。`POST /conversations`、`POST /requirements/validate` 尚未单独提供——会话由 `assistant/messages` 隐式创建，需求校验内联在编排器中。

写 API 必须鉴权、校验租户和版本、支持幂等键，并返回资源版本与 trace_id。API Schema、错误码、分页、排序和兼容策略纳入契约测试；破坏性变更升级主版本。

**状态迁移端点的实现说明**：文档原列的 `POST /orders/{id}/cancel` 被实现为更通用的 `POST /orders/{id}/transition`，由状态机裁决目标状态是否合法，取消即 `to_status=CANCELLED`。这样避免为每个目标状态各开一个端点——否则 `PROCESSING`、`COMPLETED`、`FAILED` 等迁移都会缺路由，正是此前状态机未接线的原因。该端点属高风险写操作，需 `X-Admin-Token`；请求体支持 `to_status`、可选 `reason` 与可选 `expected_status`（乐观并发保护，与库中状态不符时返回 `ORDER_STATUS_CONFLICT`）。响应中的 `allowed_transitions` 给出当前状态的合法目标，便于运营端与测试发现可用动作。每次迁移写入 `order_events`，记录前后状态、原因与操作者。

## 7. 安全

采用 OIDC/OAuth2 登录、短时令牌和服务间 mTLS；RBAC 配合租户/对象级 ABAC。敏感字段传输加密、存储加密并分级脱敏；密钥进入专用密钥管理，不进代码、提示词或日志。对话输入做提示注入与恶意内容检测，工具调用使用 allowlist、参数校验和额度限制。后台发布、折扣、取消订单等高风险操作需二次确认或审批。定义数据最小化、留存、导出和删除流程，定期做越权、依赖和备份恢复演练。

## 8. 可观测性与运行要求

所有请求和 Agent 步骤传递 `trace_id`、`span_id`、`tenant_id`、会话/报价/订单 ID。记录结构化日志、指标和分布式追踪，但日志只保留脱敏摘要。核心指标包括 Agent 成功率/转人工率、检索命中率、工具错误率、价格计算延迟、报价确认率、订单创建成功率、幂等命中率、状态迁移失败数、P50/P95 延迟、token/成本和队列积压。

设置规则冲突、非法迁移、敏感信息告警、错误率和延迟阈值；告警关联 runbook、责任人和升级链路。关键服务具备超时、熔断、限流、降级、备份、恢复点目标（RPO）和恢复时间目标（RTO）记录，发布采用灰度、回滚和资产版本冻结。
