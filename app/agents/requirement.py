"""S1 需求采集的结构化契约。

ARCH.md §2 要求 S1 输出 `RequirementProfile`、缺口与冲突；
AGENTS.md §4 要求 Schema 作为唯一机器可读契约。

本模块只做**确定性**工作：字段归一化、缺口计算、冲突检测。
不涉及任何模型判断，因此可被单元测试稳定覆盖。
"""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

# 必填字段：缺失即产出追问，是「一次采集完整率」指标的判定依据。
REQUIRED_FIELDS: tuple[str, ...] = ("use_case", "quantity", "budget")

FIELD_LABELS: dict[str, str] = {
    "use_case": "使用场景",
    "quantity": "采购数量",
    "budget": "预算范围",
    "industry": "所属行业",
    "deploy_time": "期望上线时间",
}

# 场景关键词到标准场景名的映射。命中即视为已知场景。
_USE_CASE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "仓储巡检": ("仓储", "巡检", "仓库", "盘点"),
    "教育科研": ("教育", "教学", "科研", "实验", "学生", "课程"),
    "工业制造": ("工业", "制造", "产线", "车间", "工厂"),
    "商业服务": ("商业", "服务", "导览", "接待", "配送"),
    "开发者平台": ("开发", "二次开发", "sdk", "原型"),
}

_QUANTITY_PATTERN = re.compile(r"(\d+)\s*(台|套|个|件|unit|units)", re.IGNORECASE)
# 显式写法 "quantity 2" / "数量：2"：中英文都要识别，
# 否则用户明确给出的数量会被静默忽略、退回默认值 1，导致报价偏低。
_QUANTITY_EXPLICIT_PATTERN = re.compile(r"(?:quantity|数量|qty)\s*[:：]?\s*(\d+)", re.IGNORECASE)
_BUDGET_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(万|千|元|块|k|K|w|W)")


class RequirementProfile(BaseModel):
    """结构化需求。字段为 None 表示尚未采集到。"""

    model_config = ConfigDict(extra="forbid")

    use_case: str | None = Field(default=None, max_length=64)
    quantity: int | None = Field(default=None, ge=1, le=10000)
    budget: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=64)
    deploy_time: str | None = Field(default=None, max_length=64)

    def gaps(self) -> list[str]:
        """返回缺失的必填字段标签，顺序稳定以便前端展示与测试断言。"""
        return [FIELD_LABELS[name] for name in REQUIRED_FIELDS if getattr(self, name) is None]

    def is_complete(self) -> bool:
        return not self.gaps()


@dataclass(slots=True)
class RequirementExtraction:
    """一次抽取的结果：更新后的需求、缺口、冲突与新增字段。"""

    profile: RequirementProfile
    gaps: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)


class RequirementExtractor:
    """基于规则的确定性需求抽取器。

    这里是 S1 的**确定性核心**：即使用户文本由 LLM 预处理，最终的字段落库
    仍以本抽取器为准，避免模型自由文本直接改写业务事实（AGENTS.md §2.4）。
    """

    def extract(
        self, message: str, base: RequirementProfile | None = None
    ) -> RequirementExtraction:
        profile = (base or RequirementProfile()).model_copy()
        filled: list[str] = []

        use_case = self._detect_use_case(message)
        if use_case and profile.use_case != use_case:
            profile.use_case = use_case
            filled.append("use_case")

        quantity = self._detect_quantity(message)
        if quantity is not None and profile.quantity != quantity:
            profile.quantity = quantity
            filled.append("quantity")

        budget = self._detect_budget(message)
        if budget and profile.budget != budget:
            profile.budget = budget
            filled.append("budget")

        return RequirementExtraction(
            profile=profile,
            gaps=profile.gaps(),
            conflicts=self._detect_conflicts(profile),
            filled=filled,
        )

    @staticmethod
    def _detect_use_case(message: str) -> str | None:
        lowered = message.lower()
        for name, keywords in _USE_CASE_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return name
        return None

    @staticmethod
    def _detect_quantity(message: str) -> int | None:
        match = _QUANTITY_PATTERN.search(message) or _QUANTITY_EXPLICIT_PATTERN.search(message)
        if match:
            value = int(match.group(1))
            return value if 1 <= value <= 10000 else None
        return None

    @staticmethod
    def _detect_budget(message: str) -> str | None:
        match = _BUDGET_PATTERN.search(message)
        if match:
            return f"{match.group(1)}{match.group(2)}"
        return None

    @staticmethod
    def _detect_conflicts(profile: RequirementProfile) -> list[str]:
        """检测需求内部矛盾。冲突不阻断流程，但必须披露给用户。"""
        conflicts: list[str] = []
        budget = profile.budget or ""
        quantity = profile.quantity or 0
        # 预算极低但采购数量很大，属典型矛盾组合，需要提示客户复核。
        if quantity >= 10 and budget and budget.endswith(("千", "k", "K")):
            amount = float(budget.rstrip("千kK") or 0)
            if amount <= 5:
                conflicts.append("预算与采购数量可能不匹配，建议确认预算口径或调整数量。")
        return conflicts


