# TEST.md：测试与验收规范

> 本文档定义 AI 机器人销售平台的测试策略、用例清单、执行方式与验收标准。
> 遵循 `AGENTS.md` 第 6 节「测试策略」与第 7 节「Definition of Done」。
>
> **文档版本**：v1.0.0 ｜ **生效日期**：2026-09-30 ｜ **负责人**：NewStudent258
> **适用范围**：`app/`（后端）、`frontend/`（前端）、`alembic/`（迁移）、`scripts/`（运维脚本）

---

## 1. 文档目的与非目标

### 1.1 目的

1. 明确每个测试层级的**职责边界**，避免重复测试与遗漏测试。
2. 提供**可复现的执行命令**与**可核对的验收标准**。
3. 记录**当前真实覆盖率与缺口**，作为后续迭代的输入基线。
4. 定义缺陷的**分级标准**与**放行条件**。

### 1.2 非目标

- 本文档**不替代** `PRD.md`（产品范围）与 `ARCH.md`（架构契约）。
- 本文档**不包含**性能压测的具体脚本实现，仅定义指标与判定阈值。
- 本文档**不承诺**当前已实现全部用例；第 8 节明确列出未覆盖项。

---

## 2. 测试环境与前置条件

### 2.1 环境要求

| 项目 | 版本要求 | 当前验证值 | 说明 |
|---|---|---|---|
| Python | >= 3.12 | 3.12.9 ✅ | `pyproject.toml` 声明 |
| pytest | >= 8.3, < 9.0 | 8.4.2 ✅ | 见 `[project.optional-dependencies].dev` |
| pytest-asyncio | >= 0.24, < 1.0 | 0.26.0 ✅ | `asyncio_mode = "auto"` |
| ruff | >= 0.8, < 1.0 | 0.16.9 | 静态检查 |
| anyio | — | 4.15.1 ✅ | httpx ASGI 传输依赖 |

### 2.2 测试数据库策略

测试**不依赖**外部 MySQL，采用内存 SQLite 隔离运行，见 `tests/conftest.py`：

```python
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["AUTO_CREATE_TABLES"] = "false"
os.environ["DEBUG"] = "true"
```

**关键设计**：

- `AUTO_CREATE_TABLES = "false"` — 禁用应用 lifespan 自动建表，避免污染测试库。
- 建表由 fixture 内 `Base.metadata.create_all` 显式完成，**每个用例独立 engine**。
- `ADMIN_API_TOKEN = "test-admin-token"` — 开放 `POST /api/v1/products`（用例以 `X-Admin-Token` 提交该令牌）。
- `DEBUG = "true"` — 仅影响 SQL 回显等调试行为，**不再用于写接口鉴权**（见 6.4）。
- 通过 `app.dependency_overrides[get_db]` 注入测试会话，**不触碰真实数据库**。

> ⚠️ **隔离性说明**：`sqlite+aiosqlite:///:memory:` 配合独立 engine，保证用例间数据不串扰。但由此也带来**方言差异**——SQLite 与 MySQL 在 `Numeric`、行级锁（`with_for_update`）、并发语义上行为不同，见第 8.2 节缺口。

### 2.3 初始化依赖

```bash
pip install -e ".[dev]"
```

---

## 3. 测试分层与职责

| 层级 | 目录 | 职责 | 当前状态 |
|---|---|---|---|
| 静态检查 | `ruff check .` | 导入排序、未使用导入、类型注解风格 | ✅ 全部通过 |
| 单元测试 | `tests/test_requirement.py` | 需求抽取、缺口、冲突、注入防护 | ✅ 已覆盖 |
| 契约测试 | `tests/test_api.py`、`tests/test_contract.py` | API 输入输出、错误码、Schema 兼容 | ✅ 已覆盖 |
| 集成测试 | `tests/test_api.py` | 选型→报价→确认→下单全链路 | ✅ 已覆盖 |
| Agent 编排测试 | `tests/test_agent_orchestration.py` | 多轮上下文、工具白名单、下单边界、令牌签发 | ✅ 已覆盖 |
| 前端测试 | `tests/test_frontend.py` | 页面可达性、静态资源、XSS/令牌断言 | ✅ 已覆盖 |
| 评测测试 | 未建立 | LLM 意图识别、多轮追问、注入攻击 | ⚠️ 部分覆盖（注入防护已确定性覆盖，真实 LLM 接入后需补回归集） |
| 非功能测试 | 未建立 | P95 延迟、并发、限流 | ❌ 未覆盖 |

