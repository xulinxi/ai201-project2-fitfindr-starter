"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import json
import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

# Path to the mock trend feed (stretch: trend awareness).
_TRENDS_PATH = os.path.join(os.path.dirname(__file__), "data", "trends.json")

load_dotenv()

# Model used by the LLM-backed tools (suggest_outfit, create_fit_card).
_MODEL = "llama-3.3-70b-versatile"


# ── search helpers ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercase a string and split it into alphanumeric word tokens."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _score_listing(item: dict, query_terms: list[str]) -> int:
    """
    Score how well a listing matches the query terms by weighted keyword overlap.

    Title and style_tags are the strongest signals, so they're weighted higher
    than the free-text description. Each query term contributes at most once per
    field so a single repeated word can't dominate the ranking.
    """
    if not query_terms:
        return 0

    title_tokens = set(_tokenize(item.get("title", "")))
    desc_tokens = set(_tokenize(item.get("description", "")))
    tag_tokens = set()
    for tag in item.get("style_tags", []):
        tag_tokens.update(_tokenize(tag))
    category_tokens = set(_tokenize(item.get("category", "")))

    score = 0
    for term in query_terms:
        if term in title_tokens:
            score += 3
        if term in tag_tokens:
            score += 3
        if term in category_tokens:
            score += 2
        if term in desc_tokens:
            score += 1
    return score


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()

    # Tokenize the query once: lowercase words, drop trivial noise tokens.
    _STOP = {"a", "an", "the", "for", "with", "and", "in", "of", "to", "under"}
    query_terms = [
        t for t in _tokenize(description) if t and t not in _STOP
    ]

    size_filter = size.strip().lower() if size else None

    scored: list[tuple[int, dict]] = []
    for item in listings:
        # 1. Hard filter: price ceiling.
        if max_price is not None and item["price"] > max_price:
            continue

        # 2. Hard filter: size (case-insensitive substring, both directions so
        #    "M" matches "S/M" and "M (oversized)").
        if size_filter:
            item_size = item.get("size", "").lower()
            if size_filter not in item_size and item_size not in size_filter:
                continue

        # 3. Score by keyword overlap with title / description / tags / category.
        score = _score_listing(item, query_terms)
        if score > 0:
            scored.append((score, item))

    # 4. Sort by score descending; ties keep dataset order (stable sort).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(
    new_item: dict, wardrobe: dict, trends: list[str] | None = None
) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.
        trends:   Optional list of trending style tags (stretch: trend
                  awareness). When provided, the suggestion leans into any trend
                  that fits the item and names it so the influence is visible.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_desc = (
        f"{new_item.get('title', 'this item')} "
        f"(category: {new_item.get('category', 'unknown')}, "
        f"colors: {', '.join(new_item.get('colors', [])) or 'n/a'}, "
        f"style: {', '.join(new_item.get('style_tags', [])) or 'n/a'})"
    )

    # Trend awareness (stretch): if trends were passed, instruct the model to
    # work in a currently-popular tag and call it out by name.
    trend_line = ""
    if trends:
        trend_line = (
            f"\nRight now these styles are trending: {', '.join(trends)}. "
            "If any of them fit this piece, lean into that trend and name it "
            "explicitly so the shopper knows what's current.\n"
        )

    items = wardrobe.get("items", []) if isinstance(wardrobe, dict) else []

    if not items:
        # Empty-wardrobe branch: general styling advice, no specific pieces.
        prompt = (
            f"A shopper is considering this secondhand piece: {item_desc}.\n"
            f"{trend_line}"
            "They haven't told us what's in their closet yet. Suggest how to "
            "style it in general: what kinds of pieces (tops/bottoms/shoes/"
            "layers) pair well, and what overall vibe it suits. Give 1-2 short, "
            "concrete outfit ideas in 2-4 sentences. Do not invent specific "
            "items they own."
        )
    else:
        # Populated-wardrobe branch: combine with named pieces they own.
        wardrobe_lines = "\n".join(
            f"- {it.get('name', 'item')} ({it.get('category', '?')})"
            for it in items
        )
        prompt = (
            f"A shopper is considering this secondhand piece: {item_desc}.\n\n"
            f"Here is their current wardrobe:\n{wardrobe_lines}\n"
            f"{trend_line}\n"
            "Suggest 1-2 complete outfits that combine the new piece with "
            "specific pieces from their wardrobe, referring to those pieces by "
            "name. Keep it to 2-4 sentences, concrete and wearable, with a note "
            "on the overall vibe."
        )

    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a sharp personal stylist who gives specific, "
                        "wearable outfit advice — never generic filler."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        suggestion = (resp.choices[0].message.content or "").strip()
        if suggestion:
            return suggestion
    except Exception:
        pass  # fall through to a safe, non-empty fallback below

    # Fallback so the tool never returns an empty string or raises.
    return (
        f"Style {new_item.get('title', 'this piece')} as the statement of the "
        "outfit: keep everything else simple and neutral, balance its "
        "proportions (fitted with loose, or loose with fitted), and let one "
        "accessory tie the colors together."
    )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # 1. Guard: no usable outfit → return a descriptive error string, never raise.
    if not outfit or not outfit.strip():
        return (
            "⚠️ Can't write a fit card without an outfit suggestion — "
            "run suggest_outfit first, then pass its result here."
        )

    title = new_item.get("title", "this piece")
    price = new_item.get("price")
    platform = new_item.get("platform", "")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "a steal"

    prompt = (
        "Write a short, shareable caption for a secondhand fashion find — "
        "the kind of thing someone captions an Instagram/TikTok OOTD post with. "
        "Casual and authentic, NOT a product description.\n\n"
        f"Item: {title}\n"
        f"Price: {price_str}\n"
        f"Platform: {platform or 'a thrift app'}\n"
        f"Styled like this: {outfit}\n\n"
        "Rules: 2-4 sentences. Mention the item, the price, and the platform "
        "naturally (once each). Capture the outfit's specific vibe. Lowercase, "
        "casual tone, a couple of emojis are fine. No hashtag spam."
    )

    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write punchy, authentic social captions for thrift "
                        "finds. Every caption sounds fresh and a little different."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=1.0,  # high temp → varied output for different/same inputs
        )
        caption = (resp.choices[0].message.content or "").strip()
        if caption:
            return caption
    except Exception:
        pass  # fall through to a minimal caption built from the item fields

    # Fallback caption so the tool always returns something usable.
    return (
        f"thrifted this {title.lower()} for {price_str}"
        f"{' on ' + platform if platform else ''} ✨ obsessed with how it styles up"
    )


