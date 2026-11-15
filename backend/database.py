
import os
import re
import ssl
from contextlib import closing
from difflib import SequenceMatcher
from urllib.parse import urlparse, unquote

import pg8000.dbapi
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


# --------------------------------------------------
# 1. DATABASE CONNECTION
# --------------------------------------------------

def get_connection():
    """Connect to Neon using the pure-Python pg8000 driver."""

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured in .env")

    parsed = urlparse(DATABASE_URL)

    if not parsed.hostname or not parsed.username:
        raise RuntimeError("DATABASE_URL is invalid.")

    return pg8000.dbapi.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=unquote(parsed.path.lstrip("/")),
        user=unquote(parsed.username),
        password=unquote(parsed.password or ""),
        ssl_context=ssl.create_default_context(),
        timeout=15,
    )


# --------------------------------------------------
# 2. COMMON QUERY FUNCTION
# --------------------------------------------------

def _query(sql, params=None, fetch="all"):
    """Execute SQL and return results as dictionaries."""

    with closing(get_connection()) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute(sql, params or ())

            if cur.description is None:
                return None

            columns = [column[0] for column in cur.description]

            if fetch == "one":
                row = cur.fetchone()
                return dict(zip(columns, row)) if row else None

            return [
                dict(zip(columns, row))
                for row in cur.fetchall()
            ]


# --------------------------------------------------
# 3. TEXT NORMALISATION
# --------------------------------------------------

def normalise(value):
    """Make medicine and ingredient names easier to compare."""

    value = str(value or "").lower()
    value = re.sub(r"[^a-z0-9 ]", " ", value)

    return " ".join(value.split())


# --------------------------------------------------
# 4. DATABASE STATUS
# --------------------------------------------------

def check_database():
    """Return the number of records in important tables."""

    counts = {}

    tables = (
        "medicines",
        "ingredients",
        "medicine_ingredients",
        "ingredient_knowledge",
        "source_references",
    )

    for table in tables:
        row = _query(
            f'SELECT COUNT(*) AS total FROM "{table}"',
            fetch="one",
        )

        counts[table] = row["total"]

    return counts


# --------------------------------------------------
# 5. MEDICINE SEARCH
# --------------------------------------------------

def search_medicines(query, limit=10):
    """
    Search the medicine catalogue.

    Exact matches receive the highest priority.
    Partial and similar names are returned as candidates.
    """

    query_key = normalise(query)

    if len(query_key) < 2:
        return {
            "status": "not_found",
            "candidates": [],
        }

    limit = max(1, min(int(limit), 50))

    words = [
        word for word in query_key.split()
        if len(word) >= 2
    ]

    conditions = [
        "medicine_name_normalized = %s",
        "medicine_name_normalized ILIKE %s",
        "medicine_name ILIKE %s",
    ]

    params = [
        query_key,
        f"%{query_key}%",
        f"%{query}%",
    ]

    for word in words:
        conditions.append(
            "medicine_name_normalized ILIKE %s"
        )
        params.append(f"%{word}%")

    sql = f"""
        SELECT
            medicine_id,
            source_record_id,
            medicine_name,
            manufacturer,
            dosage_form,
            pack_size,
            composition_1,
            composition_2,
            price_inr_dataset,
            discontinued_flag_dataset,
            medicine_name_normalized
        FROM medicines
        WHERE {" OR ".join(conditions)}
        ORDER BY
            CASE
                WHEN medicine_name_normalized = %s THEN 0
                WHEN medicine_name_normalized ILIKE %s THEN 1
                ELSE 2
            END,
            medicine_name
        LIMIT 500
    """

    params.extend([
        query_key,
        f"{query_key}%",
    ])

    rows = _query(sql, params)

    scored = []

    query_tokens = set(query_key.split())

    for row in rows:
        name = row.get("medicine_name") or ""

        name_key = normalise(
            row.get("medicine_name_normalized") or name
        )

        if name_key == query_key:
            score = 100
        else:
            name_tokens = set(name_key.split())

            similarity = SequenceMatcher(
                None,
                query_key,
                name_key,
            ).ratio()

            overlap = (
                len(query_tokens & name_tokens)
                / max(len(query_tokens), len(name_tokens), 1)
            )

            score = round(
                (similarity * 0.7 + overlap * 0.3) * 100
            )

        if score >= 45:
            composition = " + ".join(
                str(row.get(column) or "").strip()
                for column in ("composition_1", "composition_2")
                if str(row.get(column) or "").strip()
            )

            scored.append({
                "id": str(row.get("medicine_id")),
                "medicine_id": str(row.get("medicine_id")),
                "source_record_id": row.get("source_record_id"),
                "name": name,
                "manufacturer": row.get("manufacturer") or "",
                "composition": composition,
                "pack_size": row.get("pack_size") or "",
                "type": row.get("dosage_form") or "",
                "price": row.get("price_inr_dataset"),
                "is_discontinued": bool(
                    row.get("discontinued_flag_dataset")
                ),
                "match_score": score,
                "source": "Neon Indian medicine catalogue",
            })

    scored.sort(
        key=lambda item: item["match_score"],
        reverse=True,
    )

    candidates = scored[:limit]

    if not candidates:
        status = "not_found"
    elif candidates[0]["match_score"] == 100:
        status = "exact_match"
    else:
        status = "possible_match"

    return {
        "status": status,
        "candidates": candidates,
    }


