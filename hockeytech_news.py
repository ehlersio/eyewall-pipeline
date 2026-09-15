"""
hockeytech_news.py -- fetch AHL/ECHL news from RSS feeds and POST it to the
Worker (/{league}/news/ingest). Shared by ahl_news.py and echl_news.py;
sources live in hockeytech_leagues.py.

Runs from GitHub Actions, where Cloudflare Workers' IPs aren't blocked the
way they are by some feeds. Mirrors pwhl_news.py, minus the keyword filter:
every AHL/ECHL source is league-scoped by construction.
"""

import hashlib
import json
import logging
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

from hockeytech_leagues import League
from pipeline_common import FetchError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

WORKER_URL = os.environ.get("WORKER_URL", "https://eyewall-poller.billowing-queen-bf23.workers.dev")
POLL_SECRET = os.environ["EYEWALL_POLL_SECRET"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml,text/xml,application/xml,*/*",
}


def fetch_url(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise FetchError(f"fetch {url}: {e}") from e


def safe_text(el, tag: str) -> str:
    child = el.find(tag)
    if child is None:
        return ""
    text = child.text or ""
    # Strip CDATA and HTML tags
    text = text.replace("<![CDATA[", "").replace("]]>", "")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).strip()


def parse_rss(xml: str, source: dict) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        log.warning(f"  XML parse error for {source['id']}: {e}")
        return []

    channel = root.find("channel")
    raw_items = channel.findall("item") if channel is not None else root.findall(".//item")

    for item in raw_items:
        title = safe_text(item, "title")
        link = safe_text(item, "link") or safe_text(item, "guid")
        pub = safe_text(item, "pubDate") or safe_text(item, "dc:date")
        excerpt = safe_text(item, "description") or safe_text(item, "content:encoded")
        image = None

        media = item.find("{http://search.yahoo.com/mrss/}thumbnail")
        if media is not None:
            image = media.get("url")

        if not title or not link:
            continue

        pub_iso = None
        if pub:
            try:
                pub_iso = parsedate_to_datetime(pub).isoformat()
            except Exception:
                try:
                    pub_iso = datetime.fromisoformat(pub).isoformat()
                except Exception:
                    pass

        uid = source["id"] + "-" + hashlib.md5(link.encode()).hexdigest()[:16]

        items.append(
            {
                "id": uid,
                "source": source["id"],
                "sourceName": source["name"],
                "title": title,
                "url": link,
                "excerpt": excerpt[:200] if excerpt else "",
                "publishedAt": pub_iso,
                "imageUrl": image,
                "bg": source.get("bg", "#333"),
            }
        )

    return items


def post_to_worker(lg: League, articles: list[dict]) -> None:
    payload = json.dumps(articles).encode("utf-8")
    req = urllib.request.Request(
        f"{WORKER_URL}/{lg.key}/news/ingest",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-ingest-secret": POLL_SECRET,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            log.info(f"  Worker response: {resp.status} {body[:100]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        log.error(f"  Worker POST failed: {e.code} {body[:200]}")
        raise
    except Exception as e:
        log.error(f"  Worker POST error: {e}")
        raise


def main(lg: League) -> None:
    all_articles: list[dict] = []

    for source in lg.news_sources:
        log.info(f"Fetching {source['id']} from {source['url']}")
        try:
            xml = fetch_url(source["url"])
        except FetchError as e:
            log.warning(f"  {e}")
            continue
        parsed = parse_rss(xml, source)
        log.info(f"  {source['id']}: {len(parsed)} items")
        all_articles.extend(parsed)

    seen: set[str] = set()
    deduped = []
    for a in all_articles:
        if a["id"] not in seen:
            seen.add(a["id"])
            deduped.append(a)

    deduped.sort(key=lambda a: a.get("publishedAt") or "", reverse=True)

    log.info(f"Total {lg.label} articles: {len(deduped)}")
    if not deduped:
        log.warning(f"No {lg.label} articles found — check sources")
        return

    log.info("POSTing to Worker...")
    post_to_worker(lg, deduped)
    log.info("Done.")