# ── Tool 4 (stretch): estimate_price_fairness ──────────────────────────────────

def estimate_price_fairness(
    new_item: dict, listings: list[dict] | None = None
) -> dict:
    """
    Judge whether a listing is fairly priced versus comparable items.

    Comparison method: take every listing in the SAME category (excluding the
    item itself), average their prices, and compare:
        >= 15% below the average  -> "good deal"
        within +/- 15%            -> "fair"
        >= 15% above the average  -> "overpriced"

    Args:
        new_item: the listing dict to evaluate.
        listings: the comparison pool; defaults to the full dataset.

    Returns:
        A dict: {
            "verdict":     "good deal" | "fair" | "overpriced" | "unknown",
            "reasoning":   human-readable explanation (str),
            "avg_price":   float | None,
            "comparables": int,
        }

    Failure mode: if fewer than 3 comparable listings exist, returns
    verdict="unknown" rather than inventing a verdict from thin data.
    """
    pool = listings if listings is not None else load_listings()
    category = new_item.get("category")
    item_id = new_item.get("id")
    price = new_item.get("price")

    comparables = [
        it
        for it in pool
        if it.get("category") == category
        and it.get("id") != item_id
        and isinstance(it.get("price"), (int, float))
    ]

    if not isinstance(price, (int, float)) or len(comparables) < 3:
        return {
            "verdict": "unknown",
            "reasoning": (
                f"Not enough comparable {category or 'similar'} listings "
                f"({len(comparables)}) to judge this price fairly."
            ),
            "avg_price": None,
            "comparables": len(comparables),
        }

    avg = sum(it["price"] for it in comparables) / len(comparables)
    diff_pct = (price - avg) / avg * 100

    if diff_pct <= -15:
        verdict = "good deal"
    elif diff_pct >= 15:
        verdict = "overpriced"
    else:
        verdict = "fair"

    direction = "below" if diff_pct < 0 else "above"
    reasoning = (
        f"${price:g} is {abs(diff_pct):.0f}% {direction} the ${avg:.0f} average "
        f"of {len(comparables)} comparable {category} listings — {verdict}."
    )
    return {
        "verdict": verdict,
        "reasoning": reasoning,
        "avg_price": round(avg, 2),
        "comparables": len(comparables),
    }


# ── Tool 5 (stretch): get_trending_styles ──────────────────────────────────────

def get_trending_styles(
    size: str | None = None, trends: dict | None = None
) -> dict:
    """
    Surface currently-trending style tags from the mock fashion-platform feed.

    Data source: data/trends.json — a curated snapshot standing in for a public
    fashion platform's trending tags (NOT a live scrape; see README).

    Args:
        size:   the user's size; narrows the trends to that size bucket if known.
        trends: trend data dict; defaults to loading data/trends.json.

    Returns:
        A dict: {
            "trending_tags": list[str],
            "source":        str,
            "note":          str,
        }

    Failure mode: if the trends file is missing or empty, returns an empty
    trending_tags list with an explanatory note so the agent can style normally.
    """
    if trends is None:
        try:
            with open(_TRENDS_PATH, "r", encoding="utf-8") as f:
                trends = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {
                "trending_tags": [],
                "source": "unavailable",
                "note": "Trend feed unavailable — styling without trend input.",
            }

    source = trends.get("source", "mock trend feed")

    # Map a messy size string to one of the size buckets in the feed.
    bucket = None
    if size:
        s = size.upper()
        for candidate in ("XXL", "XL", "L", "M", "S", "XS"):
            if candidate in s:
                bucket = candidate if candidate in trends.get("by_size", {}) else None
                break

    if bucket:
        tags = trends.get("by_size", {}).get(bucket, [])
        note = f"Trending for size {bucket} per {source}."
    else:
        tags = trends.get("overall", [])
        note = f"Overall trending tags per {source}."

    return {"trending_tags": list(tags), "source": source, "note": note}
