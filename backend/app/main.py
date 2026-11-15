from __future__ import annotations

import asyncio
import os
import re
import time
import xml.etree.ElementTree as ET

from collections import defaultdict
from typing import Any
from urllib.parse import quote

import httpx
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database as db

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

# =========================================================
# CONFIGURATION
# =========================================================

APP_VERSION = "2.1.0"

TAVILY_URL = "https://api.tavily.com/search"
OPENFDA_URL = "https://api.fda.gov/drug/label.json"
DAILYMED_URL = (
    "https://dailymed.nlm.nih.gov/"
    "dailymed/services/v2/spls.json"
)

HTTP_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
CACHE_TTL = 60 * 60 * 6

_CACHE: dict[str, tuple[float, Any]] = {}
_RATE_LIMIT: dict[str, list[float]] = {}


# =========================================================
# FASTAPI APPLICATION
# =========================================================

app = FastAPI(
    title="Medicine Buddy API",
    version=APP_VERSION,
)

origins = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if item.strip()
]

if not os.getenv("VERCEL"):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=r"https://.*\.vercel\.app$",
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )


# =========================================================
# REQUEST MODELS
# =========================================================

class LookupRequest(BaseModel):
    medicine_name: str = Field(
        min_length=2,
        max_length=160,
    )

    selected_medicine_id: str | None = Field(
        default=None,
        max_length=100,
    )

    confirmed_composition: str | None = Field(
        default=None,
        max_length=500,
    )

    conditions: list[str] = Field(
        default_factory=list,
        max_length=12,
    )

    allergies: list[str] = Field(
        default_factory=list,
        max_length=12,
    )


class ChatRequest(BaseModel):
    question: str = Field(
        min_length=2,
        max_length=700,
    )

    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=60,
    )

    medicine_name: str = Field(
        default="this medicine",
        max_length=160,
    )


# =========================================================
# GENERAL HELPERS
# =========================================================

def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)

    if not item:
        return None

    created, value = item

    if time.time() - created > CACHE_TTL:
        _CACHE.pop(key, None)
        return None

    return value


def _cache_set(key: str, value: Any) -> None:
    if len(_CACHE) > 400:
        oldest = sorted(
            _CACHE,
            key=lambda item: _CACHE[item][0],
        )[:100]

        for old_key in oldest:
            _CACHE.pop(old_key, None)

    _CACHE[key] = (time.time(), value)


def _check_rate_limit(request: Request) -> None:
    forwarded = request.headers.get("x-forwarded-for", "")

    client_ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (
            request.client.host
            if request.client
            else "unknown"
        )
    )

    now = time.time()

    recent = [
        stamp
        for stamp in _RATE_LIMIT.get(client_ip, [])
        if now - stamp < 300
    ]

    if len(recent) >= 15:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many medicine searches from this connection. "
                "Please wait a few minutes before trying again."
            ),
        )

    recent.append(now)
    _RATE_LIMIT[client_ip] = recent

    if len(_RATE_LIMIT) > 1000:
        for key in list(_RATE_LIMIT)[:250]:
            _RATE_LIMIT.pop(key, None)


