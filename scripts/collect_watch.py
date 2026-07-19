#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import feedparser
except ModuleNotFoundError:  # pragma: no cover - GitHub Actions installs it.
    feedparser = None


DEFAULT_CONFIG = Path("config/watches.yml")
DEFAULT_OUTPUT_ROOT = Path("web/reports")
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36 paper-watch/1.0"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
}
ARXIV_API_URL = "https://export.arxiv.org/api/query"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
DBLP_API_URL = "https://dblp.org/search/publ/api"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
DASH_TRANSLATION = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-"})
OUTPUT_FIELDS = [
    "relevance_label",
    "topic_label",
    "title",
    "source",
    "link",
    "matched_ai_terms",
    "matched_social_terms",
]
TOPIC_PRIORITY = [
    "emotional_social",
    "gender_equality",
    "education_learning",
    "organization_work",
    "transport_automation",
    "health_mental_health",
    "consumer_marketing",
    "mind_acceptance",
    "governance_society",
    "technical_hci",
    "other",
]
TOPIC_TERMS = {
    "emotional_social": [
        "companion",
        "companions",
        "companionship",
        "intimacy",
        "self-disclosure",
        "self disclosure",
        "disclosure",
        "loneliness",
        "emotion",
        "emotional",
        "affect",
        "empathy",
        "empathetic",
        "social support",
        "emotional support",
        "attachment",
        "closeness",
        "friendship",
        "relationship",
        "relationships",
        "relational",
        "reciprocity",
    ],
    "gender_equality": ["gender", "equality", "inequality", "inequal", "equal", "bias", "stereotype", "discrimination"],
    "education_learning": [
        "education",
        "educational",
        "learning",
        "learner",
        "pedagogical",
        "tutor",
        "student",
        "teaching",
        "programming learning",
        "computational thinking",
    ],
    "organization_work": [
        "employee",
        "coworker",
        "workplace",
        "job",
        "gig",
        "platform work",
        "firm",
        "organization",
        "AI-augmented work",
        "job crafting",
    ],
    "transport_automation": ["vehicle", "driving", "autonomous vehicle", "automated vehicle", "in-vehicle", "air traffic", "takeover", "mixed traffic"],
    "health_mental_health": [
        "depression",
        "mental health",
        "therapeutic",
        "medical",
        "clinical",
        "healthcare",
        "health",
        "fitness",
        "well-being",
        "wellbeing",
        "elderly care",
        "caregiver",
        "meaning in life",
        "psychological richness",
    ],
    "consumer_marketing": ["consumer", "brand", "marketing", "advertising", "retail", "service", "loyalty", "purchase", "customer", "tourism", "commerce", "streamer"],
    "mind_acceptance": [
        "anthropomorphism",
        "anthropomorphic",
        "mind",
        "agency",
        "acceptance",
        "acceptability",
        "risk perception",
        "moral-mind",
        "transparency",
        "explainability",
        "fairness",
        "algorithm aversion",
        "cognitive capability",
    ],
    "governance_society": ["governance", "policy", "regulation", "society", "societal", "public", "responsibility", "social responsibility", "community"],
    "technical_hci": ["accessibility", "interface", "haptic", "VR", "virtual reality", "assistive", "object search", "prototype", "gesture"],
}
TRANSPORT_DRIVER_TERMS = ["driver", "drivers"]
TRANSPORT_CONTEXT_TERMS = ["vehicle", "vehicles", "driving", "traffic", "in-vehicle", "autonomous", "automated"]


@dataclass(frozen=True)
class SourceFailure:
    source: str
    source_type: str
    reason: str
    fallback_used: str = ""


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_dashes(value: str) -> str:
    return (value or "").translate(DASH_TRANSLATION)


def strip_html(value: str) -> str:
    return normalize_space(re.sub(r"<[^>]+>", " ", html.unescape(value or "")))


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()


def load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request_bytes(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def request_json(url: str, timeout: float = 30) -> dict[str, Any]:
    return json.loads(request_bytes(url, timeout=timeout).decode("utf-8"))


def parse_datetime(value: str) -> dt.datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)
    if re.fullmatch(r"\d{4}", text):
        return dt.datetime(int(text), 1, 1, tzinfo=dt.timezone.utc)
    return None


def date_to_iso(value: str | int | None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, int):
        return f"{value:04d}-01-01"
    parsed = parse_datetime(str(value))
    return parsed.date().isoformat() if parsed else str(value)


def within_since(value: str, now: dt.datetime, since_days: int) -> bool:
    parsed = parse_datetime(value)
    if not parsed:
        return True
    return parsed >= now - dt.timedelta(days=since_days)


AVAILABLE_ONLINE_DATE_PATTERN = re.compile(
    r"\bavailable\s+online\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b", flags=re.IGNORECASE
)


def reliable_entry_date(value: str) -> str:
    """Return an entry-level ISO date, excluding ambiguous year/month-only metadata."""
    text = (value or "").strip()
    if not text or re.fullmatch(r"\d{4}(?:-\d{2})?", text):
        return ""
    parsed = parse_datetime(text)
    return parsed.date().isoformat() if parsed else ""


def available_online_date(summary_slice: str) -> str:
    match = AVAILABLE_ONLINE_DATE_PATTERN.search(strip_html(summary_slice))
    if not match:
        return ""
    try:
        return dt.datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
    except ValueError:
        return ""