# --------------------------------------------------
# 6. MEDICINE DETAILS
# --------------------------------------------------

def get_medicine_by_id(medicine_id):
    """Retrieve one medicine using its catalogue ID."""

    return _query(
        """
        SELECT *
        FROM medicines
        WHERE medicine_id = %s
        LIMIT 1
        """,
        (medicine_id,),
        fetch="one",
    )


# --------------------------------------------------
# 7. MEDICINE INGREDIENTS
# --------------------------------------------------

def get_medicine_ingredients(medicine_id):
    """Retrieve the ingredients linked to a medicine."""

    return _query(
        """
        SELECT
            ingredient_name_as_listed,
            strength_as_listed,
            composition_text_original,
            ingredient_normalized,
            ingredient_id
        FROM medicine_ingredients
        WHERE medicine_id = %s
        ORDER BY ingredient_name_as_listed
        """,
        (medicine_id,),
    )


# --------------------------------------------------
# 8. INGREDIENT KNOWLEDGE
# --------------------------------------------------

def get_ingredient_knowledge(ingredient_id):
    """Retrieve stored knowledge for an ingredient."""

    if not ingredient_id:
        return None

    return _query(
        """
        SELECT *
        FROM ingredient_knowledge
        WHERE ingredient_id = %s
        LIMIT 1
        """,
        (ingredient_id,),
        fetch="one",
    )


def get_ingredient_knowledge_by_name(ingredient_name):
    """
    Find knowledge through the normalised ingredient master.

    This helps when ingredient names are listed differently
    in the medicine catalogue and knowledge table.
    """

    key = normalise(ingredient_name)

    if not key:
        return None

    return _query(
        """
        SELECT ik.*
        FROM ingredient_knowledge ik
        INNER JOIN ingredients i
            ON i.ingredient_id = ik.ingredient_id
        WHERE i.ingredient_normalized = %s
        LIMIT 1
        """,
        (key,),
        fetch="one",
    )


def get_ingredient_id_by_name(ingredient_name):
    """Find an ingredient ID using its normalised name."""

    key = normalise(ingredient_name)

    if not key:
        return None

    row = _query(
        """
        SELECT ingredient_id
        FROM ingredients
        WHERE ingredient_normalized = %s
        LIMIT 1
        """,
        (key,),
        fetch="one",
    )

    return row["ingredient_id"] if row else None


# Keep the old function name for compatibility.

def find_ingredient_id(ingredient_name):
    return get_ingredient_id_by_name(ingredient_name)


# --------------------------------------------------
# 9. INGREDIENT SOURCES
# --------------------------------------------------

def get_ingredient_sources(ingredient_id):
    """Retrieve source references linked to an ingredient."""

    if not ingredient_id:
        return []

    return _query(
        """
        SELECT
            source_name,
            source_type,
            jurisdiction,
            source_url,
            retrieval_status
        FROM source_references
        WHERE ingredient_id = %s
        ORDER BY source_name
        """,
        (ingredient_id,),
    )


# --------------------------------------------------
# 10. COMPLETE MEDICINE EVIDENCE
# --------------------------------------------------

def get_medicine_evidence(medicine_id):
    """
    Collect medicine details, ingredient knowledge,
    and available source references.

    Missing knowledge is returned as missing.
    It is never invented or treated as proof of safety.
    """

    medicine = get_medicine_by_id(medicine_id)

    if not medicine:
        return {
            "status": "not_found",
            "medicine": None,
            "ingredients": [],
        }

    ingredient_rows = get_medicine_ingredients(medicine_id)

    ingredients = []

    for item in ingredient_rows:
        ingredient_id = item.get("ingredient_id")

        knowledge = get_ingredient_knowledge(ingredient_id)

        # Try the normalised name if the direct ID lookup fails.
        if not knowledge:
            knowledge = get_ingredient_knowledge_by_name(
                item.get("ingredient_name_as_listed")
            )

        sources = []

        if knowledge:
            knowledge_id = knowledge.get("ingredient_id")

            if knowledge_id:
                sources = get_ingredient_sources(knowledge_id)

        ingredients.append({
            "name": item.get("ingredient_name_as_listed"),
            "strength": item.get("strength_as_listed"),
            "original_composition": item.get(
                "composition_text_original"
            ),
            "ingredient_id": ingredient_id,
            "knowledge": knowledge,
            "sources": sources,
            "knowledge_available": knowledge is not None,
        })

    return {
        "status": "success",
        "medicine": medicine,
        "ingredients": ingredients,
        "ingredient_count": len(ingredients),
        "knowledge_count": sum(
            1 for item in ingredients
            if item["knowledge_available"]
        ),
    }