def _clean(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


def _normalise(text: str) -> str:
    text = str(text or "").lower()

    text = re.sub(
        r"\b(tablets?|tabs?|capsules?|caps?|syrup|"
        r"oral suspension|mg|mcg|g|ml|ip|bp)\b",
        " ",
        text,
    )

    return " ".join(
        re.findall(r"[a-z0-9]+", text)
    )


def _safe_list(value: Any) -> list:
    """Convert a database value into a Python list safely."""

    if isinstance(value, list):
        return value

    if isinstance(value, tuple):
        return list(value)

    if isinstance(value, str):
        return [value] if value.strip() else []

    return []


def _get_candidate_id(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("medicine_id")
        or candidate.get("id")
        or candidate.get("source_record_id")
        or ""
    )


def _get_candidate_composition(
    candidate: dict[str, Any],
) -> str:
    """
    Support both the original Kaggle-style columns and
    the cleaned Neon table column names.
    """

    direct = (
        candidate.get("composition")
        or candidate.get("composition_text")
        or candidate.get("composition_text_original")
    )

    if direct:
        return _clean(direct)

    first = _clean(
        candidate.get("composition_1")
        or candidate.get("short_composition1")
        or ""
    )

    second = _clean(
        candidate.get("composition_2")
        or candidate.get("short_composition2")
        or ""
    )

    return " + ".join(
        value
        for value in (first, second)
        if value
    )


def _normalise_database_match(
    result: Any,
) -> dict[str, Any]:
    """
    Ensure the medicine search result has the same
    response shape expected by the frontend.
    """

    if not isinstance(result, dict):
        return {
            "status": "dataset_unavailable",
            "candidates": [],
        }

    status = str(
        result.get("status", "not_found")
    )

    candidates = result.get("candidates", [])

    if not isinstance(candidates, list):
        candidates = []

    cleaned_candidates = []

    for item in candidates:
        if not isinstance(item, dict):
            continue

        candidate = dict(item)

        candidate_id = _get_candidate_id(candidate)
        composition = _get_candidate_composition(candidate)

        candidate["id"] = candidate_id
        candidate["medicine_id"] = candidate_id
        candidate["composition"] = composition

        candidate["name"] = _clean(
            candidate.get("name")
            or candidate.get("medicine_name")
        )

        candidate["manufacturer"] = _clean(
            candidate.get("manufacturer")
            or candidate.get("manufacturer_name")
        )

        candidate["pack_size"] = _clean(
            candidate.get("pack_size")
            or candidate.get("pack_size_label")
        )

        candidate["type"] = _clean(
            candidate.get("type")
            or candidate.get("dosage_form")
        )

        cleaned_candidates.append(candidate)

    return {
        **result,
        "status": status,
        "candidates": cleaned_candidates,
    }


# =========================================================
# NEON MEDICINE DATABASE
# =========================================================

def _find_dataset_medicine(
    query: str,
) -> dict[str, Any]:
    """
    Search the Neon medicine catalogue.

    Neon is the primary medicine source.
    No local CSV fallback is used here.
    """

    try:
        result = db.search_medicines(query)

        return _normalise_database_match(result)

    except Exception as exc:
        print(f"Neon medicine search failed: {exc}")

        return {
            "status": "dataset_unavailable",
            "candidates": [],
            "error": "Medicine database is temporarily unavailable.",
        }


def _select_candidate(
    dataset_match: dict[str, Any],
    selected_id: str | None,
) -> dict[str, Any] | None:
    """
    Return only the candidate selected by the user.

    If no ID is supplied, do not silently choose one
    when multiple possible matches are returned.
    """

    candidates = dataset_match.get("candidates", [])

    if not candidates:
        return None

    if selected_id:
        selected_id = str(selected_id)

        for candidate in candidates:
            if _get_candidate_id(candidate) == selected_id:
                return candidate

        return None

    if (
        dataset_match.get("status") == "exact_match"
        and len(candidates) == 1
    ):
        return candidates[0]

    return None


# =========================================================
# EXTERNAL SOURCE QUALITY
# =========================================================

def _source_quality(
    url: str,
    title: str,
) -> tuple[str, int]:

    host = url.lower()

    if any(
        item in host
        for item in (
            "cdsco.gov.in",
            "abdm.gov.in",
            "drugregistry.abdm.gov.in",
            "dailymed.nlm.nih.gov",
            "fda.gov",
        )
    ):
        return "Government / regulator source", 5

    if any(
        item in host
        for item in (
            "who.int",
            "nhs.uk",
            "medlineplus.gov",
            "ncbi.nlm.nih.gov",
            "ods.od.nih.gov",
        )
    ):
        return "Recognised health reference", 4

    if any(
        item in host
        for item in (
            "cipla.com",
            "sunpharma.com",
            "drreddys.com",
            "torrentpharma.com",
            "zyduslife.com",
        )
    ):
        return "Manufacturer / pharmaceutical source", 4

    if any(
        item in host
        for item in (
            "1mg.com",
            "netmeds.com",
            "pharmeasy.in",
            "apollo247.com",
        )
    ):
        return "Indian medicine reference", 3

    return "Other web source — verify carefully", 1


# =========================================================
# TAVILY SEARCH
# =========================================================

async def _tavily_search(
    query: str,
    max_results: int = 5,
) -> list[dict[str, Any]]:

    api_key = os.getenv(
        "TAVILY_API_KEY",
        "",
    ).strip()

    if not api_key:
        return []

    cache_key = "tavily:" + query.lower()

    cached = _cache_get(cache_key)

    if cached is not None:
        return cached

    payload = {
        "query": query,
        "search_depth": "basic",
        "topic": "general",
        "max_results": max_results,
        "chunks_per_source": 3,
        "country": "india",
        "include_answer": False,
        "include_raw_content": "markdown",
        "include_published_date": True,
    }

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:

            response = await client.post(
                TAVILY_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
            )

            if response.status_code == 429:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "The free search service is temporarily "
                        "rate-limited. Please try again later."
                    ),
                )

            if response.status_code in (401, 403):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The configured search key was rejected. "
                        "Check TAVILY_API_KEY."
                    ),
                )

            response.raise_for_status()
            data = response.json()

    except HTTPException:
        raise

    except httpx.HTTPError as exc:
        print(f"Tavily request failed: {exc}")
        return []

    results = []

    for item in data.get("results", []):
        url = str(item.get("url", ""))

        title = _clean(
            item.get("title", "")
        )

        snippet = _clean(
            item.get("content", "")
        )

        raw_content = _clean(
            item.get("raw_content", "")
        )

        content = (
            raw_content
            if len(raw_content) > len(snippet)
            else snippet
        )

        quality, rank = _source_quality(
            url,
            title,
        )

        if (
            url.startswith("https://")
            and title
            and content
        ):
            results.append({
                "title": title[:220],
                "url": url,
                "content": content[:6500],
                "source_type": quality,
                "source_rank": rank,
                "score": float(
                    item.get("score", 0) or 0
                ),
                "published_date": (
                    _clean(item.get("published_date"))
                    if item.get("published_date")
                    else None
                ),
            })

    results.sort(
        key=lambda item: (
            item["source_rank"],
            item["score"],
        ),
        reverse=True,
    )

    _cache_set(cache_key, results)

    return results


# =========================================================
# OPENFDA SEARCH
# =========================================================