def keyword_regex(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(normalize_dashes(keyword))
    left = r"(?<![A-Za-z0-9])"
    right = r"(?![A-Za-z0-9])"
    return re.compile(f"{left}{escaped}{right}", flags=re.IGNORECASE)


def find_keyword_matches(text: str, keywords: list[str]) -> list[str]:
    normalized_text = normalize_dashes(text or "")
    matches = []
    for keyword in keywords:
        if keyword_regex(keyword).search(normalized_text):
            matches.append(keyword)
    return matches


def record_search_text(record: dict[str, Any], fields: list[str]) -> str:
    values = []
    for field in fields:
        value = record.get(field)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    return "\n".join(values)


def query_keywords(watch: dict[str, Any], config: dict[str, Any]) -> list[str]:
    if watch.get("keywords_ref"):
        return list(config[watch["keywords_ref"]])
    if watch.get("ai_object_terms_ref"):
        return list(config[watch["ai_object_terms_ref"]])
    return []


def retrieval_query_terms(watch: dict[str, Any], config: dict[str, Any], source: dict[str, Any] | None = None) -> list[str]:
    if source and source.get("query_terms_ref"):
        return list(config[source["query_terms_ref"]])
    if watch.get("query_terms_ref"):
        return list(config[watch["query_terms_ref"]])
    return query_keywords(watch, config)


def query_social_terms(watch: dict[str, Any], config: dict[str, Any]) -> list[str]:
    if watch.get("relationship_markers_ref"):
        return list(config[watch["relationship_markers_ref"]])
    if watch.get("high_relevance_markers_ref"):
        return list(config.get(watch["high_relevance_markers_ref"], []))
    return []


def topic_label_for_title(title: str) -> str:
    for topic in TOPIC_PRIORITY:
        if topic == "other":
            continue
        terms = TOPIC_TERMS[topic]
        if topic == "transport_automation":
            direct_matches = find_keyword_matches(title, terms)
            driver_matches = find_keyword_matches(title, TRANSPORT_DRIVER_TERMS)
            context_matches = find_keyword_matches(title, TRANSPORT_CONTEXT_TERMS)
            if direct_matches or (driver_matches and context_matches):
                return topic
            continue
        if find_keyword_matches(title, terms):
            return topic
    return "other"


def title_only_relevance(record: dict[str, Any], ai_terms: list[str], social_terms: list[str]) -> dict[str, Any]:
    title_text = str(record.get("title") or "")

    matched_ai = find_keyword_matches(title_text, ai_terms)
    matched_social = find_keyword_matches(title_text, social_terms)

    if matched_ai and matched_social:
        label = "high"
    elif matched_ai:
        label = "AI"
    else:
        label = "reject"

    return {
        "relevance_label": label,
        "topic_label": topic_label_for_title(title_text),
        "matched_ai_terms": matched_ai,
        "matched_social_terms": matched_social,
        "matched_keywords": list(dict.fromkeys([*matched_ai, *matched_social])),
    }


def layered_ai_social_relevance(record: dict[str, Any], watch: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return title_only_relevance(
        record,
        list(config[watch["ai_object_terms_ref"]]),
        list(config[watch["relationship_markers_ref"]]),
    )


def broad_ai_title_relevance(record: dict[str, Any], watch: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return title_only_relevance(
        record,
        list(config[watch["keywords_ref"]]),
        list(config.get(watch.get("high_relevance_markers_ref", ""), [])),
    )


def classify_relevance(record: dict[str, Any], watch: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    if watch["relevance_mode"] == "layered_ai_social":
        result = layered_ai_social_relevance(record, watch, config)
        if result["relevance_label"] == "reject":
            return None
        record.update(result)
        record["part_name"] = watch["part_name"]
        return record
    if watch["relevance_mode"] == "broad_ai":
        result = broad_ai_title_relevance(record, watch, config)
        if result["relevance_label"] == "reject":
            return None
        record.update(result)
        record["part_name"] = watch["part_name"]
        return record

    keywords = list(config[watch["keywords_ref"]])
    title_matches = find_keyword_matches(str(record.get("title") or ""), keywords)
    context_matches = find_keyword_matches(
        record_search_text(record, ["summary_slice", "source", "categories", "venue"]),
        keywords,
    )
    matched = list(dict.fromkeys([*title_matches, *context_matches]))
    if not matched:
        return None

    if watch["relevance_mode"] == "strict":
        label = "high" if title_matches else "medium"
    else:
        markers = list(config.get(watch.get("high_relevance_markers_ref", ""), []))
        marker_matches = find_keyword_matches(
            record_search_text(record, ["title", "summary_slice", "source", "categories", "venue"]),
            markers,
        )
        if marker_matches:
            label = "high"
        elif title_matches:
            label = "medium"
        else:
            label = "low"
        record["matched_markers"] = marker_matches

    record["matched_keywords"] = matched
    record["relevance_label"] = label
    record["part_name"] = watch["part_name"]
    return record


def normalized_url(value: str) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def normalized_title(value: str) -> str:
    return normalize_space(re.sub(r"[^a-z0-9]+", " ", html.unescape(value).lower()))


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for record in records:
        key = record_identity(record)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def parse_feed_entries(xml_data: bytes, source: dict[str, Any], feed_url: str = "") -> list[dict[str, Any]]:
    entries = parse_feedparser_entries(xml_data, source, feed_url)
    if entries:
        return entries

    root = ET.fromstring(xml_data)
    entries = []
    if root.tag.endswith("rss") or root.find("./channel") is not None:
        for item in root.findall("./channel/item"):
            authors = [
                strip_html(author.text or "")
                for author in item.findall("./author")
                if author.text
            ]
            entries.append(
                normalize_record(
                    {
                        "title": first_text(item, ["title"]),
                        "authors": authors,
                        "source": source["name"],
                        "source_type": source.get("source_type", "feed"),
                        "date": first_text(item, ["pubDate", "dc:date"]),
                        "link": first_text(item, ["link", "guid"]),
                        "summary_slice": first_text(item, ["description", "summary"]),
                    }
                )
            )
    else:
        for entry in root.findall("atom:entry", ATOM_NS):
            authors = [
                normalize_space(author.findtext("atom:name", default="", namespaces=ATOM_NS))
                for author in entry.findall("atom:author", ATOM_NS)
            ]
            link = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
            for link_node in entry.findall("atom:link", ATOM_NS):
                if link_node.attrib.get("href"):
                    link = link_node.attrib["href"]
                    break
            entries.append(
                normalize_record(
                    {
                        "title": entry.findtext("atom:title", default="", namespaces=ATOM_NS),
                        "authors": [author for author in authors if author],
                        "source": source["name"],
                        "source_type": source.get("source_type", "feed"),
                        "date": entry.findtext("atom:published", default="", namespaces=ATOM_NS)
                        or entry.findtext("atom:updated", default="", namespaces=ATOM_NS),
                        "link": link,
                        "summary_slice": entry.findtext("atom:summary", default="", namespaces=ATOM_NS)
                        or entry.findtext("atom:content", default="", namespaces=ATOM_NS),
                    }
                )
            )
    return [entry for entry in entries if entry.get("title")]


def parse_feedparser_entries(xml_data: bytes, source: dict[str, Any], feed_url: str = "") -> list[dict[str, Any]]:
    if feedparser is None:
        return []
    if xml_data:
        parsed = feedparser.parse(xml_data)
    else:
        parsed = feedparser.parse(feed_url, request_headers=HTTP_HEADERS)
    entries = []
    for entry in parsed.entries:
        authors = []
        for author in entry.get("authors", []) or []:
            if isinstance(author, dict):
                authors.append(author.get("name", ""))
            else:
                authors.append(str(author))
        if not authors and entry.get("author"):
            authors = [entry.get("author")]
        link = entry.get("link", "")
        if not link and str(entry.get("id", "")).startswith(("http://", "https://")):
            link = entry.get("id", "")
        record = normalize_record(
            {
                "title": entry.get("title", ""),
                "authors": authors,
                "source": source["name"],
                "source_type": source.get("source_type", "feed"),
                "date": entry.get("published", "") or entry.get("updated", ""),
                "link": link,
                "summary_slice": entry.get("summary", "") or entry.get("description", ""),
            }
        )
        if record.get("title") and record.get("link"):
            entries.append(record)
    return entries


def first_text(node: ET.Element, names: list[str]) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text
    return ""


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    authors = record.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    date = date_to_iso(record.get("date") or record.get("published") or record.get("year"))
    return {
        "title": strip_html(str(record.get("title") or "")),
        "authors": [normalize_space(str(author)) for author in authors if normalize_space(str(author))],
        "source": normalize_space(str(record.get("source") or "")),
        "source_type": normalize_space(str(record.get("source_type") or "")),
        "date": date,
        "year": (date[:4] if date else str(record.get("year") or "")),
        "link": normalize_space(str(record.get("link") or record.get("paper_url") or "")),
        "doi": normalize_space(str(record.get("doi") or "")),
        "summary_slice": strip_html(str(record.get("summary_slice") or record.get("summary") or "")),
        "categories": [str(item) for item in record.get("categories", []) if item],
        "venue": normalize_space(str(record.get("venue") or "")),
    }


def fetch_feed(source: dict[str, Any]) -> list[dict[str, Any]]:
    rss_url = str(source.get("rss_url") or "").strip()
    if not rss_url:
        raise ValueError("RSS/Atom URL is not configured")
    return parse_feed_entries(request_bytes(rss_url), source, rss_url)


def openalex_abstract_text(work: dict[str, Any]) -> str:
    inverted = work.get("abstract_inverted_index")
    if not isinstance(inverted, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted.items():
        if isinstance(indexes, list):
            positions.extend((int(index), str(word)) for index in indexes)
    return normalize_space(" ".join(word for _, word in sorted(positions)))


def fetch_openalex(source: dict[str, Any], watch: dict[str, Any], config: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    queries = [str(source.get("name"))] if source.get("source_type") == "journal" else retrieval_query_terms(watch, config, source)
    records = []
    for query in queries:
        if not query:
            continue
        records.extend(fetch_openalex_query(query, source, since_days, now))
    return dedupe_records(records)


def fetch_openalex_query(query: str, source: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    params = {
        "search": str(query),
        "per-page": "25",
        "sort": "publication_date:desc",
    }
    url = f"{OPENALEX_WORKS_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url)
    records = []
    for work in data.get("results", []):
        source_name = (
            ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
            or source.get("name")
            or "OpenAlex"
        )
        record = normalize_record(
            {
                "title": work.get("title", ""),
                "authors": [
                    ((authorship.get("author") or {}).get("display_name") or "")
                    for authorship in work.get("authorships", [])
                ],
                "source": source_name,
                "source_type": source.get("source_type", "api"),
                "date": work.get("publication_date") or work.get("publication_year"),
                "link": work.get("doi") or work.get("id") or "",
                "doi": str(work.get("doi") or "").replace("https://doi.org/", ""),
                "summary_slice": openalex_abstract_text(work),
                "categories": [concept.get("display_name") for concept in work.get("concepts", [])[:8] if concept.get("display_name")],
            }
        )
        if within_since(record["date"], now, since_days):
            records.append(record)
    return records


def fetch_crossref(source: dict[str, Any], watch: dict[str, Any], config: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    queries = [str(source.get("name"))] if source.get("source_type") == "journal" else retrieval_query_terms(watch, config, source)
    records = []
    for query in queries:
        if not query:
            continue
        records.extend(fetch_crossref_query(query, source, since_days, now))
    return dedupe_records(records)


def fetch_crossref_query(query: str, source: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    params = {
        "query.container-title" if source.get("source_type") == "journal" else "query": query,
        "rows": "25",
        "sort": "published",
        "order": "desc",
    }
    url = f"{CROSSREF_WORKS_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url)
    records = []
    for item in (data.get("message") or {}).get("items", []):
        title = normalize_space(" ".join(str(part) for part in item.get("title", []) if part))
        container = normalize_space(" ".join(str(part) for part in item.get("container-title", []) if part))
        date_parts = ((item.get("published-print") or item.get("published-online") or item.get("issued") or {}).get("date-parts") or [[]])[0]
        date = "-".join(f"{int(part):02d}" if index else f"{int(part):04d}" for index, part in enumerate(date_parts) if part)
        record = normalize_record(
            {
                "title": title,
                "authors": [
                    normalize_space(f"{author.get('given', '')} {author.get('family', '')}")
                    for author in item.get("author", [])[:12]
                ],
                "source": container or source.get("name") or "Crossref",
                "source_type": source.get("source_type", "api"),
                "date": date,
                "link": item.get("URL", ""),
                "doi": item.get("DOI", ""),
                "summary_slice": item.get("abstract", ""),
                "categories": item.get("subject", [])[:8],
            }
        )
        if title and within_since(record["date"], now, since_days):
            records.append(record)
    return records


def fetch_semantic_scholar(source: dict[str, Any], watch: dict[str, Any], config: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    records = []
    for query in retrieval_query_terms(watch, config, source):
        if not query:
            continue
        records.extend(fetch_semantic_scholar_query(query, source, since_days, now))
    return dedupe_records(records)


def fetch_semantic_scholar_query(query: str, source: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "limit": "25",
        "fields": "title,abstract,authors,year,publicationDate,url,venue,fieldsOfStudy",
    }
    url = f"{SEMANTIC_SCHOLAR_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    data = request_json(url)
    records = []
    for item in data.get("data", []):
        record = normalize_record(
            {
                "title": item.get("title", ""),
                "authors": [author.get("name", "") for author in item.get("authors", []) if isinstance(author, dict)],
                "source": item.get("venue") or source.get("name") or "Semantic Scholar",
                "source_type": source.get("source_type", "api"),
                "date": item.get("publicationDate") or item.get("year"),
                "link": item.get("url", ""),
                "summary_slice": item.get("abstract", ""),
                "categories": item.get("fieldsOfStudy") or [],
            }
        )
        if within_since(record["date"], now, since_days):
            records.append(record)
    return records


def parse_arxiv_entries(xml_data: bytes, source: dict[str, Any]) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_data)
    records = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        link = entry.findtext("atom:id", default="", namespaces=ARXIV_NS)
        categories = [
            category.attrib.get("term", "")
            for category in entry.findall("atom:category", ARXIV_NS)
            if category.attrib.get("term")
        ]
        records.append(
            normalize_record(
                {
                    "title": entry.findtext("atom:title", default="", namespaces=ARXIV_NS),
                    "authors": [
                        author.findtext("atom:name", default="", namespaces=ARXIV_NS)
                        for author in entry.findall("atom:author", ARXIV_NS)
                    ],
                    "source": source["name"],
                    "source_type": source.get("source_type", "preprint"),
                    "date": entry.findtext("atom:published", default="", namespaces=ARXIV_NS),
                    "link": link,
                    "summary_slice": entry.findtext("atom:summary", default="", namespaces=ARXIV_NS),
                    "categories": categories,
                }
            )
        )
    return records


def fetch_arxiv(source: dict[str, Any], watch: dict[str, Any], config: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    category = source.get("arxiv_category", "")
    search_query = f"cat:{category}" if category else "all:AI"
    max_results = int(source.get("max_results") or 25)
    return dedupe_records(fetch_arxiv_query(search_query, source, since_days, now, max_results=max_results))


def fetch_arxiv_query(search_query: str, source: dict[str, Any], since_days: int, now: dt.datetime, max_results: int = 25) -> list[dict[str, Any]]:
    params = {
        "search_query": search_query,
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "lastUpdatedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
    return [record for record in parse_arxiv_entries(request_bytes(url), source) if within_since(record["date"], now, since_days)]


def fetch_dblp(source: dict[str, Any], watch: dict[str, Any], config: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    toc_patterns = source.get("dblp_toc_patterns") or []
    if toc_patterns:
        return fetch_dblp_toc(source)
    if source.get("api") == "dblp_index":
        return fetch_dblp_index_toc(source)
    queries = retrieval_query_terms(watch, config, source)
    if not queries:
        queries = [""]
    records = []
    for query_term in queries:
        query = normalize_space(f'{source["name"]} {query_term}')
        records.extend(fetch_dblp_query(query, source, since_days, now))
    return dedupe_records(records)


def fetch_dblp_toc(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch one or more exact DBLP proceedings tables of contents.

    A TOC is a venue/year inventory, so its records must not be constrained by
    the watch lookback window. The caller controls when a venue/year is checked.
    """
    toc_keys = []
    years = source.get("years") or []
    patterns = source.get("dblp_toc_patterns") or []
    for year in years:
        for pattern in patterns:
            toc_keys.append(str(pattern).format(year=year))
    return fetch_dblp_toc_keys(source, toc_keys)


def toc_key_from_dblp_href(href: str) -> str:
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin("https://dblp.org", href))
    path = parsed.path.strip("/")
    if not path.startswith("db/") or not path.endswith(".html"):
        return ""
    return f"{path[:-5]}.bht"


def page_title(html_text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    return normalize_space(strip_html(match.group(1))) if match else ""


def discover_dblp_index_toc_keys(source: dict[str, Any]) -> list[str]:
    index_url = str(source.get("dblp_index_url") or "")
    href_template = str(source.get("dblp_index_href_regex") or "")
    include_terms = [str(term).lower() for term in source.get("include_toc_title_terms", [])]
    exclude_terms = [str(term).lower() for term in source.get("exclude_toc_title_terms", [])]
    if not index_url or not href_template or not include_terms:
        raise ValueError("DBLP index source requires URL, href regex, and include title terms")

    index_html = request_bytes(index_url).decode("utf-8", errors="replace")
    toc_keys = []
    for year in source.get("years") or []:
        href_regex = re.compile(href_template.format(year=year), flags=re.IGNORECASE)
        hrefs = list(dict.fromkeys(re.findall(r'''href=["']([^"']+)["']''', index_html, flags=re.IGNORECASE)))
        for href in hrefs:
            absolute_href = urllib.parse.urljoin(index_url, html.unescape(href))
            if not href_regex.search(urllib.parse.urlsplit(absolute_href).path):
                continue
            toc_key = toc_key_from_dblp_href(absolute_href)
            if not toc_key:
                continue
            toc_html = request_bytes(absolute_href).decode("utf-8", errors="replace")
            title = page_title(toc_html).lower()
            if not any(term in title for term in include_terms):
                continue
            if any(term in title for term in exclude_terms):
                continue
            toc_keys.append(toc_key)
    return list(dict.fromkeys(toc_keys))


def fetch_dblp_index_toc(source: dict[str, Any]) -> list[dict[str, Any]]:
    return fetch_dblp_toc_keys(source, discover_dblp_index_toc_keys(source))


def fetch_dblp_toc_keys(source: dict[str, Any], toc_keys: list[str]) -> list[dict[str, Any]]:
    records = []
    for toc_key in toc_keys:
        start = 0
        page_size = 1000
        while True:
            params = {"q": f"toc:{toc_key}:", "format": "json", "h": str(page_size), "f": str(start)}
            data = request_json(f"{DBLP_API_URL}?{urllib.parse.urlencode(params)}")
            hit_container = (data.get("result") or {}).get("hits") or {}
            hits = hit_container.get("hit", [])
            if isinstance(hits, dict):
                hits = [hits]
            for hit in hits:
                info = hit.get("info", {})
                record = normalize_record(
                    {
                        "title": info.get("title", ""),
                        "authors": parse_dblp_authors(info),
                        "source": source["name"],
                        "source_type": source.get("source_type", "conference"),
                        "date": str(info.get("year") or ""),
                        "link": info.get("url") or f"https://dblp.org/rec/{info.get('key', '')}",
                        "doi": info.get("doi", ""),
                        "summary_slice": "",
                        "categories": [source.get("group", source["name"])],
                        "venue": info.get("venue") or source["name"],
                    }
                )
                if record["title"]:
                    records.append(record)
            total = int(hit_container.get("@total", 0) or 0)
            if not hits or start + len(hits) >= total:
                break
            start += len(hits)
    return dedupe_records(records)


def fetch_dblp_query(query: str, source: dict[str, Any], since_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    params = {"q": query, "format": "json", "h": "25"}
    data = request_json(f"{DBLP_API_URL}?{urllib.parse.urlencode(params)}")
    records = []
    for hit in ((data.get("result") or {}).get("hits") or {}).get("hit", []):
        info = hit.get("info", {})
        record = normalize_record(
            {
                "title": info.get("title", ""),
                "authors": parse_dblp_authors(info),
                "source": info.get("venue") or source["name"],
                "source_type": source.get("source_type", "conference"),
                "date": info.get("year", ""),
                "link": info.get("url") or f"https://dblp.org/rec/{info.get('key', '')}",
                "doi": info.get("doi", ""),
                "summary_slice": "",
                "categories": [source["name"]],
                "venue": info.get("venue") or source["name"],
            }
        )
        if record["title"] and within_since(record["date"], now, since_days):
            records.append(record)
    return records


def parse_dblp_authors(info: dict[str, Any]) -> list[str]:
    raw = info.get("authors", {}).get("author", []) if isinstance(info.get("authors"), dict) else []
    if isinstance(raw, dict):
        raw = [raw]
    return [str(author.get("text") if isinstance(author, dict) else author) for author in raw if author]


def fetch_source(source: dict[str, Any], watch: dict[str, Any], config: dict[str, Any], since_days: int, now: dt.datetime) -> tuple[list[dict[str, Any]], list[SourceFailure]]:
    failures: list[SourceFailure] = []
    try:
        if source.get("rss_url") is not None:
            return [record for record in fetch_feed(source) if within_since(record["date"], now, since_days)], failures
        api = source.get("api")
        if api == "arxiv":
            return fetch_arxiv(source, watch, config, since_days, now), failures
        if api in {"dblp", "dblp_index"}:
            return fetch_dblp(source, watch, config, since_days, now), failures
        if api == "openalex":
            return fetch_openalex(source, watch, config, since_days, now), failures
        if api == "crossref":
            return fetch_crossref(source, watch, config, since_days, now), failures
        if api == "semantic_scholar":
            return fetch_semantic_scholar(source, watch, config, since_days, now), failures
        raise ValueError("No fetch method configured")
    except Exception as exc:
        failures.append(SourceFailure(source.get("name", ""), source.get("source_type", ""), str(exc)))

    records = []
    for fallback in source.get("api_fallbacks", watch.get("api_fallbacks", [])):
        try:
            fallback_source = {**source, "api": fallback, "rss_url": None}
            if fallback == "openalex":
                records.extend(fetch_openalex(fallback_source, watch, config, since_days, now))
            elif fallback == "crossref":
                records.extend(fetch_crossref(fallback_source, watch, config, since_days, now))
            else:
                failures.append(SourceFailure(source.get("name", ""), source.get("source_type", ""), f"Unsupported fallback {fallback}"))
                continue
            failures.append(SourceFailure(source.get("name", ""), source.get("source_type", ""), "RSS/primary source failed", fallback))
            break
        except Exception as exc:
            failures.append(SourceFailure(source.get("name", ""), source.get("source_type", ""), f"{fallback} fallback failed: {exc}", fallback))
    return records, failures


def record_identity(record: dict[str, Any]) -> str:
    doi = normalize_space(str(record.get("doi") or "")).lower()
    if doi:
        return f"doi:{doi}"
    link = normalized_url(str(record.get("link") or ""))
    if link:
        return f"url:{link}"
    title = normalized_title(str(record.get("title") or ""))
    year = str(record.get("year") or "")[:4]
    return f"title:{title}:{year}" if title else ""


def empty_journal_seen_state() -> dict[str, Any]:
    return {"version": 1, "sources": {}}


def load_journal_seen_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_journal_seen_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        raise ValueError(f"Invalid journal seen-state file: {path}")
    return data


def write_journal_seen_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def empty_candidate_archive() -> dict[str, Any]:
    return {"version": 1, "papers": []}


def load_candidate_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_candidate_archive()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("papers"), list):
        raise ValueError(f"Invalid candidate archive file: {path}")
    return data


def candidate_archive_identity(candidate: dict[str, Any]) -> str:
    link = normalized_url(str(candidate.get("link") or ""))
    if link:
        return f"url:{link}"
    return f"title:{normalized_title(str(candidate.get('title') or ''))}:{normalize_space(str(candidate.get('source') or '')).lower()}"


def update_candidate_archive(archive: dict[str, Any], payload: dict[str, Any], captured_at: str) -> None:
    """Retain one dashboard record per candidate while preserving its first capture time."""
    papers = archive.setdefault("papers", [])
    by_identity = {candidate_archive_identity(paper): paper for paper in papers}
    watch_id = str(payload.get("watch_id") or "")
    watch_name = str(payload.get("part_name") or watch_id)

    for candidate in payload.get("candidates", []):
        identity = candidate_archive_identity(candidate)
        if not identity or identity == "title::":
            continue
        existing = by_identity.get(identity)
        snapshot = {field: candidate.get(field, [] if field.startswith("matched_") else "") for field in OUTPUT_FIELDS}
        if existing is None:
            existing = {
                **snapshot,
                "first_captured_at": captured_at,
                "last_captured_at": captured_at,
                "watch_ids": [],
                "watch_names": [],
            }
            papers.append(existing)
            by_identity[identity] = existing
        else:
            existing.update(snapshot)
            existing["last_captured_at"] = captured_at

        if watch_id and watch_id not in existing["watch_ids"]:
            existing["watch_ids"].append(watch_id)
        if watch_name and watch_name not in existing["watch_names"]:
            existing["watch_names"].append(watch_name)


def write_candidate_archive(path: Path, archive: dict[str, Any]) -> None:
    archive["papers"] = sorted(
        archive.get("papers", []),
        key=lambda candidate: (str(candidate.get("first_captured_at") or ""), str(candidate.get("title") or "")),
        reverse=True,
    )
    write_json(path, archive)


def bootstrap_candidate_archive(
    archive: dict[str, Any], output_root: Path, active_watch_ids: set[str]
) -> None:
    """Seed a new archive from dated reports already retained in the repository."""
    if archive.get("papers"):
        return
    for json_path in sorted(output_root.glob("*/*.json")):
        if not REPORT_JSON_PATTERN.fullmatch(json_path.name):
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("watch_id") or "") not in active_watch_ids:
            continue
        update_candidate_archive(archive, payload, str(payload.get("generated_at") or ""))


def journal_record_date_policy(record: dict[str, Any]) -> tuple[str, str]:
    entry_date = reliable_entry_date(str(record.get("date") or ""))
    if entry_date:
        return "entry_date", entry_date
    online_date = available_online_date(str(record.get("summary_slice") or ""))
    if online_date:
        record["date"] = online_date
        record["year"] = online_date[:4]
        return "available_online", online_date
    return "feed_novelty", ""


def retain_journal_records(
    records: list[dict[str, Any],],
    source: dict[str, Any],
    state: dict[str, Any],
    now: dt.datetime,
    since_days: int,
    source_succeeded: bool,
) -> list[dict[str, Any]]:
    """Use precise dates when supplied; otherwise emit only entries new since the RSS baseline."""
    if not source_succeeded:
        return [
            record
            for record in records
            if journal_record_date_policy(record)[0] != "feed_novelty"
            and within_since(record["date"], now, since_days)
        ]

    sources = state.setdefault("sources", {})
    source_key = str(source.get("name") or "")
    source_state = sources.setdefault(source_key, {"initialized": False, "record_keys": []})
    previous_keys = set(source_state.get("record_keys", []))
    current_keys = set(previous_keys)
    retained = []

    for record in records:
        policy, record_date = journal_record_date_policy(record)
        if policy != "feed_novelty":
            if within_since(record_date, now, since_days):
                retained.append(record)
            continue
        identity = record_identity(record)
        if identity and source_state.get("initialized") and identity not in previous_keys:
            retained.append(record)
        if identity:
            current_keys.add(identity)

    source_state.update(
        {
            "initialized": True,
            "record_keys": sorted(current_keys),
            "last_checked_at": now.isoformat(),
        }
    )
    return retained


def conference_state_key(source: dict[str, Any], year: int | str) -> str:
    return f"{source.get('name', 'conference')}:{year}"


def empty_conference_state() -> dict[str, Any]:
    return {"version": 1, "conferences": {}}


def load_conference_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_conference_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("conferences"), dict):
        raise ValueError(f"Invalid conference state file: {path}")
    return data


def write_conference_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pending_conference_years(source: dict[str, Any], state: dict[str, Any]) -> list[int]:
    conferences = state.setdefault("conferences", {})
    years = source.get("years") or []
    return [int(year) for year in years if conferences.get(conference_state_key(source, year), {}).get("status") != "complete"]


def update_conference_state(
    state: dict[str, Any], source: dict[str, Any], records: list[dict[str, Any]], years: list[int], now: dt.datetime
) -> None:
    conferences = state.setdefault("conferences", {})
    for year in years:
        key = conference_state_key(source, year)
        previous = conferences.get(key, {})
        record_keys = sorted(
            identity
            for record in records
            if str(record.get("year") or "") == str(year)
            for identity in [record_identity(record)]
            if identity
        )
        previous_keys = set(previous.get("record_keys", []))
        check_count = int(previous.get("check_count", 0)) + 1
        new_record_count = len(set(record_keys) - previous_keys)
        removed_record_count = len(previous_keys - set(record_keys))
        unchanged = check_count >= 2 and set(record_keys) == previous_keys
        status = "complete" if unchanged else "active"
        conferences[key] = {
            "source": source.get("name", ""),
            "year": int(year),
            "publication_scope": source.get("publication_scope", "main_conference"),
            "check_count": check_count,
            "new_record_count": new_record_count,
            "removed_record_count": removed_record_count,
            "record_keys": record_keys,
            "status": status,
            "last_checked_at": now.isoformat(),
        }


def source_matches_type_filter(source: dict[str, Any], source_types: set[str] | None) -> bool:
    return source_types is None or source.get("source_type", "") in source_types


def is_conference_inventory_source(source: dict[str, Any]) -> bool:
    return source.get("source_type") == "conference" and bool(
        source.get("dblp_toc_patterns") or source.get("api") == "dblp_index"
    )


def collect_watch(
    watch: dict[str, Any],
    config: dict[str, Any],
    since_days: int,
    now: dt.datetime,
    source_types: set[str] | None = None,
    conference_state: dict[str, Any] | None = None,
    journal_seen_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = []
    raw_records = []
    source_results = []
    failures: list[SourceFailure] = []
    state = conference_state if conference_state is not None else empty_conference_state()
    selected_sources = [source for source in watch.get("sources", []) if source_matches_type_filter(source, source_types)]
    for source in selected_sources:
        if not source.get("enabled", True):
            source_results.append(
                {
                    "source": source.get("name", ""),
                    "source_type": source.get("source_type", ""),
                    "raw_count": 0,
                    "failed": False,
                    "skipped": True,
                    "skip_reason": source.get("disabled_reason", "Source is disabled"),
                    "failure_count": 0,
                }
            )
            continue
        source_to_fetch = source
        conference_years: list[int] = []
        if is_conference_inventory_source(source):
            conference_years = pending_conference_years(source, state)
            if not conference_years:
                source_results.append(
                    {
                        "source": source.get("name", ""),
                        "source_type": source.get("source_type", ""),
                        "raw_count": 0,
                        "failed": False,
                        "skipped": True,
                        "skip_reason": "Conference year already complete after two checks without new records",
                        "failure_count": 0,
                    }
                )
                continue
            source_to_fetch = {**source, "years": conference_years}

        source_records, source_failures = fetch_source(source_to_fetch, watch, config, since_days, now)
        if (
            source.get("source_type") == "journal"
            and watch.get("journal_recency_policy") == "entry_date_or_available_online_or_feed_novelty"
            and journal_seen_state is not None
        ):
            source_records = retain_journal_records(
                source_records,
                source,
                journal_seen_state,
                now,
                since_days,
                source_succeeded=not source_failures,
            )
        # An empty or failed request is not evidence that a proceedings list is stable.
        # State starts only after a successful non-empty inventory is available.
        if conference_years and source_records and not source_failures:
            update_conference_state(state, source, source_records, conference_years, now)
        if watch["relevance_mode"] == "layered_ai_social":
            for record in source_records:
                record.update(layered_ai_social_relevance(record, watch, config))
                record["part_name"] = watch["part_name"]
        elif watch["relevance_mode"] == "broad_ai":
            for record in source_records:
                record.update(broad_ai_title_relevance(record, watch, config))
                record["part_name"] = watch["part_name"]
        failures.extend(source_failures)
        raw_records.extend(source_records)
        source_results.append(
            {
                "source": source.get("name", ""),
                "source_type": source.get("source_type", ""),
                "raw_count": len(source_records),
                "failed": bool(source_failures and not source_records),
                "skipped": False,
                "failure_count": len(source_failures),
            }
        )
        for record in source_records:
            classified = classify_relevance(record, watch, config)
            if classified:
                records.append(classified)
    records = dedupe_records(records)
    raw_records = dedupe_records(raw_records)
    records.sort(key=lambda item: (item.get("date") or "", item.get("title") or ""), reverse=True)
    raw_records.sort(key=lambda item: (item.get("date") or "", item.get("title") or ""), reverse=True)
    return {
        "watch_id": watch["id"],
        "part_name": watch["part_name"],
        "frequency": watch["frequency"],
        "generated_at": now.isoformat(),
        "since_days": since_days,
        "source_count": len(selected_sources),
        "successful_source_count": sum(1 for item in source_results if not item["failed"] and not item.get("skipped")),
        "failed_source_count": sum(1 for item in source_results if item["failed"]),
        "skipped_source_count": sum(1 for item in source_results if item.get("skipped")),
        "raw_entry_count": len(raw_records),
        "candidate_count": len(records),
        "source_results": source_results,
        "raw_records": raw_records,
        "candidates": records,
        "failed_sources": [failure.__dict__ for failure in failures],
        "conference_state": state,
    }


def author_text(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    if len(authors) <= 20:
        return ", ".join(authors[:-1]) + ", & " + authors[-1]
    return ", ".join(authors[:19]) + ", ... " + authors[-1]


def apa_like(record: dict[str, Any]) -> str:
    parts = []
    authors = author_text(record.get("authors", []))
    year = record.get("year") or ""
    if authors:
        parts.append(f"{authors} ({year})." if year else f"{authors}.")
    elif year:
        parts.append(f"({year}).")
    title = record.get("title") or ""
    if title:
        parts.append(f"{title}.")
    source = record.get("source") or record.get("venue") or ""
    if source:
        parts.append(f"*{source}*.")
    return " ".join(parts).strip()


def compact_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "relevance_label": record.get("relevance_label", ""),
        "topic_label": record.get("topic_label", ""),
        "title": record.get("title", ""),
        "source": record.get("source", ""),
        "link": record.get("link", ""),
        "matched_ai_terms": list(record.get("matched_ai_terms", [])),
        "matched_social_terms": list(record.get("matched_social_terms", [])),
    }


def compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(payload)
    compacted.pop("conference_state", None)
    if "candidates" in compacted:
        compacted["candidates"] = [compact_record(item) for item in compacted["candidates"]]
    if "raw_records" in compacted:
        compacted["raw_records"] = [compact_record(item) for item in compacted["raw_records"]]
    return compacted


def group_by_topic(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        topic = record.get("topic_label") or "other"
        grouped.setdefault(topic, []).append(record)
    return grouped


def render_record_item(item: dict[str, Any]) -> list[str]:
    return [
        f"### {item.get('title', 'Untitled')}",
        f"- title: {item.get('title', '')}",
        f"- source: {item.get('source', '')}",
        f"- link: {item.get('link', '')}",
        f"- matched_ai_terms: {', '.join(item.get('matched_ai_terms', []))}",
        f"- matched_social_terms: {', '.join(item.get('matched_social_terms', []))}",
        f"- topic_label: {item.get('topic_label', '')}",
        "",
    ]


def render_markdown(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates", [])
    high_records = [item for item in candidates if item.get("relevance_label") == "high"]
    ai_records = [item for item in candidates if item.get("relevance_label") == "AI"]
    raw_records = payload.get("raw_records", [])
    reject_count = sum(1 for item in raw_records if item.get("relevance_label") == "reject")
    lines = [
        f"# {payload['part_name']}",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Lookback days: {payload['since_days']}",
        f"- Candidate count: {payload['candidate_count']}",
        f"- High relevance count: {len(high_records)}",
        f"- AI-related only count: {len(ai_records)}",
        f"- Reject count: {reject_count}",
        "",
        "## High relevance",
        "",
    ]
    if not high_records:
        lines.extend(["No high relevance records.", ""])
    for topic in TOPIC_PRIORITY:
        topic_records = group_by_topic(high_records).get(topic, [])
        if not topic_records:
            continue
        lines.extend([f"### {topic}", ""])
        for item in topic_records:
            lines.extend(render_record_item(item))

    lines.extend(["## AI-related only", ""])
    if not ai_records:
        lines.extend(["No AI-related only records.", ""])
    for topic in TOPIC_PRIORITY:
        topic_records = group_by_topic(ai_records).get(topic, [])
        if not topic_records:
            continue
        lines.extend([f"### {topic}", ""])
        for item in topic_records:
            lines.extend(render_record_item(item))

    lines.extend(["## Reject summary", "", f"- reject_count: {reject_count}", ""])
    lines.extend(["## Failed Sources", ""])
    if not payload["failed_sources"]:
        lines.extend(["No failed sources.", ""])
    for failure in payload["failed_sources"]:
        fallback = f"; fallback_used={failure['fallback_used']}" if failure.get("fallback_used") else ""
        lines.append(f"- {failure['source']} ({failure['source_type']}): {failure['reason']}{fallback}")
    lines.append("")
    return "\n".join(lines)


def render_raw_markdown(payload: dict[str, Any]) -> str:
    raw_records = payload.get("raw_records", [])
    lines = [
        f"# {payload['part_name']} - All Fetched Records",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Lookback days: {payload['since_days']}",
        f"- Source count: {payload.get('source_count', '')}",
        f"- Raw entry count: {payload.get('raw_entry_count', len(raw_records))}",
        f"- High relevance count: {sum(1 for item in raw_records if item.get('relevance_label') == 'high')}",
        f"- AI-related only count: {sum(1 for item in raw_records if item.get('relevance_label') == 'AI')}",
        f"- Reject count: {sum(1 for item in raw_records if item.get('relevance_label') == 'reject')}",
        "",
        "## Source Results",
        "",
    ]
    for item in payload.get("source_results", []):
        status = "failed" if item.get("failed") else "ok"
        lines.append(f"- {item.get('source', '')}: {item.get('raw_count', 0)} records ({status})")
    lines.extend(["", "## All Records", ""])
    if not raw_records:
        lines.extend(["No records fetched.", ""])
    for index, item in enumerate(raw_records, start=1):
        lines.append(f"### {index}. {item.get('title', 'Untitled')}")
        lines.extend(
            [
                f"- relevance_label: {item.get('relevance_label', '')}",
                f"- topic_label: {item.get('topic_label', '')}",
                f"- title: {item.get('title', '')}",
                f"- source: {item.get('source', '')}",
                f"- link: {item.get('link', '')}",
                f"- matched_ai_terms: {', '.join(item.get('matched_ai_terms', []))}",
                f"- matched_social_terms: {', '.join(item.get('matched_social_terms', []))}",
                "",
            ]
        )
    return "\n".join(lines)


def xlsx_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def write_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = OUTPUT_FIELDS
    sheet_rows = [headers]
    for row in rows:
        compact = compact_record(row)
        sheet_rows.append(
            [
                compact.get("relevance_label", ""),
                compact.get("topic_label", ""),
                compact.get("title", ""),
                compact.get("source", ""),
                compact.get("link", ""),
                ", ".join(compact.get("matched_ai_terms", [])),
                ", ".join(compact.get("matched_social_terms", [])),
            ]
        )

    worksheet_rows = []
    for row_index, row in enumerate(sheet_rows, start=1):
        cells = []
        for col_index, value in enumerate(row):
            cell_ref = f"{column_name(col_index)}{row_index}"
            cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{xlsx_escape(value)}</t></is></c>')
        worksheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "".join(worksheet_rows)
        + "</sheetData></worksheet>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        xlsx.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        xlsx.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="All Records" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        xlsx.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        xlsx.writestr("xl/worksheets/sheet1.xml", worksheet)


def report_stem(watch: dict[str, Any], now: dt.datetime, suffix: str = "") -> str:
    stem = now.strftime(watch["date_format"])
    return f"{stem}-{suffix}" if suffix else stem


REPORT_JSON_PATTERN = re.compile(r"^(?P<period>\d{4}-\d{2}(?:-\d{2})?)(?:-(?P<scope>[a-z0-9-]+))?\.json$")


def update_report_index(output_root: Path, active_watch_ids: set[str] | None = None) -> Path:
    """Write latest watch links plus the full dated report history for the static homepage."""
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    history: list[dict[str, Any]] = []
    for json_path in output_root.glob("*/*.json"):
        match = REPORT_JSON_PATTERN.fullmatch(json_path.name)
        if not match:
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        watch_id = str(payload.get("watch_id") or "")
        part_name = str(payload.get("part_name") or "")
        if not watch_id or not part_name:
            continue
        if active_watch_ids is not None and watch_id not in active_watch_ids:
            continue

        relative_json = json_path.relative_to(output_root).as_posix()
        md_path = json_path.with_suffix(".md")
        xlsx_path = json_path.with_name(f"{json_path.stem}_all.xlsx")
        scope = match.group("scope") or "default"
        entry = {
            "watch_id": watch_id,
            "part_name": part_name,
            "frequency": str(payload.get("frequency") or ""),
            "period": match.group("period"),
            "scope": scope,
            "generated_at": str(payload.get("generated_at") or ""),
            "candidate_count": int(payload.get("candidate_count") or 0),
            "markdown": md_path.relative_to(output_root).as_posix() if md_path.exists() else "",
            "json": relative_json,
            "xlsx": xlsx_path.relative_to(output_root).as_posix() if xlsx_path.exists() else "",
        }
        history.append(entry)
        key = (watch_id, scope)
        current = latest_by_key.get(key)
        if current is None or (entry["generated_at"], entry["period"]) > (current["generated_at"], current["period"]):
            latest_by_key[key] = entry

    reports = sorted(
        latest_by_key.values(),
        key=lambda entry: (entry["watch_id"], entry["scope"]),
    )
    history.sort(key=lambda entry: (entry["generated_at"], entry["period"], entry["json"]), reverse=True)
    index_path = output_root / "index.json"
    write_json(index_path, {"reports": reports, "history": history})
    return index_path


def write_reports(
    payload: dict[str, Any], watch: dict[str, Any], output_root: Path, now: dt.datetime, suffix: str = ""
) -> tuple[Path, Path]:
    directory = output_root / watch["id"]
    stem = report_stem(watch, now, suffix)
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    all_md_path = directory / f"{stem}_all.md"
    all_xlsx_path = directory / f"{stem}_all.xlsx"
    output_payload = compact_payload(payload)
    write_json(json_path, output_payload)
    md_path.write_text(render_markdown(output_payload), encoding="utf-8")
    if "raw_records" in output_payload:
        all_md_path.write_text(render_raw_markdown(output_payload), encoding="utf-8")
        write_xlsx(all_xlsx_path, output_payload["raw_records"])
    return md_path, json_path


def selected_watches(config: dict[str, Any], selection: str) -> list[dict[str, Any]]:
    watches = config["watches"]
    if selection == "all":
        return watches
    for watch in watches:
        if watch["id"] == selection:
            return [watch]
    raise ValueError(f"Unknown watch: {selection}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect deterministic paper watch reports.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--watch", default="all")
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument(
        "--source-types",
        default="",
        help="Comma-separated source types to run, for example conference or preprint,api.",
    )
    parser.add_argument(
        "--conference-state-path",
        type=Path,
        default=None,
        help="Persistent state for annual DBLP conference checks.",
    )
    parser.add_argument("--report-suffix", default="", help="Suffix for source-scoped reports, for example conferences.")
    parser.add_argument(
        "--journal-seen-state-path",
        type=Path,
        default=None,
        help="Persistent RSS seen-state for journals without reliable per-entry dates.",
    )
    parser.add_argument("--date", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    now = (
        dt.datetime.fromisoformat(args.date).replace(tzinfo=dt.timezone.utc)
        if args.date
        else dt.datetime.now(dt.timezone.utc)
    )
    config = load_config(args.config)
    active_watch_ids = {watch["id"] for watch in config["watches"]}
    journal_seen_state_path = args.journal_seen_state_path or (args.output_root / "journal_seen_state.json")
    journal_seen_state = load_journal_seen_state(journal_seen_state_path)
    candidate_archive_path = args.output_root / "candidate_archive.json"
    candidate_archive = load_candidate_archive(candidate_archive_path)
    bootstrap_candidate_archive(candidate_archive, args.output_root, active_watch_ids)
    source_types = {value.strip() for value in args.source_types.split(",") if value.strip()} or None
    written = []
    for watch in selected_watches(config, args.watch):
        state_path = args.conference_state_path or (args.output_root / watch["id"] / "conference_state.json")
        has_selected_conference_toc = any(
            is_conference_inventory_source(source) and source_matches_type_filter(source, source_types)
            for source in watch.get("sources", [])
        )
        state = load_conference_state(state_path) if has_selected_conference_toc else None
        payload = collect_watch(
            watch,
            config,
            args.since_days,
            now,
            source_types=source_types,
            conference_state=state,
            journal_seen_state=journal_seen_state,
        )
        written.extend(write_reports(payload, watch, args.output_root, now, suffix=args.report_suffix))
        update_candidate_archive(candidate_archive, payload, str(payload.get("generated_at") or now.isoformat()))
        if state is not None:
            write_conference_state(state_path, payload["conference_state"])
    write_journal_seen_state(journal_seen_state_path, journal_seen_state)
    write_candidate_archive(candidate_archive_path, candidate_archive)
    update_report_index(args.output_root, active_watch_ids)
    for path in written:
        print(path)
    index_path = args.output_root / "index.json"
    if index_path.exists():
        print(index_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
