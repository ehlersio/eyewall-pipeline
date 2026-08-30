#!/usr/bin/env python3
"""
echl_news.py — Fetch ECHL news from RSS feeds and POST to Worker.

Runs from GitHub Actions where Cloudflare Workers IPs are not blocked.
Mirrors ahl_news.py exactly, with one real difference: only 2 sources
exist for ECHL, not 3. echl.com has no discoverable RSS feed at all
(confirmed live 2026-08-30: /feed and /rss both 404 -- consistent with
this site being a Laravel/Livewire rebuild that renders server-side, the
same reason its HockeyTech key isn't exposed on the network tab either,
see seasons.js's ECHL_HT_KEY comment in eyewall-poller). The two real
sources below are both ECHL-scoped by construction, so no keyword filter
is needed here either.

Usage:
    python echl_news.py
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

SOURCES = [
    {
        # Hockey Writers' dedicated ECHL category feed, not its general
        # site feed (thehockeywriters.com/category/echl/feed/, confirmed
        # live 2026-08-30) -- every item here is already ECHL-tagged.
        "id": "hockeywriters-echl",
        "name": "The Hockey Writers",
        "bg": "#1a1a1a",
        "url": "https://thehockeywriters.com/category/echl/feed/",
        "type": "rss",
        "filter": False,
    },
    {
        # ECHL press releases: game recaps, signings, roster moves.
        # League id 18 on OurSportsCentral -- NOT 17 like AHL's, confirmed
        # live 2026-08-30 (searched for it rather than assumed, since
        # league ids on this site aren't sequential-by-launch-date).
        "id": "osc-echl",
        "name": "OurSports Central",
        "bg": "#8b0000",
        "url": "https://www.oursportscentral.com/feeds/l18.xml",
        "type": "rss",
        "filter": False,
    },
]


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


def post_to_worker(articles: list[dict]) -> None:
    payload = json.dumps(articles).encode("utf-8")
    req = urllib.request.Request(
        f"{WORKER_URL}/echl/news/ingest",
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
        try:
            xml = fetch_url(source["url"])
        except FetchError as e:
            log.warning(f"  {e}")
            continue
        parsed = parse_rss(xml, source)
        log.info(f"  {source['id']}: {len(parsed)} items")
        all_articles.extend(parsed)

    # Deduplicate by id
    seen: set[str] = set()
    deduped = []
    for a in all_articles:
        if a["id"] not in seen:
            seen.add(a["id"])
            deduped.append(a)

    # Sort newest first
    deduped.sort(key=lambda a: a.get("publishedAt") or "", reverse=True)

    log.info(f"Total ECHL articles: {len(deduped)}")
    if not deduped:
        log.warning("No ECHL articles found — check sources")
        return

    log.info("POSTing to Worker...")
    post_to_worker(deduped)
    log.info("Done.")


if __name__ == "__main__":
    main()