async def _openfda_search(
    name: str,
) -> list[dict[str, Any]]:

    safe = re.sub(
        r"[^A-Za-z0-9 .+\-]",
        " ",
        name,
    ).strip()

    if not safe:
        return []

    queries = [
        f'openfda.brand_name:"{safe}"',
        f'openfda.generic_name:"{safe}"',
    ]

    output = []

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    ) as client:

        for query in queries:
            try:
                response = await client.get(
                    OPENFDA_URL,
                    params={
                        "search": query,
                        "limit": 2,
                    },
                )

                if response.status_code != 200:
                    continue

                data = response.json()

                for item in data.get("results", []):
                    openfda = item.get("openfda", {})

                    brand = (
                        openfda.get("brand_name") or [""]
                    )[0]

                    generic = (
                        openfda.get("generic_name") or [""]
                    )[0]

                    ingredients = (
                        item.get("active_ingredient") or []
                    )

                    label_sections = []

                    for field in (
                        "indications_and_usage",
                        "purpose",
                        "warnings",
                        "dosage_and_administration",
                        "description",
                    ):
                        values = item.get(field) or []

                        if values:
                            label_sections.append(
                                f"{field.replace('_', ' ').title()}: "
                                f"{_clean(values[0])[:900]}"
                            )

                    content = " | ".join([
                        f"Brand: {brand}",
                        f"Generic: {generic}",
                        "Active ingredient: "
                        + "; ".join(ingredients),
                        *label_sections,
                    ])

                    output.append({
                        "title": (
                            f"openFDA label: {brand or generic}"
                        ),
                        "url": (
                            "https://open.fda.gov/apis/drug/label/"
                        ),
                        "content": content[:2200],
                        "source_type": (
                            "US FDA submitted label "
                            "(not India-specific)"
                        ),
                        "source_rank": 4,
                        "score": 0.5,
                    })

            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
            ):
                continue

    return output


# =========================================================
# DAILYMED SEARCH
# =========================================================

async def _dailymed_search(
    name: str,
) -> list[dict[str, Any]]:

    safe = _clean(name)[:100]

    if not safe:
        return []

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:

            response = await client.get(
                DAILYMED_URL,
                params={
                    "drug_name": safe,
                    "pagesize": 2,
                },
            )

            if response.status_code != 200:
                return []

            data = response.json()
            items = data.get("data", [])[:2]

            async def fetch_label(
                item: dict[str, Any],
            ) -> dict[str, Any] | None:

                setid = str(item.get("setid", ""))

                title = _clean(
                    item.get("title", "")
                )

                if (
                    not title
                    or not re.fullmatch(
                        r"[A-Fa-f0-9-]{20,50}",
                        setid,
                    )
                ):
                    return None

                label_url = (
                    "https://dailymed.nlm.nih.gov/"
                    f"dailymed/drugInfo.cfm?setid={quote(setid)}"
                )

                content_parts = [
                    f"Label title: {title}"
                ]

                xml_url = (
                    "https://dailymed.nlm.nih.gov/"
                    "dailymed/services/v2/spls/"
                    f"{quote(setid)}.xml"
                )

                try:
                    label_response = await client.get(xml_url)

                    if label_response.status_code == 200:
                        root = ET.fromstring(
                            label_response.text
                        )

                        wanted_sections = (
                            "description",
                            "indications",
                            "purpose",
                            "warnings",
                            "contraindications",
                            "precautions",
                            "adverse reactions",
                            "dosage",
                            "active ingredient",
                            "clinical pharmacology",
                        )

                        for section in root.iter():
                            tag = section.tag.rsplit("}", 1)[-1].lower()

                            if tag != "section":
                                continue

                            title_text = ""

                            for child in list(section):
                                child_tag = (
                                    child.tag.rsplit("}", 1)[-1].lower()
                                )

                                if child_tag == "title":
                                    title_text = _clean(
                                        " ".join(child.itertext())
                                    )
                                    break

                            if (
                                title_text
                                and any(
                                    term in title_text.lower()
                                    for term in wanted_sections
                                )
                            ):
                                section_text = _clean(
                                    " ".join(section.itertext())
                                )

                                if len(section_text) > len(title_text) + 10:
                                    content_parts.append(
                                        section_text[:2200]
                                    )

                except (
                    httpx.HTTPError,
                    ET.ParseError,
                    ValueError,
                ):
                    pass

                return {
                    "title": f"DailyMed label: {title[:180]}",
                    "url": label_url,
                    "content": " ".join(content_parts)[:6500],
                    "source_type": (
                        "US NLM/FDA drug label "
                        "(not India-specific)"
                    ),
                    "source_rank": 4,
                    "score": 0.55,
                    "published_date": (
                        _clean(item.get("published_date"))
                        if item.get("published_date")
                        else None
                    ),
                }

            labels = await asyncio.gather(
                *(fetch_label(item) for item in items),
                return_exceptions=True,
            )

        return [
            item
            for item in labels
            if isinstance(item, dict)
        ]

    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        TypeError,
    ):
        return []


# =========================================================
# COMPOSITION EXTRACTION
# =========================================================

def _composition_lines(
    sources: list[dict[str, Any]],
) -> list[dict[str, str]]:

    patterns = (
        r"(?:composition|each\s+(?:tablet|capsule|5\s*ml|"
        r"10\s*ml|sachet)[^:]{0,50}contains?|active\s+"
        r"ingredients?|each\s+dosage\s+unit\s+contains?)"
        r"\s*[:\-]?\s*([^.;]{12,260})",

        r"(?:contains|containing)\s+"
        r"([A-Za-z][A-Za-z0-9 +,()\-/]{8,180}"
        r"(?:\d\s*(?:mg|mcg|g|iu|ml))[^.;]{0,80})",
    )

    found = []
    seen = set()

    for source in sources:
        content = source.get("content", "")

        for pattern in patterns:
            for match in re.finditer(
                pattern,
                content,
                flags=re.I,
            ):
                line = _clean(
                    match.group(1)
                ).strip(" :-,;")

                key = re.sub(
                    r"[^a-z0-9]",
                    "",
                    line.lower(),
                )

                if len(line) >= 10 and key not in seen:
                    seen.add(key)

                    found.append({
                        "text": line[:260],
                        "source_title": source.get("title", "Source"),
                        "source_url": source.get("url", ""),
                        "source_type": source.get("source_type", ""),
                    })

    return found[:8]


