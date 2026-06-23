#!/usr/bin/env python3
"""
pwhl_news.py — Fetch PWHL news from RSS feeds and POST to Worker.

Runs from GitHub Actions where Cloudflare Workers IPs are not blocked.
Fetches from multiple sources, filters for PWHL content, and POSTs
the combined articles to /pwhl/news/ingest on the Worker.

Usage:
    python pwhl_news.py
"""
import json
import logging
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

WORKER_URL   = os.environ.get("WORKER_URL", "https://eyewall-poller.billowing-queen-bf23.workers.dev")
POLL_SECRET  = os.environ["POLL_SECRET"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml,text/xml,application/xml,*/*",
}

# PWHL keyword filter — article must contain at least one
PWHL_KEYWORDS = [
    "pwhl",
    "women's hockey", "womens hockey",
    "walter cup",
    # Team names (official and common)
    "minnesota frost", "boston fleet", "montreal victoire", "montréal victoire",
    "new york sirens", "ottawa charge", "toronto sceptres",
    "seattle torrent", "vancouver goldeneyes",
    "pwhl detroit", "pwhl hamilton", "pwhl las vegas", "pwhl san jose",
    # Expansion team shorthand
    "goldeneyes", "torrent", "sceptres", "victoire",
    # Key players
    "kelly pannek", "sarah fillier", "marie-philip poulin", "laura stacey",
    "aerin frankel", "ann-renée desbiens", "hilary knight",
    "natalie spooner", "brianne jenner", "jayna hefford",
    "taylor heise", "abby boreen",
    # Coverage keywords
    "women's professional hockey", "professional women's hockey",
    "female hockey", "women hockey",
]

SOURCES = [
    {
        "id":   "espn-pwhl",
        "name": "ESPN",
        "bg":   "#cc0000",
        "url":  "https://www.espn.com/espn/rss/hockey/news",
        "type": "rss",
    },
    {
        "id":   "thescore-pwhl",
        "name": "The Score",
        "bg":   "#e8000d",
        "url":  "https://origin-feeds.thescore.com/hockey.rss",
        "type": "rss",
    },
    {
        "id":   "sportsnet-pwhl",
        "name": "Sportsnet",
        "bg":   "#d4a017",
        "url":  "https://www.sportsnet.ca/feed/",
        "type": "rss",
    },
    {
        "id":   "tsn-pwhl",
        "name": "TSN",
        "bg":   "#004f9f",
        "url":  "https://www.tsn.ca/rss/tsn.rss",
        "type": "rss",
    },
    {
        "id":   "hockeynews-pwhl",
        "name": "Hockey News",
        "bg":   "#c8102e",
        "url":  "https://thehockeywriters.com/feed/",
        "type": "rss",
    },
]


def fetch_url(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"  fetch {url}: {e}")
        return None


def safe_text(el, tag: str) -> str:
    child = el.find(tag)
    if child is None:
        return ""
    text = child.text or ""
    # Strip CDATA and HTML tags
    text = text.replace("<![CDATA[", "").replace("]]>", "")
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).strip()


def parse_rss(xml: str, source: dict) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        log.warning(f"  XML parse error for {source['id']}: {e}")
        return []

    # Handle both RSS and Atom

    channel = root.find("channel")
    raw_items = channel.findall("item") if channel is not None else root.findall(".//item")

    for item in raw_items:
        title   = safe_text(item, "title")
        link    = safe_text(item, "link") or safe_text(item, "guid")
        pub     = safe_text(item, "pubDate") or safe_text(item, "dc:date")
        excerpt = safe_text(item, "description") or safe_text(item, "content:encoded")
        image   = None

        # Try media:thumbnail
        media = item.find("{http://search.yahoo.com/mrss/}thumbnail")
        if media is not None:
            image = media.get("url")

        if not title or not link:
            continue

        # Parse date
        pub_iso = None
        if pub:
            try:
                pub_iso = parsedate_to_datetime(pub).isoformat()
            except Exception:
                try:
                    pub_iso = datetime.fromisoformat(pub).isoformat()
                except Exception:
                    pass

        # Unique ID
        import hashlib
        uid = source["id"] + "-" + hashlib.md5(link.encode()).hexdigest()[:16]

        items.append({
            "id":          uid,
            "source":      source["id"],
            "sourceName":  source["name"],
            "title":       title,
            "url":         link,
            "excerpt":     excerpt[:200] if excerpt else "",
            "publishedAt": pub_iso,
            "imageUrl":    image,
            "bg":          source.get("bg", "#333"),
        })

    return items


def is_pwhl(item: dict) -> bool:
    text = (item.get("title", "") + " " + item.get("excerpt", "")).lower()
    return any(kw in text for kw in PWHL_KEYWORDS)


def post_to_worker(articles: list[dict]) -> None:
    payload = json.dumps(articles).encode("utf-8")
    req = urllib.request.Request(
        f"{WORKER_URL}/pwhl/news/ingest",
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


def main():
    all_articles: list[dict] = []

    for source in SOURCES:
        log.info(f"Fetching {source['id']} from {source['url']}")
        xml = fetch_url(source["url"])
        if not xml:
            continue
        parsed = parse_rss(xml, source)
        log.info(f"  {source['id']}: {len(parsed)} raw items")
        pwhl = [a for a in parsed if is_pwhl(a)]
        log.info(f"  {source['id']}: {len(pwhl)} PWHL items after filter")
        all_articles.extend(pwhl)

    # Deduplicate by id
    seen: set[str] = set()
    deduped = []
    for a in all_articles:
        if a["id"] not in seen:
            seen.add(a["id"])
            deduped.append(a)

    # Sort newest first
    deduped.sort(key=lambda a: a.get("publishedAt") or "", reverse=True)

    log.info(f"Total PWHL articles: {len(deduped)}")
    if not deduped:
        log.warning("No PWHL articles found — check sources and filters")
        return

    log.info("POSTing to Worker...")
    post_to_worker(deduped)
    log.info("Done.")


if __name__ == "__main__":
    main()