---

## 4. 业务流与测试映射

### 4.1 MVP 主链路

```
客户对话  →  Agent 多轮追问  →  工具选型  →  Agent 生成报价  →  客户确认  →  创建订单
POST /assistant/messages   search_products  create_quote   POST /assistant/quote-token
                                                → POST /quotes/{id}/confirm → POST /orders
```

> 传统表单路径仍然可用：`/sales` → `GET /products` → `POST /quotes` → 确认 → 下单。

### 4.2 报价状态机（实际实现）

`app/models/commerce.py` 中 `Quote.status` 的迁移路径：

```
PENDING_CONFIRMATION ──confirm──> CONFIRMED ──create_order──> ORDER_CREATED
        │
        └──expires_at 超时──> EXPIRED
```

| 当前状态 | 触发动作 | 目标状态 | 校验条件 | 失败错误码 |
|---|---|---|---|---|
| `PENDING_CONFIRMATION` | `confirm_quote` | `CONFIRMED` | `version` 匹配 | `QUOTE_VERSION_CONFLICT` (409) |
| `PENDING_CONFIRMATION` | `confirm_quote` | `EXPIRED` | 超 `expires_at` | `QUOTE_EXPIRED` (409) |
| `CONFIRMED` | `create_order` | `ORDER_CREATED` | 幂等键抢占成功且未过期 | `IDEMPOTENCY_KEY_REUSED` (409) |
| `CONFIRMED` | `create_order` | `EXPIRED` | 超 `expires_at` | `QUOTE_EXPIRED` (409) |
| 任意 | `create_order` | — | 状态不属于 `{CONFIRMED, ORDER_CREATED}` | `QUOTE_NOT_CONFIRMED` (409) |

> 报价过期在确认与下单两条路径都会校验（`TEST.md` 早期版本仅记录确认路径，下单路径漏检已修复）。

### 4.3 订单状态机（实际实现）

`ORDER_TRANSITIONS`（`app/services/commerce_service.py`）：

```python
ORDER_TRANSITIONS = {
    "DRAFT": {"PENDING_CONFIRMATION", "EXPIRED"},
    "PENDING_CONFIRMATION": {"CONFIRMED", "EXPIRED", "DRAFT"},
    "CONFIRMED": {"CREATING", "CANCELLED"},
    "CREATING": {"CREATED", "FAILED", "CANCELLED"},
    "CREATED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"COMPLETED", "FAILED", "CANCELLED"},
    "FAILED": {"CREATING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "EXPIRED": set(),
}
```

> **实现观察**：`create_order` 在同一事务内完成 `CREATING → CREATED`，并向 `order_events` 写入两条事件（`None→CREATING`、`CREATING→CREATED`）。
> 终态（`COMPLETED`/`CANCELLED`/`EXPIRED`）必须显式声明为空集：若从字典中省略，`ORDER_TRANSITIONS.get(status, set())` 会静默回退为空集，使 `COMPLETED` 既不可达也无法迁出。
> `transition_order` 已通过 `POST /orders/{id}/transition` 接线（需 `X-Admin-Token`），支持 `reason` 与 `expected_status`，并在响应中返回 `allowed_transitions`。取消订单即 `to_status=CANCELLED`。

---

## 5. 测试用例清单

### 5.1 契约与集成测试（`tests/test_api.py`）

