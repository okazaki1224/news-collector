from datetime import datetime

from pydantic import BaseModel, Field


class KeywordCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=200)


class KeywordResponse(BaseModel):
    id: int
    text: str
    enabled: bool
    created_at: datetime
    article_count: int = 0

    model_config = {"from_attributes": True}


class KeywordUpdate(BaseModel):
    enabled: bool


class ArticleResponse(BaseModel):
    id: int
    keyword_id: int
    keyword_text: str
    title: str
    url: str
    source: str
    summary: str | None
    published_at: datetime | None
    collected_at: datetime

    model_config = {"from_attributes": True}


class CollectionLogResponse(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    articles_found: int
    articles_new: int
    status: str
    message: str | None

    model_config = {"from_attributes": True}


class CollectResult(BaseModel):
    articles_found: int
    articles_new: int
    keywords_processed: int


class StatusResponse(BaseModel):
    scheduler_running: bool
    next_run: datetime | None
    last_collection: CollectionLogResponse | None
    keyword_count: int
    article_count: int
