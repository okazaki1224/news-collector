import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.collector import collect_all, collect_for_keyword, search_keyword, save_articles
from app.database import get_db, init_db
from app.models import Article, CollectionLog, Keyword
from app.scheduler import get_next_run, scheduler, start_scheduler, stop_scheduler
from app.schemas import (
    ArticleResponse,
    CollectResult,
    CollectionLogResponse,
    KeywordCreate,
    KeywordResponse,
    KeywordUpdate,
    StatusResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="ニュース収集アプリ", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status", response_model=StatusResponse)
def get_status(db: Session = Depends(get_db)):
    last_log = db.query(CollectionLog).order_by(CollectionLog.started_at.desc()).first()
    return StatusResponse(
        scheduler_running=scheduler.running,
        next_run=get_next_run(),
        last_collection=last_log,
        keyword_count=db.query(Keyword).count(),
        article_count=db.query(Article).count(),
    )


@app.get("/api/keywords", response_model=list[KeywordResponse])
def list_keywords(db: Session = Depends(get_db)):
    keywords = db.query(Keyword).order_by(Keyword.created_at.desc()).all()
    result = []
    for kw in keywords:
        count = db.query(Article).filter(Article.keyword_id == kw.id).count()
        result.append(
            KeywordResponse(
                id=kw.id,
                text=kw.text,
                enabled=kw.enabled,
                created_at=kw.created_at,
                article_count=count,
            )
        )
    return result


@app.post("/api/keywords", response_model=KeywordResponse, status_code=201)
def create_keyword(data: KeywordCreate, db: Session = Depends(get_db)):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="キーワードを入力してください")

    existing = db.query(Keyword).filter(Keyword.text == text).first()
    if existing:
        raise HTTPException(status_code=409, detail="このキーワードは既に登録されています")

    keyword = Keyword(text=text)
    db.add(keyword)
    db.commit()
    db.refresh(keyword)
    return KeywordResponse(
        id=keyword.id,
        text=keyword.text,
        enabled=keyword.enabled,
        created_at=keyword.created_at,
        article_count=0,
    )


@app.patch("/api/keywords/{keyword_id}", response_model=KeywordResponse)
def update_keyword(keyword_id: int, data: KeywordUpdate, db: Session = Depends(get_db)):
    keyword = db.query(Keyword).filter(Keyword.id == keyword_id).first()
    if not keyword:
        raise HTTPException(status_code=404, detail="キーワードが見つかりません")

    keyword.enabled = data.enabled
    db.commit()
    db.refresh(keyword)
    count = db.query(Article).filter(Article.keyword_id == keyword.id).count()
    return KeywordResponse(
        id=keyword.id,
        text=keyword.text,
        enabled=keyword.enabled,
        created_at=keyword.created_at,
        article_count=count,
    )


@app.delete("/api/keywords/{keyword_id}", status_code=204)
def delete_keyword(keyword_id: int, db: Session = Depends(get_db)):
    keyword = db.query(Keyword).filter(Keyword.id == keyword_id).first()
    if not keyword:
        raise HTTPException(status_code=404, detail="キーワードが見つかりません")
    db.delete(keyword)
    db.commit()


@app.get("/api/articles", response_model=list[ArticleResponse])
def list_articles(
    keyword_id: int | None = Query(None),
    source: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Article, Keyword.text).join(Keyword, Article.keyword_id == Keyword.id)

    if keyword_id is not None:
        query = query.filter(Article.keyword_id == keyword_id)
    if source:
        query = query.filter(Article.source == source)

    rows = (
        query.order_by(
            func.coalesce(Article.published_at, Article.collected_at).desc()
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [
        ArticleResponse(
            id=article.id,
            keyword_id=article.keyword_id,
            keyword_text=keyword_text,
            title=article.title,
            url=article.url,
            source=article.source,
            summary=article.summary,
            published_at=article.published_at,
            collected_at=article.collected_at,
        )
        for article, keyword_text in rows
    ]


@app.post("/api/collect", response_model=CollectResult)
async def collect_now(db: Session = Depends(get_db)):
    log = await collect_all(db, trigger="manual")
    keywords_processed = db.query(Keyword).filter(Keyword.enabled.is_(True)).count()
    return CollectResult(
        articles_found=log.articles_found,
        articles_new=log.articles_new,
        keywords_processed=keywords_processed,
    )


@app.post("/api/collect/{keyword_id}", response_model=CollectResult)
async def collect_keyword(keyword_id: int, db: Session = Depends(get_db)):
    keyword = db.query(Keyword).filter(Keyword.id == keyword_id).first()
    if not keyword:
        raise HTTPException(status_code=404, detail="キーワードが見つかりません")

    found, new = await collect_for_keyword(db, keyword)
    return CollectResult(
        articles_found=found,
        articles_new=new,
        keywords_processed=1,
    )


@app.post("/api/search", response_model=CollectResult)
async def search_and_save(data: KeywordCreate, db: Session = Depends(get_db)):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="キーワードを入力してください")

    keyword = db.query(Keyword).filter(Keyword.text == text).first()
    if not keyword:
        keyword = Keyword(text=text)
        db.add(keyword)
        db.commit()
        db.refresh(keyword)

    raw = await search_keyword(text)
    found, new = save_articles(db, keyword.id, raw)
    return CollectResult(
        articles_found=found,
        articles_new=new,
        keywords_processed=1,
    )


@app.get("/api/logs", response_model=list[CollectionLogResponse])
def list_logs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return (
        db.query(CollectionLog)
        .order_by(CollectionLog.started_at.desc())
        .limit(limit)
        .all()
    )
