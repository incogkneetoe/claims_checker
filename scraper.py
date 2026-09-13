#!/usr/bin/env python3
"""Claim Watch scraper.

Scrapes open class action settlements from Top Class Actions and
ClassAction.org, matches them against a vendor list, and merges results
into docs/data.json (served by GitHub Pages).

No API keys required. Safe to re-run: existing entries keep their
firstSeen date, and a source that fails to parse simply contributes
nothing that day instead of wiping data.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(ROOT, "docs", "data.json")
VENDORS_PATH = os.path.join(ROOT, "vendors.txt")

# Easiest way to manage your vendor list: a secret GitHub Gist.
# Create one at gist.github.com with your vendors (one per line), open its
# Raw view, and paste the URL here WITHOUT the long revision hash, e.g.
#   https://gist.githubusercontent.com/<user>/<gist_id>/raw/vendors.txt
# so it always serves the latest edit. A pastebin.com/raw/XXXX URL works too.
# If empty, the VENDORS Actions secret is used, then vendors.txt.
VENDORS_URL = "https://docs.google.com/spreadsheets/d/1jCjEKiU8iKn5aLoVkl1PLQI9H-kzmLx_BIngiMZz4OA/export?format=csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DIAG = []  # per-run diagnostics, embedded in data.json for debugging

DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},?\s+\d{4}"
)
MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|M|B))?", re.I)


def log(msg):
    print(f"[claim-watch] {msg}", flush=True)


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        DIAG.append(f"HTTP {r.status_code} len={len(r.text)} {url}")
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        DIAG.append(f"FETCH FAIL {type(e).__name__} {url}")
        raise


def slug_for(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def find_deadline(text):
    """Return ISO date of the first date that appears near the word deadline,
    else the first date in the text, else None."""
    lower = text.lower()
    idx = lower.find("deadline")
    windows = []
    if idx != -1:
        windows.append(text[idx : idx + 160])
    windows.append(text)
    for w in windows:
        m = DATE_RE.search(w)
        if m:
            try:
                return dateparser.parse(m.group(0)).date().isoformat()
            except (ValueError, OverflowError):
                continue
    return None


def find_payout(text):
    m = MONEY_RE.search(text)
    return m.group(0) if m else None


def scrape_classaction_org():
    """ClassAction.org keeps a structured list of open settlements."""
    out = []
    try:
        html = fetch("https://www.classaction.org/settlements")
    except Exception as e:  # noqa: BLE001
        log(f"classaction.org fetch failed: {e}")
        return out
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    anchors = soup.select("a[href*='/settlements/']")
    DIAG.append(
        "cao anchors: %d, samples: %r"
        % (len(anchors), [a.get_text(" ", strip=True)[:40] for a in anchors[:8]])
    )
    for a in anchors:
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        tl = title.lower()
        if not href or len(title) < 8:
            continue
        if tl.startswith("list of") or tl in (
            "settlements",
            "open settlements",
            "closed settlements",
            "view all",
        ):
            continue
        if href.startswith("/"):
            href = "https://www.classaction.org" + href
        if href.rstrip("/").endswith(("/settlements", "/open", "/closed")):
            continue
        if href in seen:
            continue
        seen.add(href)
        container = a.find_parent(["article", "li", "section", "div"])
        ctx = container.get_text(" ", strip=True)[:800] if container else title
        out.append(
            {
                "id": "cao-" + slug_for(href),
                "title": title[:140],
                "description": ctx[:220],
                "deadline": find_deadline(ctx),
                "payout": find_payout(ctx),
                "claimUrl": href,
                "source": "ClassAction.org",
            }
        )
    log(f"classaction.org: {len(out)} entries")
    return out


def scrape_topclassactions():
    """Top Class Actions 'open settlements' category articles, first 3 pages."""
    out = []
    base = (
        "https://topclassactions.com/category/lawsuit-settlements/"
        "open-lawsuit-settlements/"
    )
    for page in range(1, 4):
        url = base if page == 1 else f"{base}page/{page}/"
        try:
            html = fetch(url)
        except Exception as e:  # noqa: BLE001
            log(f"topclassactions page {page} fetch failed: {e}")
            break
        soup = BeautifulSoup(html, "html.parser")
        heads = [
            h
            for h in soup.select("h2 a[href], h3 a[href]")
            if "topclassactions.com" in h.get("href", "")
        ]
        if page == 1:
            DIAG.append(
                "tca anchors: %d, samples: %r"
                % (len(heads), [h.get_text(" ", strip=True)[:40] for h in heads[:8]])
            )
        for h in heads:
            href = h.get("href", "")
            title = h.get_text(" ", strip=True)
            if len(title) < 12:
                continue
            container = h.find_parent(["article", "li", "div"])
            ctx = container.get_text(" ", strip=True)[:800] if container else title
            out.append(
                {
                    "id": "tca-" + slug_for(href),
                    "title": title[:140],
                    "description": ctx[:220],
                    "deadline": find_deadline(ctx),
                    "payout": find_payout(ctx) or find_payout(title),
                    "claimUrl": href,
                    "source": "Top Class Actions",
                }
            )
    # dedupe by id
    uniq = {e["id"]: e for e in out}
    out = list(uniq.values())
    log(f"topclassactions: {len(out)} entries")
    return out


def scrape_tca_feed():
    """WordPress RSS feed for TCA's open settlements category. Often
    reachable when the HTML pages are bot-blocked."""
    out = []
    url = (
        "https://topclassactions.com/category/lawsuit-settlements/"
        "open-lawsuit-settlements/feed/"
    )
    try:
        xml_text = fetch(url)
    except Exception:  # noqa: BLE001
        return out
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        DIAG.append(f"tca feed parse error: {e}")
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        if not title or not link:
            continue
        ctx = title + " " + desc
        out.append(
            {
                "id": "tca-" + slug_for(link),
                "title": title[:140],
                "description": desc[:220] or title,
                "deadline": find_deadline(ctx),
                "payout": find_payout(ctx),
                "claimUrl": link,
                "source": "Top Class Actions",
            }
        )
    DIAG.append(f"tca feed items: {len(out)}")
    log(f"topclassactions feed: {len(out)} entries")
    return out


def load_vendors():
    """Vendor list priority: VENDORS_URL (gist/pastebin raw), then VENDORS
    env var (Actions secret), then vendors.txt. One vendor per line;
    anything after a comma (like year ranges) is ignored for matching."""
    raw = ""
    if VENDORS_URL:
        try:
            raw = fetch(VENDORS_URL)
            log("vendors loaded from URL")
        except Exception as e:  # noqa: BLE001
            log(f"vendor URL fetch failed, falling back: {e}")
    if not raw.strip():
        raw = os.environ.get("VENDORS", "")
    if not raw.strip() and os.path.exists(VENDORS_PATH):
        with open(VENDORS_PATH, encoding="utf-8") as f:
            raw = f.read()
    vendors = []
    for line in raw.splitlines():
        name = line.split(",")[0].strip()
        if name and not name.startswith("#"):
            vendors.append(name)
    log(f"vendors loaded: {len(vendors)}")
    return vendors


def vendor_match(entry, vendors):
    hay = (entry.get("title", "") + " " + entry.get("description", "")).lower()
    return any(v.lower() in hay for v in vendors)


def load_existing():
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            log("existing data.json unreadable, starting fresh")
    return {"generated": None, "settlements": []}


def main():
    vendors = load_vendors()
    tca = scrape_topclassactions()
    if not tca:
        tca = scrape_tca_feed()
    scraped = scrape_classaction_org() + tca
    if not scraped:
        log("WARNING: both sources returned nothing; keeping existing data as-is")

    existing = load_existing()
    by_id = {s["id"]: s for s in existing.get("settlements", [])}
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    for e in scraped:
        e["vendorMatch"] = vendor_match(e, vendors)
        if e["id"] in by_id:
            old = by_id[e["id"]]
            e["firstSeen"] = old.get("firstSeen", now_iso)
            e["dismissedHint"] = old.get("dismissedHint", False)
            # keep a previously found deadline/payout if this pass lost it
            e["deadline"] = e["deadline"] or old.get("deadline")
            e["payout"] = e["payout"] or old.get("payout")
        else:
            e["firstSeen"] = now_iso
        by_id[e["id"]] = e

    # prune entries whose deadline passed more than 30 days ago, plus junk
    pruned = []
    for s in by_id.values():
        if s.get("title", "").lower().startswith("list of"):
            continue
        d = s.get("deadline")
        if d:
            try:
                if dateparser.parse(d).date() < (now - timedelta(days=30)).date():
                    continue
            except (ValueError, OverflowError):
                pass
        pruned.append(s)

    pruned.sort(key=lambda s: s.get("firstSeen", ""), reverse=True)

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated": now_iso,
                "vendors": vendors,
                "settlements": pruned,
                "debug": DIAG,
            },
            f,
            indent=1,
        )
    log(f"wrote {len(pruned)} settlements to docs/data.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