def _split_ingredients(
    composition: str,
) -> list[str]:

    chunks = re.split(
        r"\s*(?:\+|;|\band\b|,\s*(?=[A-Z]))\s*",
        composition,
    )

    candidates = []

    for chunk in chunks:
        chunk = _clean(chunk).strip(" .,:;-")

        if not chunk:
            continue

        cleaned = re.sub(
            r"\b\d[\d,]*(?:\.\d+)?\s*"
            r"(?:mg|mcg|μg|g|iu|ml|%|units?)\b",
            "",
            chunk,
            flags=re.I,
        )

        cleaned = re.sub(
            r"\b(ip|bp|usp|eq\.?\s*to|each|contains?|"
            r"per\s+tablet|per\s+capsule)\b",
            "",
            cleaned,
            flags=re.I,
        )

        cleaned = _clean(
            re.sub(
                r"[^A-Za-z0-9()\- ]",
                " ",
                cleaned,
            )
        ).strip(" -")

        if len(cleaned) >= 3 and len(cleaned.split()) <= 7:
            candidates.append(cleaned)

    deduped = []
    seen = set()

    for item in candidates:
        key = item.lower()

        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped[:8]


# =========================================================
# EVIDENCE EXTRACTION
# =========================================================

def _evidence_highlights(
    sources: list[dict[str, Any]],
) -> dict[str, Any]:

    buckets = {
        "what_it_does": (
            "used to",
            "use in",
            "indicated for",
            "treat",
            "relief",
            "purpose",
            "helps",
            "prevents",
        ),
        "how_it_works": (
            "works by",
            "mechanism",
            "inhibit",
            "inhibits",
            "block",
            "blocks",
            "reduces",
            "acts on",
            "action",
        ),
        "precautions": (
            "warning",
            "avoid",
            "caution",
            "side effect",
            "adverse",
            "contraindication",
            "allergy",
            "pregnancy",
            "do not use",
        ),
    }

    output = {}
    all_sentences = []

    for source in sources:
        text = source.get("content", "")

        sentences = [
            sentence.strip()
            for sentence in re.split(
                r"(?<=[.!?])\s+|\s+\|\s+",
                text,
            )
            if len(sentence.strip()) > 24
        ]

        for sentence in sentences:
            all_sentences.append(
                (sentence[:650], source)
            )

    for key, terms in buckets.items():
        selected = []
        seen = set()

        for sentence, source in all_sentences:
            lowered = sentence.lower()

            if any(term in lowered for term in terms):
                normalized = re.sub(
                    r"[^a-z0-9]",
                    "",
                    lowered,
                )

                if normalized not in seen:
                    seen.add(normalized)

                    selected.append({
                        "text": sentence,
                        "source_title": source.get("title", "Source"),
                        "source_url": source.get("url", ""),
                    })

            if len(selected) == 2:
                break

        output[key] = selected

    strong_domains = {
        re.sub(
            r"^https?://",
            "",
            source.get("url", ""),
        ).split("/")[0]
        for source in sources
        if source.get("source_rank", 0) >= 4
    }

    if len(strong_domains) >= 2:
        confidence = "corroborated across recognised sources"
    elif strong_domains:
        confidence = "one recognised source found"
    elif sources:
        confidence = "limited web references; verify independently"
    else:
        confidence = "no supporting source found"

    output["evidence_level"] = confidence

    return output


# =========================================================
# PRODUCT IDENTITY
# =========================================================

