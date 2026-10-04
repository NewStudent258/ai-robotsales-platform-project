"""Agent 层：Provider 抽象、受控工具、需求抽取与提示注入防护。"""

from app.agents.mock_provider import MockAgentProvider
from app.agents.provider import (
    CONFIDENCE_THRESHOLD,
    MAX_AGENT_STEPS,
    MAX_TOOL_CALLS,
    AgentContext,
    AgentProvider,
    AgentTurn,
    ToolCall,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from app.agents.requirement import (
    FIELD_LABELS,
    REQUIRED_FIELDS,
    InjectionVerdict,
    QuoteIntent,
    RequirementExtraction,
    RequirementExtractor,
    RequirementProfile,
    detect_injection,
    extract_quote_intent,
)
from app.agents.tools import build_default_registry

__all__ = [
    "CONFIDENCE_THRESHOLD",
    "FIELD_LABELS",
    "MAX_AGENT_STEPS",
    "MAX_TOOL_CALLS",
    "REQUIRED_FIELDS",
    "AgentContext",
    "AgentProvider",
    "AgentTurn",
    "InjectionVerdict",
    "MockAgentProvider",
    "QuoteIntent",
    "RequirementExtraction",
    "RequirementExtractor",
    "RequirementProfile",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_default_registry",
    "detect_injection",
    "extract_quote_intent",
]