| ID | 用例 | 验证点 | 状态 |
|---|---|---|---|
| T-API-01 | `test_health` | `/health` 返回 200 且 `status == "ok"` | ✅ PASSED |
| T-API-02 | `test_product_search_and_assistant` | 关键词检索命中 1 条；`/assistant/messages` 推荐首位命中该产品 | ✅ PASSED |
| T-API-03 | `test_quote_confirm_and_idempotent_order` | 报价 `2 × 1000.00 = 2000.00`；确认后状态 `CONFIRMED`；同幂等键两次下单返回**同一订单 ID** | ✅ PASSED |
| T-API-04 | `test_quote_with_multiple_products_uses_server_prices` | 多产品合计 `2×1000 + 3×2500 = 9500.00`，价格**取自服务端**而非客户端 | ✅ PASSED |
| T-API-05 | `test_quote_rejects_client_tax_and_mixed_currency` | 客户端传 `tax_rate` → 422（`extra="forbid"`）；混合币种 → 422 `MIXED_CURRENCY` | ✅ PASSED |
| T-API-06 | `test_idempotency_key_cannot_be_reused_for_another_quote` | 同一幂等键跨不同报价复用 → 409 `IDEMPOTENCY_KEY_REUSED` | ✅ PASSED |
| T-API-07 | `test_quote_and_order_require_matching_access_token` | 缺 token → 422；错 token → 404（不泄露存在性）；正确 token → 200/201 | ✅ PASSED |

### 5.2 契约测试（`tests/test_contract.py`）

| ID | 用例 | 验证点 | 状态 |
|---|---|---|---|
| T-CT-01 | `test_error_envelope_shape` | 错误体为 `{data:null, error:{code,message,retryable}, trace_id}` | ✅ PASSED |
| T-CT-02 | `test_validation_error_uses_envelope` | 422 走统一封套并给出 `error.fields` | ✅ PASSED |
| T-CT-03 | `test_documented_error_codes_have_messages` | 常见错误码均有中文文案 | ✅ PASSED |
| T-CT-04 | `test_trace_id_returned_and_echoed` | 响应头回传，且合法上游 trace_id 被沿用 | ✅ PASSED |
| T-CT-05 | `test_trace_id_rejects_injected_value` | 非法 trace_id 不被透传（防日志注入） | ✅ PASSED |
| T-CT-06 | `test_trace_id_present_on_error` | 错误响应 body 与响应头 trace_id 一致 | ✅ PASSED |
| T-CT-07 | `test_product_write_requires_admin_token` | 缺令牌 → 403 `ADMIN_AUTH_REQUIRED` | ✅ PASSED |
| T-CT-08 | `test_product_write_rejects_wrong_admin_token` | 错令牌 → 401 `ADMIN_AUTH_INVALID` | ✅ PASSED |
| T-CT-09 | `test_product_write_succeeds_with_admin_token` | 正确令牌可写入 | ✅ PASSED |
| T-CT-10 | `test_quote_token_returned_once_and_not_on_read` | 创建返回令牌；读取/确认 `access_token: null` | ✅ PASSED |
| T-CT-11 | `test_all_routes_have_error_handlers_registered` | 异常处理器已挂载，封套不会退化 | ✅ PASSED |

### 5.3 订单完整性回归（`tests/test_order_integrity.py`）

| ID | 用例 | 验证点 | 状态 |
|---|---|---|---|
| T-OI-01 | `test_expired_confirmed_quote_cannot_create_order` | 过期报价下单 → 409 `QUOTE_EXPIRED`，报价落 `EXPIRED`，且**不产生订单** | ✅ PASSED |
| T-OI-02 | `test_concurrent_same_key_creates_single_order` | 同键 6 并发 → 全部 201 且**仅 1 个订单**（文件型 SQLite + 连接池） | ✅ PASSED |
| T-OI-03 | `test_sequential_replay_returns_same_order` | 顺序重放返回同一订单 | ✅ PASSED |
| T-OI-04 | `test_order_state_machine_matches_arch` | `PROCESSING → COMPLETED` 可达，终态守卫返回 `ORDER_INVALID_TRANSITION` | ✅ PASSED |
| T-OI-05 | `test_order_transitions_declare_terminal_states` | 三个终态显式声明为空集 | ✅ PASSED |
| T-OI-06 | `test_quote_total_matches_server_price` | 多商品金额由服务端计算 | ✅ PASSED |

### 5.4 订单状态机与报价乐观锁（`tests/test_order_state_machine.py`）

