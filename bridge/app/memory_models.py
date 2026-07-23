from pydantic import BaseModel, Field


class SaveMemoryRequest(BaseModel):
    category: str = Field(
        min_length=1,
        max_length=50,
        examples=["preference"],
    )
    subject: str = Field(
        min_length=1,
        max_length=150,
        examples=["favourite football team"],
    )
    content: str = Field(
        min_length=1,
        max_length=2000,
        examples=["Aaron's favourite football team is Aston Villa."],
    )


class SearchMemoryRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        examples=["favourite football team"],
    )
    limit: int = Field(
        default=8,
        ge=1,
        le=20,
    )