def _rank_identity(
    name: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:

    wanted_tokens = [
        token
        for token in _normalise(name).split()
        if not token.isdigit()
    ]

    matches = []

    for source in sources:
        haystack_tokens = set(
            _normalise(
                source.get("title", "")
                + " "
                + source.get("content", "")
            ).split()
        )

        if (
            wanted_tokens
            and all(
                token in haystack_tokens
                for token in wanted_tokens
            )
        ):
            matches.append(source)

    strong = [
        source
        for source in matches
        if source.get("source_rank", 0) >= 4
    ]

    if strong:
        return {
            "level": "moderate",
            "label": "Possible match — verify pack details",
            "matched_sources": len(strong),
            "note": (
                "A recognised reference matched the entered name, "
                "but this does not confirm the Indian marketed pack "
                "or its current composition."
            ),
        }

    if len(matches) >= 2:
        return {
            "level": "limited",
            "label": "Candidate match — confirmation needed",
            "matched_sources": len(matches),
            "note": (
                "More than one source mentions the name, "
                "but product identity is not confirmed."
            ),
        }

    return {
        "level": "unverified",
        "label": "Not verified",
        "matched_sources": len(matches),
        "note": (
            "The retrieved evidence is not enough to identify "
            "this exact product. Check the brand, strength, "
            "manufacturer and dosage form on the pack."
        ),
    }


# =========================================================
# NEON INGREDIENT RESEARCH
# =========================================================

async def _research_ingredient_with_database(
    ingredient: str,
) -> dict[str, Any]:
    """
    Look up an ingredient in Neon.

    1. Find its ingredient ID.
    2. Retrieve stored ingredient knowledge.
    3. Retrieve stored source references.
    4. If no useful stored knowledge exists, use Tavily.
    """

    ingredient_id = None
    knowledge = None
    references = []

    try:
        ingredient_id = await asyncio.to_thread(
            db.find_ingredient_id,
            ingredient,
        )

        if ingredient_id:
            knowledge = await asyncio.to_thread(
                db.get_ingredient_knowledge,
                ingredient_id,
            )

            references = await asyncio.to_thread(
                db.get_ingredient_sources,
                ingredient_id,
            )

    except Exception as exc:
        print(
            f"Neon ingredient lookup failed for "
            f"{ingredient}: {exc}"
        )

    if not isinstance(knowledge, dict):
        knowledge = {}

    if not isinstance(references, list):
        references = []

    clinical_fields = (
        "clinical_uses",
        "mechanism_of_action",
        "precautions",
        "side_effects",
    )

    content_parts = []

    for field in clinical_fields:
        value = knowledge.get(field)

        if value and str(value).strip():
            content_parts.append(
                f"{field.replace('_', ' ').title()}: "
                f"{str(value).strip()}"
            )

    stored_sources = []

    if content_parts:
        stored_content = " ".join(content_parts)

        for reference in references:
            if not isinstance(reference, dict):
                continue

            url = str(
                reference.get("source_url") or ""
            )

            if not url.startswith("https://"):
                continue

            stored_sources.append({
                "title": str(
                    reference.get("source_name")
                    or "Ingredient reference"
                ),
                "url": url,
                "content": stored_content,
                "source_type": str(
                    reference.get("source_type")
                    or "Stored ingredient reference"
                ),
                "source_rank": 4,
                "score": 1.0,
                "jurisdiction": reference.get("jurisdiction"),
            })

        # Knowledge without a usable URL can still be returned
        # as stored information, but it is not presented as a
        # source-linked clinical reference.
        if not stored_sources:
            stored_sources.append({
                "title": f"Stored knowledge: {ingredient}",
                "url": "",
                "content": stored_content,
                "source_type": "Stored ingredient knowledge",
                "source_rank": 2,
                "score": 1.0,
            })

        return {
            "ingredient_id": (
                str(ingredient_id)
                if ingredient_id is not None
                else None
            ),
            "knowledge": knowledge,
            "sources": stored_sources,
            "status": "database_knowledge_found",
        }

    # Neon has no useful clinical text for this ingredient.
    # Use the existing live search fallback.
    live_sources = await _search_ingredient(ingredient)

    return {
        "ingredient_id": (
            str(ingredient_id)
            if ingredient_id is not None
            else None
        ),
        "knowledge": knowledge or None,
        "sources": live_sources,
        "status": (
            "live_evidence_found"
            if live_sources
            else "no_evidence_found"
        ),
    }


async def _search_ingredient(
    ingredient: str,
) -> list[dict[str, Any]]:

    query = (
        f'"{ingredient}" active ingredient '
        "mechanism of action uses precautions "
        "official health reference medicine "
        "-ayurveda -homeopathy"
    )

    try:
        return await _tavily_search(
            query,
            max_results=4,
        )

    except HTTPException:
        raise

    except Exception as exc:
        print(f"Ingredient web search failed: {exc}")
        return []


# =========================================================
# BASIC PRECAUTION SCREEN
# =========================================================

def _basic_health_check(
    conditions: list[str],
    allergies: list[str],
    ingredients: list[str],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:

    if not conditions and not allergies:
        return {
            "status": "not_run",
            "message": (
                "Add a condition or known allergy if you want "
                "the basic precaution screen. This is not a "
                "full interaction check."
            ),
        }

    text_parts = []

    for item in evidence:
        for source in item.get("sources", []):
            text_parts.append(
                source.get("content", "")
            )

    text = " ".join(text_parts).lower()

    matched = []

    for term in [*conditions, *allergies]:
        term_clean = _clean(term)

        if term_clean and term_clean.lower() in text:
            matched.append(term_clean)

    if matched:
        return {
            "status": "review",
            "message": (
                "The retrieved ingredient references mention "
                "one or more terms you entered. Review the cited "
                "source with a pharmacist or doctor. This is not "
                "a validated interaction assessment."
            ),
            "matched_terms": matched,
        }

    return {
        "status": "no_match_in_retrieved_text",
        "message": (
            "No direct text match was found in the retrieved "
            "excerpts. This does not mean the medicine is safe "
            "or that no interaction exists."
        ),
    }


# =========================================================
# HEALTH ENDPOINT
# =========================================================

@app.get("/api/health", include_in_schema=False)
@app.get("/health")
async def health() -> dict[str, Any]:

    database_status = "disconnected"
    database_counts = {}
    database_error = None

    try:
        database_counts = await asyncio.to_thread(
            db.check_database
        )

        database_status = "connected"

    except Exception as exc:
        database_error = str(exc)
        print(f"Database health check failed: {exc}")

    return {
        "ok": True,
        "service": "Medicine Buddy API",
        "version": APP_VERSION,
        "tavily_configured": bool(
            os.getenv("TAVILY_API_KEY", "").strip()
        ),
        "database_status": database_status,
        "database_counts": database_counts,
        "database_error": database_error,
    }


# =========================================================
# MEDICINE LOOKUP ENDPOINT
# =========================================================

@app.post("/api/lookup", include_in_schema=False)
@app.post("/lookup")
async def lookup(
    payload: LookupRequest,
    http_request: Request,
) -> dict[str, Any]:

    name = _clean(payload.medicine_name)

    if len(name) < 2:
        raise HTTPException(
            status_code=422,
            detail="Enter at least two characters of the medicine name.",
        )

    _check_rate_limit(http_request)

    selected_id = (
        str(payload.selected_medicine_id)
        if payload.selected_medicine_id
        else None
    )

    confirmed = _clean(
        payload.confirmed_composition or ""
    )

    # Cache is specific to the medicine, selected candidate,
    # and confirmed composition.
    cache_key = (
        "lookup:"
        + _normalise(name)
        + ":"
        + (selected_id or "")
        + ":"
        + confirmed.lower()
    )

    cached = _cache_get(cache_key)

    if cached is not None:
        return {
            **cached,
            "health_check": _basic_health_check(
                payload.conditions,
                payload.allergies,
                [
                    item.get("name", "")
                    for item in cached.get("ingredients", [])
                ],
                cached.get("ingredients", []),
            ),
            "cached": True,
        }

    errors = []
    trace = [
        "Input received",
        "Searching the Neon Indian medicine catalogue",
    ]

    # -----------------------------------------------------
    # 1. SEARCH NEON MEDICINE CATALOGUE
    # -----------------------------------------------------

    dataset_match = await asyncio.to_thread(
        _find_dataset_medicine,
        name,
    )

    candidates = dataset_match.get("candidates", [])

    trace.append(
        "Medicine catalogue search completed"
    )

    # -----------------------------------------------------
    # 2. SELECT THE MEDICINE CANDIDATE
    # -----------------------------------------------------

    selected_candidate = _select_candidate(
        dataset_match,
        selected_id,
    )

    composition_candidates = []

    for candidate in candidates:
        composition = _get_candidate_composition(candidate)

        if composition:
            composition_candidates.append({
                "medicine_id": _get_candidate_id(candidate),
                "medicine_name": candidate.get("name", ""),
                "text": composition,
                "source_title": "Neon Indian medicine catalogue",
                "source_url": "",
                "source_type": "Dataset listing — verify pack",
            })

    # If the user supplied an ID that does not belong to the
    # returned candidates, do not silently use another product.
    if selected_id and not selected_candidate:
        identity = {
            "level": "unverified",
            "label": "Selected medicine could not be verified",
            "matched_sources": 0,
            "note": (
                "The selected medicine ID was not found among "
                "the current search results. Search again and "
                "select one of the displayed candidates."
            ),
        }

    elif selected_candidate:
        identity = {
            "level": (
                "exact"
                if dataset_match.get("status") == "exact_match"
                else "candidate"
            ),
            "label": (
                "Selected medicine from Indian catalogue"
            ),
            "matched_sources": 1,
            "note": (
                "This medicine was selected from the Neon "
                "Indian medicine catalogue. Check the brand, "
                "strength, manufacturer and pack against the "
                "physical medicine label."
            ),
        }

    elif dataset_match.get("status") in (
        "exact_match",
        "possible_match",
    ):
        identity = {
            "level": dataset_match.get("status"),
            "label": (
                "Medicine candidates found — select the correct one"
            ),
            "matched_sources": len(candidates),
            "note": (
                "Several catalogue records may have similar names. "
                "Select the correct manufacturer and pack before "
                "confirming the composition."
            ),
        }

    else:
        identity = None

    sources = []
    product_highlights = _evidence_highlights([])
    combination_sources = []
    ingredient_results = []
    notices = []

    # -----------------------------------------------------
    # 3. EXTERNAL PRODUCT SEARCH IF NEON HAS NO MATCH
    # -----------------------------------------------------

    if dataset_match.get("status") in (
        "not_found",
        "dataset_unavailable",
    ):
        trace.append(
            "No usable Neon catalogue match; checking supplementary references"
        )

        tavily_task = _tavily_search(
            (
                f'"{name}" composition ingredients strength '
                "manufacturer India medicine supplement "
                "-ayurveda -homeopathy"
            ),
            6,
        )

        fda_task = _openfda_search(name)
        daily_task = _dailymed_search(name)

        tavily_results, fda_results, daily_results = (
            await asyncio.gather(
                tavily_task,
                fda_task,
                daily_task,
                return_exceptions=True,
            )
        )

        for label, result in (
            ("Tavily", tavily_results),
            ("openFDA", fda_results),
            ("DailyMed", daily_results),
        ):
            if isinstance(result, Exception):
                errors.append(
                    f"{label} lookup unavailable"
                )
            elif result:
                sources.extend(result)

        # Remove duplicate URLs.
        deduplicated = {}

        for source in sources:
            deduplicated.setdefault(
                source.get("url", ""),
                source,
            )

        sources = sorted(
            deduplicated.values(),
            key=lambda item: (
                item.get("source_rank", 0),
                item.get("score", 0),
            ),
            reverse=True,
        )[:12]

        identity = _rank_identity(
            name,
            sources,
        )

        composition_candidates = _composition_lines(
            sources,
        )

        product_highlights = _evidence_highlights(
            sources,
        )

    else:
        trace.append(
            "Neon catalogue candidates retrieved; "
            "external product-name search skipped"
        )

    if identity is None:
        identity = {
            "level": "unverified",
            "label": "Not verified",
            "matched_sources": 0,
            "note": (
                "No reliable product identity could be established. "
                "Check the medicine packaging before proceeding."
            ),
        }

    # -----------------------------------------------------
    # 4. COMPOSITION CONFIRMATION
    # -----------------------------------------------------

    if confirmed:
        ingredients = _split_ingredients(confirmed)

        composition_status = "user_confirmed"

        trace.append(
            "User-provided composition accepted for ingredient research"
        )

    else:
        ingredients = []

        composition_status = "not_confirmed"

        trace.append(
            "Composition confirmation required before ingredient research"
        )

    # If a medicine candidate was selected, include its
    # composition in the response for the frontend to display.
    # It is not silently treated as user-confirmed.
    if selected_candidate:
        selected_composition = _get_candidate_composition(
            selected_candidate
        )

        if selected_composition:
            trace.append(
                "Composition retrieved for the selected catalogue medicine"
            )

    # -----------------------------------------------------
    # 5. INGREDIENT RESEARCH
    # -----------------------------------------------------

    if ingredients:
        trace.append(
            "Checking each confirmed ingredient in Neon first"
        )

        ingredient_tasks = [
            _research_ingredient_with_database(ingredient)
            for ingredient in ingredients
        ]

        results = await asyncio.gather(
            *ingredient_tasks,
            return_exceptions=True,
        )

        for ingredient, result in zip(ingredients, results):
            if isinstance(result, Exception):
                print(
                    f"Ingredient research failed for "
                    f"{ingredient}: {result}"
                )

                ingredient_results.append({
                    "name": ingredient,
                    "ingredient_id": None,
                    "knowledge": None,
                    "sources": [],
                    "highlights": _evidence_highlights([]),
                    "status": "research_unavailable",
                })

                continue

            ingredient_sources = result.get(
                "sources",
                [],
            )

            ingredient_results.append({
                "name": ingredient,
                "ingredient_id": result.get("ingredient_id"),
                "knowledge": result.get("knowledge"),
                "sources": ingredient_sources,
                "highlights": _evidence_highlights(
                    ingredient_sources
                ),
                "status": result.get(
                    "status",
                    "no_evidence_found",
                ),
            })

        trace.append(
            "Ingredient information retrieved and organised separately"
        )

        # -------------------------------------------------
        # 6. COMBINATION RESEARCH
        # -------------------------------------------------

        if len(ingredients) > 1:
            combo_query = (
                " ".join(ingredients)
                + " fixed dose combination rationale "
                "why combined official medicine label"
            )

            try:
                combination_sources = await _tavily_search(
                    combo_query,
                    max_results=4,
                )

            except Exception as exc:
                print(
                    f"Combination research failed: {exc}"
                )
                combination_sources = []

            trace.append(
                "Checked for evidence explaining the combination"
            )

    # -----------------------------------------------------
    # 7. SAFETY NOTICES
    # -----------------------------------------------------

    disclaimer = (
        "Medicine Buddy provides educational information, "
        "not diagnosis, prescribing or proof that a medicine "
        "is safe for you."
    )

    notices.append(disclaimer)

    if not os.getenv("TAVILY_API_KEY", "").strip():
        notices.append(
            "Tavily is not configured. Live web research is "
            "unavailable when stored ingredient knowledge is missing."
        )

    if errors:
        notices.append(
            "Some external sources could not be reached: "
            + ", ".join(errors)
            + "."
        )

    if (
        not sources
        and not composition_candidates
        and not selected_candidate
    ):
        notices.append(
            "No usable medicine references were retrieved. "
            "Do not infer composition from the medicine name."
        )

    if identity["level"] == "unverified":
        notices.append(
            "Product identity is unverified. Do not use this "
            "result to make a medication decision."
        )

    if not confirmed:
        notices.append(
            "Ingredient research starts only after the composition "
            "is confirmed. A catalogue match alone is not confirmation."
        )

    # -----------------------------------------------------
    # 8. FINAL RESPONSE
    # -----------------------------------------------------

    response = {
        "medicine_name": name,

        "identity": identity,

        "dataset_match": dataset_match,

        "composition_status": composition_status,

        "composition_candidates": composition_candidates,

        "product_highlights": product_highlights,

        "confirmed_composition": confirmed or None,

        "ingredients": ingredient_results,

        "combination_sources": combination_sources,

        "sources": sources,

        "trace": trace,

        "notices": notices,

        "health_check": _basic_health_check(
            payload.conditions,
            payload.allergies,
            ingredients,
            ingredient_results,
        ),

        "cached": False,
    }

    # Do not cache user conditions or allergies.
    cacheable = {
        key: value
        for key, value in response.items()
        if key != "health_check"
    }

    _cache_set(
        cache_key,
        cacheable,
    )

    return response


# =========================================================
# GROQ + GEMINI LLM
# =========================================================

async def _call_groq(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("Groq API key not configured")

    model = os.getenv(
        "GROQ_MODEL",
        "openai/gpt-oss-20b"
    ).strip()

    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_completion_tokens": 900,
            },
        )

        response.raise_for_status()
        data = response.json()

        answer = data["choices"][0]["message"]["content"]

        if not answer:
            raise RuntimeError("Groq returned an empty response")

        return answer.strip()


async def _call_gemini(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("Gemini API key not configured")

    model = os.getenv(
        "GEMINI_MODEL",
        "gemini-2.5-flash"
    ).strip()

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )

    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.post(
            url,
            params={"key": api_key},
            json={
                "systemInstruction": {
                    "parts": [{"text": system_prompt}]
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": user_prompt}],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 900,
                },
            },
        )

        response.raise_for_status()
        data = response.json()

        answer = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )

        if not answer:
            raise RuntimeError("Gemini returned an empty response")

        return answer.strip()