| ID | 用例 | 验证点 | 状态 |
|---|---|---|---|
| T-SM-01 | `test_transition_requires_admin_token` | 迁移需 `X-Admin-Token`，缺令牌拒绝 | ✅ PASSED |
| T-SM-02 | `test_transition_route_exists` | 路由已挂载（G-01 核心） | ✅ PASSED |
| T-SM-03 | `test_transition_unknown_order_returns_404` | 未知订单 404 `ORDER_NOT_FOUND` | ✅ PASSED |
| T-SM-04 | `test_create_order_exposes_allowed_transitions` | 响应给出 `allowed_transitions` | ✅ PASSED |
| T-SM-05 | `test_full_happy_path_to_completed` | `CREATED→PROCESSING→COMPLETED`，终态 `allowed_transitions` 为空 | ✅ PASSED |
| T-SM-06 | `test_cancel_from_created` | `CREATED→CANCELLED` 合法 | ✅ PASSED |
| T-SM-07 | `test_failed_then_retry_cycle` | `PROCESSING→FAILED→CREATING` 重试路径合法 | ✅ PASSED |
| T-SM-08 | `test_invalid_transitions_rejected`（8 组参数化） | 越级、回退、终态迁出等一律 409 `ORDER_INVALID_TRANSITION` | ✅ PASSED |
| T-SM-09 | `test_unknown_target_status_rejected` | 未知目标状态按非法迁移处理 | ✅ PASSED |
| T-SM-10 | `test_transition_rejects_extra_fields` | 未声明字段 → 422 `VALIDATION_ERROR` | ✅ PASSED |
| T-SM-11 | `test_expected_status_conflict` | 并发保护：`expected_status` 不符 → 409 `ORDER_STATUS_CONFLICT` | ✅ PASSED |
| T-SM-12 | `test_transition_writes_audit_event` | 事件记录前后状态、原因、操作者 `admin` | ✅ PASSED |
| T-SM-13 | `test_service_transition_rejects_invalid_without_api` | 服务层同样受状态机约束，无法绕过 API | ✅ PASSED |
| T-VC-01 | `test_confirm_with_wrong_version_conflicts` | **G-03**：`version=99` → 409 `QUOTE_VERSION_CONFLICT`，报价保持未确认 | ✅ PASSED |
| T-VC-02 | `test_confirm_twice_conflicts` | 重复确认 → 409，不重复生效 | ✅ PASSED |
| T-VC-03 | `test_confirm_requires_matching_access_token` | 版本正确但令牌错误 → 404（不泄露存在性） | ✅ PASSED |
| T-VC-04 | `test_confirm_version_must_be_integer` | `version=abc` → 422 | ✅ PASSED |
| T-VC-05 | `test_confirm_expired_wins_over_version` | 过期优先于版本校验 → 409 `QUOTE_EXPIRED` | ✅ PASSED |

### 5.5 前端测试（`tests/test_frontend.py`）

| ID | 用例 | 验证点 | 状态 |
|---|---|---|---|
| T-FE-01 | `test_frontend_home_and_static_assets` | `/` 含 `ROBOTIQ`；静态资源可达；脚本中出现 `/api/v1/assistant/messages`、`/api/v1/assistant/quote-token`、`ensureQuoteToken`、`reviewAssistantQuote`、`/api/v1/quotes`、`Idempotency-Key`；`/sales` 含 `ROBOTIQ SALES FLOOR` | ✅ PASSED |
| T-FE-02 | `test_frontend_source_files_exist` | `frontend/index.html`、`styles.css`、`app.js` 物理存在 | ✅ PASSED |

**XSS 防护断言**：`app.js` 与 `sales.js` 中**断言不出现 `innerHTML`**（`assert "innerHTML" not in script.text`），强制前端使用安全 DOM 写入 API。这是低成本、高价值的防御性回归断言。

### 5.7 Agent 编排与工具边界（`tests/test_agent_orchestration.py`）

