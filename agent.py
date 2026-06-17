"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    estimate_price_fairness,
    get_trending_styles,
)
from memory import apply_profile, update_profile


# ── query parsing ─────────────────────────────────────────────────────────────

def _parse_query(query: str) -> dict:
    """
    Extract search parameters from a natural-language query using lightweight
    regex — no LLM needed for this step (documented in planning.md).

    Pulls out:
      - max_price: from patterns like "under $30", "below 40", "$25"
      - size:      from "size M", "in size 8", or a standalone size token
      - description: the query with the price/size phrases stripped out

    Returns a dict: {"description": str, "size": str | None, "max_price": float | None}
    """
    text = query.strip()
    lowered = text.lower()

    # max_price: "under $30", "below 40", "less than $25", or a bare "$30"
    max_price = None
    price_match = re.search(
        r"(?:under|below|less than|max|up to|cheaper than)\s*\$?\s*(\d+(?:\.\d+)?)",
        lowered,
    )
    if not price_match:
        price_match = re.search(r"\$\s*(\d+(?:\.\d+)?)", lowered)
    if price_match:
        max_price = float(price_match.group(1))

    # size: "size M", "in size 8", "in a medium"
    size = None
    size_match = re.search(
        r"\bsize\s+([a-z0-9]+)\b", lowered
    ) or re.search(
        r"\bin\s+(?:a\s+)?(xs|s|m|l|xl|xxl|small|medium|large)\b", lowered
    )
    if size_match:
        size = size_match.group(1).upper()

    # description: strip the price and size phrases so they don't pollute keywords
    description = re.sub(
        r"(?:under|below|less than|max|up to|cheaper than)\s*\$?\s*\d+(?:\.\d+)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    description = re.sub(r"\$\s*\d+(?:\.\d+)?", "", description)
    description = re.sub(
        r"\bsize\s+[a-z0-9]+\b", "", description, flags=re.IGNORECASE
    )
    description = re.sub(r"\s+", " ", description).strip()
    description = description.strip(" ,.;")  # drop stray punctuation left behind

    return {"description": description or text, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
        # ── stretch-feature state ──
        "adjustments": [],           # retry: what constraints were loosened
        "price_assessment": None,    # dict from estimate_price_fairness
        "trends": None,              # list of trending tags used for styling
        "memory_notes": [],          # what the style profile contributed
        "profile": None,             # updated style profile (caller may persist)
    }


# ── search with retry/fallback (stretch) ────────────────────────────────────────

def _search_with_retry(parsed: dict) -> tuple[list[dict], list[str]]:
    """
    Run search_listings; if it returns nothing, progressively loosen the
    constraints and retry. Returns (results, adjustments) where `adjustments`
    lists, in plain language, what was relaxed to get a result.

    Order of loosening: size filter first (most likely to over-restrict), then
    the price ceiling. Keywords are never dropped — that would change intent.
    """
    adjustments: list[str] = []

    results = search_listings(
        description=parsed["description"],
        size=parsed["size"],
        max_price=parsed["max_price"],
    )
    if results:
        return results, adjustments

    # Retry 1: drop the size filter.
    if parsed.get("size"):
        results = search_listings(
            description=parsed["description"], size=None, max_price=parsed["max_price"]
        )
        if results:
            adjustments.append(f"removed the size {parsed['size']} filter")
            return results, adjustments

    # Retry 2: also drop the price ceiling.
    if parsed.get("max_price") is not None:
        results = search_listings(
            description=parsed["description"], size=None, max_price=None
        )
        if results:
            if parsed.get("size"):
                adjustments.append(f"removed the size {parsed['size']} filter")
            adjustments.append(f"ignored the ${parsed['max_price']:g} budget")
            return results, adjustments

    return [], adjustments


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict, profile: dict | None = None) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    `profile` (optional, stretch: style memory) is a remembered style profile.
    When given, the agent fills gaps the query left blank (size/budget/style)
    from it, and returns an updated profile in session["profile"] for the caller
    to persist.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1: fresh session — the single source of truth for this interaction.
    session = _new_session(query, wardrobe)

    # Guard: empty query.
    if not query or not query.strip():
        session["error"] = (
            "Tell me what you're looking for — e.g. 'vintage graphic tee under "
            "$30, size M'."
        )
        return session

    # Step 2: parse the query into search parameters.
    session["parsed"] = _parse_query(query)

    # Step 2b (stretch: style memory): fill blanks from the remembered profile.
    if profile is not None:
        session["parsed"], session["memory_notes"] = apply_profile(
            session["parsed"], profile
        )
    parsed = session["parsed"]

    # Step 3: search, with retry/fallback (stretch). Branch on the result.
    session["search_results"], session["adjustments"] = _search_with_retry(parsed)

    if not session["search_results"]:
        # ERROR BRANCH: stop here. Do NOT call suggest_outfit with empty input.
        bits = []
        if parsed["max_price"] is not None:
            bits.append(f"raising your ${parsed['max_price']:g} budget")
        if parsed["size"]:
            bits.append(f"dropping the size {parsed['size']} filter")
        bits.append("using broader keywords")
        session["error"] = (
            f"No listings matched \"{parsed['description']}\" even after "
            "loosening filters. Try "
            + ", or ".join(bits)
            + "."
        )
        return session

    # Step 4: select the top-ranked result and put it in state.
    session["selected_item"] = session["search_results"][0]

    # Step 4b (stretch: price comparison): is it a fair price?
    session["price_assessment"] = estimate_price_fairness(session["selected_item"])

    # Step 4c (stretch: trend awareness): what's popular for this size?
    trend_info = get_trending_styles(parsed.get("size"))
    session["trends"] = trend_info["trending_tags"]

    # Step 5: suggest an outfit (trend-aware). The tool handles the empty-wardrobe
    # case itself and always returns a non-empty string.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"], trends=session["trends"]
    )

    # Step 6: turn the outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 6b (stretch: style memory): learn from this run for next time.
    if profile is not None:
        session["profile"] = update_profile(
            profile, parsed, session["selected_item"]
        )

    # Step 7: done.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

        # stretch: price comparison + trend awareness
        pa = session["price_assessment"]
        print(f"\nPrice check: [{pa['verdict']}] {pa['reasoning']}")
        print(f"Trending used: {session['trends']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")

    print("\n\n=== Stretch: retry with fallback ===\n")
    # A real item, but with an impossible size that forces a loosen-and-retry.
    session3 = run_agent(
        query="Levi's jeans size ZZ9 under $60",
        wardrobe=get_example_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found after retry: {session3['selected_item']['title']}")
        print(f"Adjustments made: {session3['adjustments']}")

    print("\n\n=== Stretch: style profile memory (two sessions) ===\n")
    from memory import _empty_profile

    profile = _empty_profile()
    print("Session A — explicit: 'vintage graphic tee size M under $30'")
    sA = run_agent("vintage graphic tee size M under $30", get_example_wardrobe(), profile)
    profile = sA["profile"]
    print(f"  parsed: {sA['parsed']}")
    print(f"  profile now: size={profile['size']}, "
          f"top tags={sorted(profile['style_tags'], key=profile['style_tags'].get, reverse=True)[:3]}")

    print("\nSession B — NO size given: 'show me a flannel'")
    sB = run_agent("show me a flannel", get_example_wardrobe(), profile)
    print(f"  parsed (size filled from memory): {sB['parsed']}")
    print(f"  memory contributed: {sB['memory_notes']}")
