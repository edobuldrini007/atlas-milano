#!/usr/bin/env python3
"""ATLAS DAILY: collect topical RSS headlines for Milan.
Writes the JSON contract read by ATLAS V5. No AI claims are made.
Uses the standard library only. RSS availability/content may vary.
"""
import html
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "atlas-news.json"
NOW = datetime.now(timezone.utc)
LOOKBACK_DAYS = 45
MAX_ARTICLES = 250
QUERIES = [
    'Milano "rigenerazione urbana" when:30d',
    'Milano urbanistica PGT quartieri when:30d',
    'Milano parchi verde pubblico depavimentazione when:30d',
    'Milano metropolitana tram ciclabile mobilita when:30d',
    'Milano nuovi progetti architettura cantieri when:30d',
    'Milano casa edilizia residenziale abitare when:30d',
]
KEYWORDS = {
    "Verde": ["verde", "parco", "parchi", "giardini", "alberi", "forestazione", "depavimenta", "bosco"],
    "Nuovi progetti": ["progett", "cantiere", "cantieri", "riqualifica", "rigeneraz", "architettura", "costruzione"],
    "Mobilità": ["metro", "metropolitana", "tram", "trasport", "mobilita", "ciclab", "bicicl", "pedon"],
    "Urbanistica": ["urbanistic", "pgt", "quartier", "pianific", "scalo", "trasformaz", "rigeneraz"],
    "Spazi pubblici": ["piazza", "piazze", "spazio pubblico", "spazi pubblici", "strade", "cascina"],
    "Ambiente": ["ambiente", "clima", "inquinament", "aria", "suolo", "acqua", "lambro", "sostenibil"],
    "Casa": ["casa", "case", "abitare", "housing", "allogg", "residenz", "affitt"],
    "Cultura": ["museo", "musei", "cultur", "mostra", "teatro", "arte", "design"],
    "Infrastrutture": ["infrastruttur", "opere pubbliche", "stazione", "ferrovi", "metropolitan"],
}
NEIGHBOURHOODS = [
    "San Cristoforo", "Centro storico", "Porta Romana", "Porta Nuova", "Città Studi",
    "Scalo Romana", "San Siro", "CityLife", "Lorenteggio", "Giambellino",
    "Crescenzago", "Bicocca", "Lambrate", "Bovisa", "Brera", "Isola",
    "NoLo", "Navigli", "Dergano", "Corvetto", "Affori", "Comasina",
    "Portello", "Rubattino", "Loreto", "Monluè",
]
DISTRICT_TERMS = ["milano", "milanese", "milanesi", "milan", "nolo"] + [
    x.lower() for x in NEIGHBOURHOODS
]
EXCLUDE = [
    "inter milan", "ac milan", "calciomercato", "champions league",
    "serie a ", "serie a:", "risultati calcio",
]

def normalized(value):
    value = unicodedata.normalize("NFKD", str(value))
    return "".join(c for c in value if not unicodedata.combining(c)).casefold()

def clean(value):
    value = re.sub(r"<[^>]*>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()

def categorize(value):
    t = normalized(value)
    found = [category for category, terms in KEYWORDS.items() if any(k in t for k in terms)]
    return found[:4]

def get_date(value):
    try:
        d = parsedate_to_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

def load_existing():
    if not OUTPUT.is_file():
        return []
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return data.get("articles", []) if isinstance(data.get("articles"), list) else []
    except (json.JSONDecodeError, OSError):
        return []

def fetch_feed(query):
    params = urllib.parse.urlencode({"q": query, "hl": "it", "gl": "IT", "ceid": "IT:it"})
    url = "https://news.google.com/rss/search?" + params
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ATLAS-Milano/1.0; urban-news-reader)",
        "Accept": "application/rss+xml, application/xml, text/xml",
    })
    with urllib.request.urlopen(request, timeout=18) as response:
        data = response.read(1_600_000)
    root = ET.fromstring(data)
    if root.tag != "rss":
        raise ValueError("Unexpected RSS document")
    return root.findall("./channel/item")

def extract(item):
    title = clean(item.findtext("title"))
    link = (item.findtext("link") or "").strip()
    source = clean(item.findtext("source")) or "Google News"
    when = get_date(item.findtext("pubDate"))
    if not title or not when or not link.startswith("https://"):
        return None
    if when < NOW - timedelta(days=LOOKBACK_DAYS) or when > NOW + timedelta(days=1):
        return None
    if title.endswith(" - " + source):
        title = title[: -(len(source) + 3)].strip()
    joined = normalized(title + " " + clean(item.findtext("description")))
    if not any(key in joined for key in DISTRICT_TERMS):
        return None
    if any(term in joined for term in EXCLUDE):
        return None
    categories = categorize(title + " " + clean(item.findtext("description")))
    if not categories:
        return None
    district = next((d for d in NEIGHBOURHOODS if normalized(d) in joined), "Milano")
    return {
        "title": title[:220],
        "source": source[:90],
        "date": when.date().isoformat(),
        "url": link,
        "description": "",
        "category": categories,
        "neighborhood": district,
    }

def main():
    existing = load_existing()
    articles = []
    success = 0
    for query in QUERIES:
        try:
            raw = fetch_feed(query)
            success += 1
            added = 0
            for item in raw:
                result = extract(item)
                if result:
                    articles.append(result)
                    added += 1
            print(f"OK: {query} -> {added} relevant articles")
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"WARN: {query}: {exc}", file=sys.stderr)

    # Avoid falsely presenting old demo headlines as freshly collected news.
    seen = set()
    dedup = []
    for article in sorted(articles, key=lambda a: a["date"], reverse=True):
        key = re.sub(r"\W+", "", normalized(article["title"]))
        if not key or key in seen:
            continue
        seen.add(key)
        dedup.append(article)
    if dedup:
        # Preserve genuinely available seed links as historical archive, not as new items.
        for previous in existing:
            if not isinstance(previous, dict):
                continue
            key = re.sub(r"\W+", "", normalized(previous.get("title", "")))
            if key and key not in seen:
                seen.add(key)
                dedup.append(previous)
        dedup.sort(key=lambda a: a.get("date", ""), reverse=True)
        payload = {
            "mode": "live",
            "updated_at": NOW.isoformat(timespec="seconds"),
            "ai_tagging": False,
            "articles": dedup[:MAX_ARTICLES],
            "count": min(len(dedup), MAX_ARTICLES),
        }
    else:
        # Keep fallback and show honest preview mode if every RSS fetch is empty or fails.
        payload = {
            "mode": "seed",
            "updated_at": NOW.isoformat(timespec="seconds"),
            "ai_tagging": False,
            "articles": existing[:MAX_ARTICLES],
            "count": min(len(existing), MAX_ARTICLES),
        }
        if not existing:
            raise RuntimeError("No RSS articles and no seeded articles to display.")
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Catalog written: {payload['count']} articles; live={payload['mode']=='live'}; feeds={success}/{len(QUERIES)}")

if __name__ == "__main__":
    main()
