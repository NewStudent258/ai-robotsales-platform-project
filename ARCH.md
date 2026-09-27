# ARCH：AI 机器人销售平台 MVP 架构

## 0. 当前实现基线

- 后端语言：Python 3.12；Web 框架：FastAPI；数据校验：Pydantic v2。
- 数据访问：SQLAlchemy 2.x Async + `asyncmy`；主数据库：MySQL 8.0+；迁移：Alembic。
- 缓存、限流和幂等辅助：Redis；异步任务可使用 Celery/Arq；前端通过 REST + SSE/WebSocket 访问 API。
- Agent Provider 通过 `app/agents/` 抽象；默认使用 Mock Provider，生产环境替换为真实 LLM 适配器。
- MySQL 保存产品、价格、报价、订单和审计事实；语义检索索引可独立部署，不能替代 MySQL 主数据。

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
- `POST /orders`、`GET /orders/{id}`、`POST /orders/{id}/cancel`；
- `GET/POST /admin/assets/{type}`、`POST /admin/assets/{type}/{id}/publish`、`GET /admin/audit-events`。

写 API 必须鉴权、校验租户和版本、支持幂等键，并返回资源版本与 trace_id。API Schema、错误码、分页、排序和兼容策略纳入契约测试；破坏性变更升级主版本。

## 7. 安全

采用 OIDC/OAuth2 登录、短时令牌和服务间 mTLS；RBAC 配合租户/对象级 ABAC。敏感字段传输加密、存储加密并分级脱敏；密钥进入专用密钥管理，不进代码、提示词或日志。对话输入做提示注入与恶意内容检测，工具调用使用 allowlist、参数校验和额度限制。后台发布、折扣、取消订单等高风险操作需二次确认或审批。定义数据最小化、留存、导出和删除流程，定期做越权、依赖和备份恢复演练。

## 8. 可观测性与运行要求

所有请求和 Agent 步骤传递 `trace_id`、`span_id`、`tenant_id`、会话/报价/订单 ID。记录结构化日志、指标和分布式追踪，但日志只保留脱敏摘要。核心指标包括 Agent 成功率/转人工率、检索命中率、工具错误率、价格计算延迟、报价确认率、订单创建成功率、幂等命中率、状态迁移失败数、P50/P95 延迟、token/成本和队列积压。

设置规则冲突、非法迁移、敏感信息告警、错误率和延迟阈值；告警关联 runbook、责任人和升级链路。关键服务具备超时、熔断、限流、降级、备份、恢复点目标（RPO）和恢复时间目标（RTO）记录，发布采用灰度、回滚和资产版本冻结。