| ID | 用例 | 验证点 | 状态 |
|---|---|---|---|
| T-AG-01 | `test_session_id_is_persisted` | `session_id` 落库（此前从不持久化） | ✅ PASSED |
| T-AG-02 | `test_messages_are_persisted` | user/assistant 消息均落库 | ✅ PASSED |
| T-AG-03 | `test_reuses_existing_session` | 复用既有会话，不新建 | ✅ PASSED |
| T-AG-04 | `test_requirement_accumulates_across_turns` | 第二轮补充预算不丢失第一轮场景 | ✅ PASSED |
| T-AG-05 | `test_asks_for_missing_fields` | 缺字段时追问而非硬编码话术 | ✅ PASSED |
| T-AG-06 | `test_no_match_hands_off_without_fabricating` | 无匹配转人工且不编造产品 | ✅ PASSED |
| T-AG-07 | `test_agent_generates_quote_and_exposes_reference` | **P0 核心**：助手产出 `quote_id` 供前端跳转确认 | ✅ PASSED |
| T-AG-08 | `test_quote_total_is_server_computed` | 金额由服务端复算（3 × 25000 = 75000） | ✅ PASSED |
| T-AG-09 | `test_agent_never_creates_order_autonomously` | **Agent 不得自主下单**，订单表为空 | ✅ PASSED |
| T-AG-10 | `test_prepare_order_reports_requires_confirmation` | `prepare_order` 属 `write_commit` 且需确认 | ✅ PASSED |
| T-AG-11 | `test_unregistered_tool_is_rejected` | 未注册工具 → `TOOL_NOT_ALLOWED` | ✅ PASSED |
| T-AG-12 | `test_default_registry_exposes_expected_tools` | 白名单恰为 3 个工具 | ✅ PASSED |
| T-AG-13 | `test_token_not_returned_in_chat_response` | 令牌不随对话响应回流 | ✅ PASSED |
| T-AG-14 | `test_token_issued_on_demand_for_own_quote` | 本会话报价可按需换取令牌 | ✅ PASSED |
| T-AG-15 | `test_token_rejected_for_foreign_session` | 伪造 session → 404（不可越权换取） | ✅ PASSED |
| T-AG-16 | `test_issued_token_can_confirm_and_order` | 签发令牌确实可用：确认+建单全通 | ✅ PASSED |
| T-AG-17 | `test_injection_is_refused_not_obeyed` | 注入 → `policy_refusal`，不生成报价 | ✅ PASSED |
| T-AG-18 | `test_injection_never_creates_order` | 注入不产生订单 | ✅ PASSED |
| T-AG-19 | `test_injection_flagged_for_manual_review` | 注入留痕，标记人工复核 | ✅ PASSED |
| T-AG-20 | `test_tool_calls_recorded_without_token_leak` | 轨迹可审计但不含令牌字段名与令牌值 | ✅ PASSED |

### 5.8 需求抽取与注入防护单元测试（`tests/test_requirement.py`）

| ID | 用例 | 验证点 | 状态 |
|---|---|---|---|
| T-RQ-01 | `test_extracts_use_case_quantity_and_budget` | 三要素抽取且无缺口 | ✅ PASSED |
| T-RQ-02 | `test_reports_gaps_for_empty_message` | 空消息给出全部缺口 | ✅ PASSED |
| T-RQ-03 | `test_accumulates_across_turns` | 多轮叠加不覆盖 | ✅ PASSED |
| T-RQ-04 | `test_quantity_out_of_range_ignored` | 超限数量不写入 | ✅ PASSED |
| T-RQ-05 | `test_budget_and_quantity_conflict_detected` | 预算/数量矛盾被检出 | ✅ PASSED |
| T-RQ-06 | `test_low_budget_with_small_quantity_no_conflict` | 正常组合不误报 | ✅ PASSED |
| T-RQ-07~11 | `test_detects_injection_attempts`（5 组参数化） | 中英文注入话术均命中 | ✅ PASSED |
| T-RQ-12~14 | `test_normal_requests_not_flagged`（3 组参数化） | 正常咨询不误判 | ✅ PASSED |
| T-RQ-15 | `test_extracts_contact_and_product` | 邮箱/联系人/产品/数量抽取 | ✅ PASSED |
| T-RQ-16 | `test_not_ready_without_email` | 缺邮箱不得视为可报价 | ✅ PASSED |
| T-RQ-17 | `test_does_not_guess_missing_values` | 抽不到不臆造联系方式 | ✅ PASSED |
| T-RQ-18 | `test_rejects_unknown_fields` | Schema 拒绝未声明字段 | ✅ PASSED |

### 5.6 安全与幂等要点（已覆盖）