def _prepare_chat_evidence(
    evidence: list[dict[str, Any]]
) -> tuple[str, list[dict[str, str]]]:

    context_parts = []
    citations = []
    seen = set()

    for item in evidence:
        if not isinstance(item, dict):
            continue

        title = _clean(
            item.get("title")
            or item.get("source_title")
            or "Retrieved medicine information"
        )

        url = str(
            item.get("url")
            or item.get("source_url")
            or ""
        )

        content = _clean(
            item.get("content")
            or item.get("excerpt")
            or item.get("text")
            or ""
        )

        # Also support structured Neon ingredient knowledge.
        knowledge = item.get("knowledge")

        if isinstance(knowledge, dict):
            fields = (
                "clinical_uses",
                "mechanism_of_action",
                "precautions",
                "side_effects",
                "contraindications",
                "drug_interactions_text",
            )

            knowledge_text = []

            for field in fields:
                value = knowledge.get(field)

                if value:
                    knowledge_text.append(
                        f"{field.replace('_', ' ').title()}: "
                        f"{_clean(value)}"
                    )

            if knowledge_text:
                content = (
                    content + " " + " ".join(knowledge_text)
                ).strip()

        if not content:
            continue

        key = (title.lower(), content[:200].lower())

        if key in seen:
            continue

        seen.add(key)

        context_parts.append(
            f"SOURCE: {title}\n"
            f"REFERENCE: {url or 'Stored Neon database knowledge'}\n"
            f"INFORMATION: {content[:2500]}"
        )

        if url.startswith("https://"):
            citations.append({
                "title": title,
                "url": url,
            })

    return (
        "\n\n".join(context_parts)[:14000],
        citations[:12],
    )


