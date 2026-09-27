from pydantic import BaseModel, Field


class AssistantMessageRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class RecommendedProduct(BaseModel):
    product_id: int
    name: str
    reason: str
    base_price: str


class AssistantMessageResponse(BaseModel):
    session_id: str
    intent: str
    answer: str
    missing_fields: list[str] = Field(default_factory=list)
    recommendations: list[RecommendedProduct] = Field(default_factory=list)
    handoff_required: bool = False