| 机制 | 实现位置 | 测试用例 |
|---|---|---|
| 报价访问令牌 | `Quote.access_token`（64 位 hex），`hmac.compare_digest` 常量时间比较 | T-API-07, T-CT-10 |
| 令牌回显收敛 | 仅创建报价时返回一次，读取/确认不回显 | T-CT-10 |
| 存在性隐藏 | 令牌错误统一返回 404 而非 403 | T-API-07 |
| 乐观锁 | `Quote.version` + `confirm?version=N` | T-API-03/04 |
| 幂等键 | `IdempotencyRecord`，`UniqueConstraint(operation, idempotency_key)` + `request_hash` 比对 + **独立事务抢占** | T-API-03, T-API-06, T-OI-02/03 |
| 后台写鉴权 | `X-Admin-Token` + `hmac.compare_digest`，缺省拒绝 | T-CT-07/08/09 |
| 统一错误封套 | `app/core/errors.py` 注入 `code/message/retryable/trace_id` | T-CT-01~06 |
| 金额防篡改 | `QuoteCreateRequest` 设 `extra="forbid"`，客户端无法注入 `tax_rate`/价格 | T-API-05 |
| 服务端定价 | 单价一律取自 `Product.base_price` | T-API-04 |
| 币种一致性 | 校验所有产品 `currency` 唯一 | T-API-05 |

---

## 6. 执行方式

### 6.1 全量执行（推荐）

```bash
python -m pytest -q
```

### 6.2 详细模式

```bash
python -m pytest -v
```

### 6.3 静态检查

```bash
python -m ruff check .
```

### 6.4 写接口鉴权

`POST /api/v1/products` 需运营后台令牌 `X-Admin-Token`（`app/core/security.py`）：

| `ADMIN_API_TOKEN` | 请求头 | 行为 |
|---|---|---|
| 未配置 | 任意 | 403 `ADMIN_AUTH_REQUIRED`（默认拒绝） |
| 已配置 | 缺失或不匹配 | 401 `ADMIN_AUTH_INVALID` |
| 已配置 | 匹配 | 允许创建产品 |

同一令牌也用于 `POST /api/v1/orders/{order_id}/transition`（订单状态迁移属高风险写操作）。

> 已不再依赖 `DEBUG` 开关：此前 `DEBUG=true` 即对公网开放写接口，属最高风险安全边界。现改为缺省拒绝，令牌以 `hmac.compare_digest` 常量时间比较。正式 RBAC/OIDC 见 ARCH.md §7。
> 报价访问令牌仅在创建报价时返回一次，`GET`/确认响应回显 `access_token: null`；`EXPOSE_QUOTE_TOKEN=true` 可临时恢复回显，仅供本地调试。

---

## 7. 当前执行基线

**执行环境**：Windows / Python 3.12.9 / pytest 8.4.2

```
platform win32 -- Python 3.12.9, pytest-8.4.2, pluggy-1.6.0
rootdir: F:\pycharm_t\ai-robotsales-platform-project
configfile: pyproject.toml
plugins: anyio-4.15.1, asyncio-0.26.0
asyncio: mode=Mode.AUTO

tests/test_api.py                    7 passed   # 产品/报价/订单主路径
tests/test_contract.py              11 passed   # 错误封套、trace_id、后台鉴权、令牌回显
tests/test_frontend.py               2 passed
tests/test_order_integrity.py        6 passed   # 过期下单、幂等并发、状态机
tests/test_order_state_machine.py   25 passed   # G-01 迁移矩阵、G-03 乐观锁
tests/test_agent_orchestration.py   20 passed   # P0：多轮、工具白名单、下单边界、令牌签发
tests/test_requirement.py           24 passed   # P0：需求抽取、缺口、冲突、注入防护

============================= 95 passed in 4.19s ==============================
```

| 指标 | 结果 |
|---|---|
| 用例总数 | 95 |
| 通过 | 95 |
| 失败 | 0 |
| 跳过 | 0 |
| 退出码 | 0 ✅ |

**结论**：当前基线为**全绿**，MVP 主链路（选型→报价→确认→下单）端到端可跑通，且 P0 之后**客户可从对话直接拿到正式报价**。`ruff check .` 与 `ruff format --check .` 均通过。

---

## 8. 已知缺口与风险

> 本节遵循 `AGENTS.md` 要求：**测试失败或未覆盖不得以放宽断言掩盖**。以下缺口需逐项登记责任人、期限与风险。

### 8.1 高优先级缺口