# 提示注入防护：外部文本一律作为数据，不得覆盖系统策略（ARCH.md §5）。
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"忽略(以上|之前|上述|前面).{0,10}(指令|规则|设定|提示)", re.IGNORECASE),
    re.compile(
        r"(ignore|disregard)\s+(all\s+)?(previous|above|prior)\s+instructions", re.IGNORECASE
    ),
    re.compile(r"(直接|马上|立刻)?(生成|创建|开出?).{0,6}(0|零|免费)\s*元", re.IGNORECASE),
    re.compile(r"(修改|改动|篡改).{0,6}(价格|金额|报价|订单状态|状态)", re.IGNORECASE),
    re.compile(r"(绕过|跳过).{0,8}(确认|审批|鉴权|审核|校验)"),
    re.compile(r"(system|系统)\s*(prompt|提示词)"),
    re.compile(r"(你现在是|扮演|假装你是).{0,12}(管理员|admin|root)", re.IGNORECASE),
)


@dataclass(slots=True)
class InjectionVerdict:
    detected: bool
    matched: list[str] = field(default_factory=list)


# 报价所需的联系方式与目标产品，从用户文本中确定性抽取。
_EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
_NAME_PATTERN = re.compile(r"(?:联系人|姓名|叫我|我是)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z]{2,20})")
_PRODUCT_ID_PATTERN = re.compile(r"(?:product_id|产品\s*id|产品ID)\s*[:：]?\s*(\d+)", re.IGNORECASE)


@dataclass(slots=True)
class QuoteIntent:
    """从用户文本抽取的报价要素。`ready` 为真表示可以生成正式报价。"""

    customer_name: str | None = None
    customer_email: str | None = None
    product_id: int | None = None
    quantity: int | None = None

    @property
    def ready(self) -> bool:
        return bool(self.customer_name and self.customer_email)


def extract_quote_intent(message: str) -> QuoteIntent:
    """确定性抽取报价要素。

    不做任何自由文本推断：抽不到就返回空，由编排器继续追问，
    避免把猜测的邮箱或产品写入正式报价。
    """
    email = _EMAIL_PATTERN.search(message)
    name = _NAME_PATTERN.search(message)
    product = _PRODUCT_ID_PATTERN.search(message)
    quantity = RequirementExtractor._detect_quantity(message)
    return QuoteIntent(
        customer_name=name.group(1).strip() if name else None,
        customer_email=email.group(0).strip() if email else None,
        product_id=int(product.group(1)) if product else None,
        quantity=quantity,
    )


def detect_injection(message: str) -> InjectionVerdict:
    """检测提示注入与越权指令。

    只做标记与披露，不据此改写金额或状态——那些始终由确定性服务裁决。
    """
    matched = [pattern.pattern for pattern in _INJECTION_PATTERNS if pattern.search(message)]
    return InjectionVerdict(detected=bool(matched), matched=matched)
