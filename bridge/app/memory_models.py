from pydantic import BaseModel, Field


class SaveMemoryRequest(BaseModel):
    category: str = Field(min_length=1, max_length=50, examples=["preference"])
    subject: str = Field(
        min_length=1,
        max_length=150,
        examples=["Amber health conditions"],
    )
    content: str = Field(
        min_length=1,
        max_length=2000,
        examples=["Amber is lactose intolerant."],
    )
    owner_key: str = Field(default="aaron", min_length=1, max_length=64)
    subject_key: str | None = Field(default=None, max_length=64)
    visibility: str | None = Field(
        default=None,
        pattern=r"^(private|subject_and_owner|household)$",
    )
    sensitivity: str | None = Field(
        default=None,
        pattern=r"^(normal|sensitive)$",
    )


class SearchMemoryRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        examples=["health conditions"],
    )
    limit: int = Field(default=8, ge=1, le=20)
    requester_key: str = Field(default="aaron", min_length=1, max_length=64)