| ID | 缺口 | 风险 | 建议 | 期限 |
|---|---|---|---|---|
| ~~G-01~~ | ~~订单状态机 API 未接线~~ **已修复**：新增 `POST /orders/{id}/transition`（需后台令牌），`test_order_state_machine.py` 覆盖合法/非法迁移矩阵、并发保护与审计事件 | — | 已完成 | ✅ |
| ~~G-02~~ | ~~报价过期分支未测试~~ **已修复**：`test_expired_confirmed_quote_cannot_create_order` 覆盖过期下单（并发现下单路径原先漏检） | — | 已完成 | ✅ |
| ~~G-03~~ | ~~`QUOTE_VERSION_CONFLICT` 未测试~~ **已修复**：`test_confirm_with_wrong_version_conflicts` 等 5 例覆盖版本不匹配、重复确认、令牌不匹配、非法类型、过期优先 | — | 已完成 | ✅ |
| ~~G-04~~ | ~~`DEBUG=false` 鉴权路径未测试~~ **已修复**：写接口改为 `X-Admin-Token`，`test_product_write_*` 覆盖缺令牌/错令牌/正确令牌三种路径 | — | 已完成 | ✅ |

> **P0 缺口已全部关闭**（G-01 ~ G-04）。

### 8.2 中优先级缺口

| ID | 缺口 | 风险 |
|---|---|---|
| G-05 | 测试库为 SQLite，与生产 MySQL 存在方言差异（`Numeric` 精度、`with_for_update` 行锁、并发语义） | 幂等与锁行为在生产可能不一致 |
| G-06 | 无独立单元测试层，`ProductService.search_for_assistant` 的中文分词（2-gram）逻辑仅被间接覆盖 | 分词边界（单字、超长词、混合中英）无验证 |
| ~~G-07~~ | ~~无并发测试~~ **已修复**：`test_concurrent_same_key_creates_single_order` 用文件型 SQLite + 连接池验证同键并发只产生一个订单 | — | ✅ |
| ~~G-08~~ | ~~无 `ORDER_INVALID_TRANSITION` 用例~~ **已修复**：`test_order_state_machine_matches_arch` 覆盖 `COMPLETED` 可达性与终态守卫 | — | ✅ |

### 8.3 低优先级缺口

| ID | 缺口 |
|---|---|
| G-09 | `OrderEvent.actor` 字段有默认值 `"system"`，但无审计用例验证操作者记录 |
| G-10 | `alembic/versions/7b23c4e8a901_quote_access_token.py` 为**新增迁移**，无迁移升降级测试（CI 已校验 `upgrade head` 可用） |
| G-11 | `scripts/seed_products.py` 无单测（CI 已校验脚本可执行） |
| G-12 | 统一错误封套与 `trace_id` 已实现，但未验证 `trace_id` 在跨服务/日志侧的贯通 |
| G-12 | 无评测测试：LLM 意图识别、多轮追问、提示注入防护（当前为 `MockAgentProvider`，见 9.1） |

### 8.4 静态检查缺口

`ruff check .` 与 `ruff format --check .` 当前**全部通过**。此前的 8 项告警已修复：`alembic/env.py` 的导入排序与 F401 已按建议以 `# noqa: F401` + 注释保留（两个模型导入用于向 `Base.metadata` 注册表，不可物理删除），`9336ebdc3898_initial_schema.py` 的 `UP035/I001/UP007` 已清理。

---

## 9. 架构约束下的测试要求

### 9.1 Agent 边界（当前为 Mock）

`app/agents/mock_provider.py` 的 `MockAgentProvider` 是**确定性实现**，在真实 LLM 适配器接入前提供稳定行为，并已完整驱动多轮与工具闭环：

| 场景 | `intent` | `handoff_required` |
|---|---|---|
| 命中提示注入 | `policy_refusal` | `false`（明确拒绝） |
| 需求冲突 | `requirement_clarification` | `true` |
| 检索无结果 | `product_discovery` | `true`（转人工） |
| 需求缺字段 | `requirement_clarification` | `false`（追问） |
| 需求完整待联系方式 | `product_recommendation` | `false` |
| 已生成报价 | `quote_ready` | `false` |

**编排契约**（`app/agents/provider.py`）：`AgentProvider` 协议是模型与业务之间的唯一边界；`ToolRegistry` 是受控白名单；`MAX_AGENT_STEPS=4`、`MAX_TOOL_CALLS=6`、`CONFIDENCE_THRESHOLD=0.55`。达到预算或置信度不足时安全停止并转人工。

