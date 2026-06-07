import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import httpx
from dateutil import parser as date_parser
from sqlalchemy.orm import Session

from app.models import Article, CollectionLog, Keyword

logger = logging.getLogger(__name__)

NEWS_SOURCES = [
    {
        "name": "Google News",
        "build_url": lambda kw: (
            f"https://news.google.com/rss/search?q={quote_plus(kw)}&hl=ja&gl=JP&ceid=JP:ja"
        ),
        "filter_keyword": False,
    },
    {
        "name": "Bing News",
        "build_url": lambda kw: (
            f"https://www.bing.com/news/search?q={quote_plus(kw)}&format=rss"
        ),
        "filter_keyword": False,
    },
    {
        "name": "NHKニュース",
        "build_url": lambda _kw: "https://www3.nhk.or.jp/rss/news/cat0.xml",
        "filter_keyword": True,
    },
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class RawArticle:
    title: str
    url: str
    source: str
    summary: str | None
    published_at: datetime | None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return date_parser.parse(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _clean_summary(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    return cleaned[:1000] if cleaned else None


def _matches_keyword(text: str, keyword: str) -> bool:
    return keyword.casefold() in text.casefold()


async def fetch_feed(
    url: str, source_name: str, keyword: str = "", filter_keyword: bool = False
) -> list[RawArticle]:
    articles: list[RawArticle] = []
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
    except Exception as exc:
        logger.warning("Failed to fetch %s (%s): %s", source_name, url, exc)
        return articles

    for entry in feed.entries[:30]:
        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()
        if not title or not url:
            continue

        if filter_keyword and keyword:
            haystack = f"{title} {getattr(entry, 'summary', '')} {getattr(entry, 'description', '')}"
            if not _matches_keyword(haystack, keyword):
                continue

        summary = _clean_summary(
            getattr(entry, "summary", None) or getattr(entry, "description", None)
        )
        published = _parse_date(getattr(entry, "published", None) or getattr(entry, "updated", None))

        articles.append(
            RawArticle(
                title=title,
                url=url,
                source=source_name,
                summary=summary,
                published_at=published,
            )
        )

    return articles


async def search_keyword(keyword: str) -> list[RawArticle]:
    seen_urls: set[str] = set()
    results: list[RawArticle] = []

    for source in NEWS_SOURCES:
        url = source["build_url"](keyword)
        items = await fetch_feed(
            url,
            source["name"],
            keyword=keyword,
            filter_keyword=source.get("filter_keyword", False),
        )
        for item in items:
            url_hash = hashlib.md5(item.url.encode()).hexdigest()
            if url_hash in seen_urls:
                continue
            seen_urls.add(url_hash)
            results.append(item)

    return results


def save_articles(db: Session, keyword_id: int, raw_articles: list[RawArticle]) -> tuple[int, int]:
    found = len(raw_articles)
    new_count = 0

    for raw in raw_articles:
        exists = (
            db.query(Article)
            .filter(Article.keyword_id == keyword_id, Article.url == raw.url)
            .first()
        )
        if exists:
            continue

        db.add(
            Article(
                keyword_id=keyword_id,
                title=raw.title,
                url=raw.url,
                source=raw.source,
                summary=raw.summary,
                published_at=raw.published_at,
            )
        )
        new_count += 1

    db.commit()
    return found, new_count


async def collect_for_keyword(db: Session, keyword: Keyword) -> tuple[int, int]:
    raw_articles = await search_keyword(keyword.text)
    return save_articles(db, keyword.id, raw_articles)


async def collect_all(db: Session, trigger: str = "scheduled") -> CollectionLog:
    log = CollectionLog(status="running", message=f"trigger={trigger}")
    db.add(log)
    db.commit()
    db.refresh(log)

    total_found = 0
    total_new = 0

    try:
        keywords = db.query(Keyword).filter(Keyword.enabled.is_(True)).all()
        for keyword in keywords:
            found, new = await collect_for_keyword(db, keyword)
            total_found += found
            total_new += new
            logger.info(
                "Collected keyword '%s': %d found, %d new", keyword.text, found, new
            )

        log.finished_at = datetime.utcnow()
        log.articles_found = total_found
        log.articles_new = total_new
        log.status = "success"
        log.message = f"{len(keywords)} keywords processed ({trigger})"
    except Exception as exc:
        logger.exception("Collection failed")
        log.finished_at = datetime.utcnow()
        log.status = "error"
        log.message = str(exc)

    db.commit()
    db.refresh(log)
    return log
