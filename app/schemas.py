from pydantic import BaseModel, Field


class ProcessVideoResponse(BaseModel):
    video_id: int
    bv_id: str
    title: str
    category: str
    summary: str
    key_entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    structured_notes: dict = Field(default_factory=dict)
    transcript_source: str