async def _generate_medicine_answer(
    question: str,
    medicine_name: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:

    context, citations = _prepare_chat_evidence(evidence)

    if not context:
        return {
            "answer": (
                "I couldn't find sufficient ingredient or medicine "
                "information in the current database evidence. "
                "I don't want to guess. Please consult a pharmacist "
                "or doctor for medicine-specific advice."
            ),
            "sources": [],
            "grounded": False,
            "provider": "database_fallback",
        }

    system_prompt = """
You are Medicine Buddy, an educational medicine-information assistant.

STRICT RULES:
1. Answer using ONLY the supplied medicine evidence.
2. Never invent medical facts, ingredient properties, dosages,
   interactions, contraindications or clinical uses.
3. If information is missing, explicitly say:
   "This information is not available in the retrieved sources."
4. Explain medical terminology in simple language.
5. Distinguish documented label information from general explanations.
6. Do not diagnose, prescribe, recommend a dosage, or tell users
   to start, stop or change medication.
7. Do not claim a medicine is safe just because no warning was found.
8. If the evidence comes from US DailyMed or FDA labels, identify
   it as US-label information, not Indian prescribing guidance.
9. For personal medical decisions, recommend consulting a doctor
   or pharmacist.

Use short paragraphs and clear bullet points where helpful.
"""

    user_prompt = f"""
Medicine: {medicine_name}

USER QUESTION:
{question}

RETRIEVED DATABASE AND SOURCE EVIDENCE:
{context}

Explain the answer clearly using the available evidence.
Mention when the evidence does not answer the question.
"""

    # Primary provider: Groq
    try:
        answer = await _call_groq(
            system_prompt,
            user_prompt,
        )

        return {
            "answer": answer,
            "sources": citations,
            "grounded": True,
            "provider": "groq",
        }

    except Exception as exc:
        print(f"Groq unavailable: {type(exc).__name__}: {exc}")

    # Secondary provider: Gemini
    try:
        answer = await _call_gemini(
            system_prompt,
            user_prompt,
        )

        return {
            "answer": answer,
            "sources": citations,
            "grounded": True,
            "provider": "gemini",
        }

    except Exception as exc:
        print(f"Gemini unavailable: {type(exc).__name__}: {exc}")

    # Last fallback: show retrieved evidence without generating claims.
    fallback = "\n\n".join(
        part
        for part in context_parts_for_fallback(context)
    )

    return {
        "answer": (
            "The AI services are temporarily unavailable. "
            "Here is the information retrieved from your database "
            "and its references:\n\n" + fallback[:5000]
        ),
        "sources": citations,
        "grounded": True,
        "provider": "database_fallback",
    }


def context_parts_for_fallback(context: str) -> list[str]:
    return [
        part.strip()
        for part in context.split("\n\n")
        if part.strip()
    ]


# =========================================================
# CHAT ENDPOINT
# =========================================================


@app.post("/api/chat", include_in_schema=False)
@app.post("/chat")
async def chat(
    request: ChatRequest,
) -> dict[str, Any]:

    result = await _generate_medicine_answer(
        question=request.question,
        medicine_name=request.medicine_name,
        evidence=request.evidence,
    )

    return result

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "build"
app.frontend("/", directory=FRONTEND_DIR)