**权限边界**：`search_products`(read) → `create_quote`(write_local) → `prepare_order`(write_commit，**只准备不执行**)。`create_order` 端点不在工具白名单内，Agent 无自主下单能力；客户须在页面勾选条款后提交。T-AG-09/T-AG-10 固化此约束，接入 LLM 后**该断言不得放宽**。

**测试要求**：接入真实 LLM 后，必须保持 `AssistantMessageResponse` 结构与 `intent` 枚举不变，并新增**固定样本集回归**，覆盖：

- 多轮追问与缺字段补全（现有 4 例集成测试可复用为基线）
- 歧义需求（如“我要便宜的”无预算锚点）
- 提示注入（现有 5 组参数化用例可复用）
- 知识冲突与低置信度披露

依据 `AGENTS.md` 第 5 节：**Agent 无权自行计算金额或篡改订单状态**，所有金额必须由 `CommerceService` 以 `Product.base_price` 复算。T-API-04 与 T-AG-08 已固化此约束，接入 LLM 后**这两条断言不得放宽**。

**遗留缺口**：`MockAgentProvider` 的推荐理由是模板化文案而非模型生成；真实 LLM 适配器尚未实现（见 `LLM_PROVIDER` 配置项）。`prepare_order` 已注册但当前 Provider 未主动调用，其"待确认动作"路径由 `pending_action` 字段先行承接。

### 9.2 确定性优先原则

以下判断**必须**由确定性服务完成，禁止交由模型自由文本决定：

- 金额计算（`subtotal`、`tax`、`total`）
- 订单状态迁移（`ORDER_TRANSITIONS`）
- 幂等判定（`IdempotencyRecord`）
- 权限与访问令牌校验
- 币种一致性

---

## 10. 验收标准（Definition of Done）

一个变更只有同时满足以下条件才可合入：

- [ ] 新增/修改逻辑有对应用例，且**先复现失败、再验证通过**
- [ ] `python -m pytest -q` **全绿**，退出码 0
- [ ] `python -m ruff check .` 无**新增**告警
- [ ] 金额、状态、幂等路径有确定性校验用例
- [ ] 未以放宽断言、跳过用例、修改生产规则的方式规避失败
- [ ] 新增缺口已登记至第 8 节，含责任人、期限与风险说明
- [ ] 本文档的用例清单与执行基线同步更新

---

## 11. 缺陷分级

| 级别 | 定义 | 响应要求 |
|---|---|---|
| S1 阻断 | 金额错误、重复下单、越权访问、生产写接口未鉴权 | 立即修复，禁止发布 |
| S2 严重 | 状态机非法迁移、幂等失效、报价过期仍可下单 | 当版本内修复 |
| S3 一般 | 边界输入 500、错误码不一致、前端展示异常 | 排期修复 |
| S4 轻微 | 静态检查告警、文档不一致、日志字段缺失 | 随版本清理 |

---

## 12. 后续测试计划

| 阶段 | 目标 | 交付物 |
|---|---|---|
| ~~第一优先~~ | ~~补齐 G-01 ~ G-04（状态机、过期、版本冲突、鉴权）~~ **已完成** | 新增用例 ≥ 8 个 |
| ~~P0~~ | ~~Agent 编排：多轮会话、Tool Calling、报价接通、注入防护~~ **已完成** | `test_agent_orchestration.py` + `test_requirement.py`（44 例） |
| 第二优先 | 引入 MySQL 容器化集成测试，消除方言差异 | `docker-compose` 测试 profile |
| 第三优先 | 接入真实 LLM 并建立评测样本库 | `skills/*/evals/` + 固定回归集 |
| 第四优先 | 非功能测试：P95 延迟、限流、可观测性 | 压测脚本 + 指标看板 |

## 13. 变更记录

| 版本 | 日期 | 变更内容 | 作者 |
|---|---|---|---|
| v1.1.0 | 2026-10-04 | P0：新增 Agent 编排与需求抽取 44 例用例；基线更新为 95 passed；关闭静态检查缺口；重写 §9.1 Agent 边界 | NewStudent258 |
| v1.0.0 | 2026-09-30 | 首版：定义测试分层、9 个用例清单、执行基线、12 项缺口 | NewStudent258 |
