from pydantic import BaseModel, Field


class VerificationDecision(BaseModel):
    application_id: str
    cleaner_id: str
    reason: str | None = None


class DocumentDecision(BaseModel):
    document_id: str
    reason: str | None = Field(default=None, max_length=500)