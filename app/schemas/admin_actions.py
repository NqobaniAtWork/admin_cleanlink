from pydantic import BaseModel, Field


class AdminActionCreate(BaseModel):
    admin_user_id: str
    action: str = Field(min_length=3)
    target_type: str = Field(min_length=2)
    target_id: str | None = None
    reason: str | None = None
    metadata: dict = Field(default_factory=dict)