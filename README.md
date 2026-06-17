# FitFindr 🛍️

A multi-tool AI agent that helps you find secondhand clothing and figure out how
to wear it. Describe what you want in plain language and FitFindr searches a mock
listings dataset, styles the find against your wardrobe, and writes a shareable
caption for it — handling the cases where a tool returns nothing useful.

```
You: "vintage graphic tee under $30, size M"
  → searches 40 listings, picks the best match
  → styles it with your baggy jeans + combat boots
  → "just scored this sick graphic tee on depop for $24 🤘 ..."
```

---

## Setup

**macOS / Linux:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

Create a `.env` file in the repo root (already git-ignored — never commit it):
```
GROQ_API_KEY=your_key_here
```
Free key at [console.groq.com](https://console.groq.com) — no credit card required.

**Run it:**
```bash
python app.py        # Gradio UI — open the URL printed in your terminal
python agent.py      # CLI: runs the happy path + the no-results path
pytest tests/        # tool isolation tests (9 tests)
```

LLM: Groq `llama-3.3-70b-versatile`. Mock data lives in `data/` — no external API.

---

## Project layout

```
tools.py        # the 5 tools (3 required + price comparison & trend awareness)
agent.py        # run_agent(): the planning loop + session state + retry/fallback
memory.py       # style profile memory (stretch) — load/apply/update profile
app.py          # Gradio UI (handle_query maps the session to 3 panels)
utils/          # data_loader.py — load_listings / get_example_wardrobe / get_empty_wardrobe
data/           # listings.json (40 items), wardrobe_schema.json, trends.json (mock trend feed)
tests/          # test_tools.py + test_agent.py — 20 tests incl. every failure mode
planning.md     # the spec, written before any code
```

---

## Tool Inventory

Documented signatures match the actual functions in `tools.py`.

### 1. `search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]`
**Purpose:** Find listings matching the user's keywords, optional size, and optional
price ceiling, ranked by relevance. Pure Python — no LLM.
- `description` (str): keywords, e.g. `"vintage graphic tee"`. Scored against each listing's title, style_tags, category, and description.
- `size` (str | None): case-insensitive substring match in both directions, so `"M"` matches `"S/M"`. `None` skips size filtering.
- `max_price` (float | None): inclusive ceiling. `None` skips price filtering.
- **Returns:** `list[dict]` of full listing records (`id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), sorted best-match first. Returns `[]` when nothing matches — never raises.

### 2. `suggest_outfit(new_item: dict, wardrobe: dict) -> str`
**Purpose:** Style the found item — 1–2 complete outfits combining it with pieces
the user already owns. LLM-backed.
- `new_item` (dict): a listing dict (the selected search result).
- `wardrobe` (dict): a wardrobe dict with an `"items"` list; may be empty.
- **Returns:** a non-empty `str` of outfit suggestions. With a populated wardrobe it names specific owned pieces; with an empty wardrobe it gives general styling advice instead.

### 3. `create_fit_card(outfit: str, new_item: dict) -> str`
**Purpose:** Turn the outfit into a short, casual, shareable caption — an OOTD post,
not a product description. LLM-backed, high temperature so output varies.
- `outfit` (str): the suggestion string from `suggest_outfit`.
- `new_item` (dict): the listing dict (for name, price, platform).
- **Returns:** a 2–4 sentence `str` caption naming the item, price, and platform once each. If `outfit` is empty, returns a descriptive error string instead of calling the LLM.

### 4. `estimate_price_fairness(new_item: dict, listings: list[dict] | None = None) -> dict` *(stretch)*
**Purpose:** Judge whether the found item is fairly priced vs. comparable listings. Pure Python.
- `new_item` (dict): the listing to evaluate.
- `listings` (list[dict] | None): comparison pool; defaults to the full dataset.
- **Returns:** a dict `{verdict, reasoning, avg_price, comparables}` where `verdict` is `"good deal"`, `"fair"`, `"overpriced"`, or `"unknown"`. Returns `"unknown"` when fewer than 3 same-category comparables exist.

### 5. `get_trending_styles(size: str | None = None, trends: dict | None = None) -> dict` *(stretch)*
**Purpose:** Surface currently-trending style tags (optionally for the user's size) so styling can lean into them.
- `size` (str | None): narrows trends to a size bucket; `None` returns overall trends.
- `trends` (dict | None): trend data; defaults to loading `data/trends.json`.
- **Returns:** a dict `{trending_tags: list[str], source: str, note: str}`. Returns an empty `trending_tags` list with an explanatory note if the feed is missing/empty.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in `agent.py` is a **guarded, state-driven sequence** —
it inspects each tool's output before deciding the next action, so behavior changes
with the input rather than always running the same three calls.

1. **Create session** (`_new_session`) — the single source of truth for the run.
2. **Guard empty query** → set `error`, return.
3. **Parse** the query into `description` / `size` / `max_price` (regex in `_parse_query`, no LLM).
4. **Search** with the parsed params.
   - **Branch — no results:** set `session["error"]` with a specific "try raising your budget / dropping the size filter / broader keywords" message and **return early**. `suggest_outfit` is never called with empty input; `fit_card` stays `None`.
   - **Results found:** continue.
5. **Select** `search_results[0]` → `selected_item`.
6. **Suggest outfit** with the selected item + wardrobe.
7. **Create fit card** from the outfit + selected item.
8. **Return** the completed session.

The decisive branch is step 4: a query like `"designer ballgown size XXS under $5"`
terminates after the search with an error, while `"vintage graphic tee under $30"`
runs the full pipeline. Same code, different paths.

**Stretch steps woven into the loop:** step 3 uses `_search_with_retry`, which
loosens constraints and retries before declaring failure (retry logic). Between
selection and styling, the loop runs `estimate_price_fairness` (price comparison)
and `get_trending_styles` (trend awareness), passing the trends into
`suggest_outfit`. When a style profile is supplied, the loop fills query gaps from
it before searching and updates it afterward (style memory). All results land in
the session dict (`adjustments`, `price_assessment`, `trends`, `memory_notes`,
`profile`).

## State Management

A single **session dict** carries all state for one interaction. Each step reads
upstream fields and writes its own, so nothing is re-entered or hardcoded between
tools.

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | entry point | parse step |
| `parsed` (`description`, `size`, `max_price`) | `_parse_query` | `search_listings` |
| `search_results` | `search_listings` | selection step |
| `selected_item` | selection step | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | entry point | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | final output |
| `error` | any step, on failure | final output / UI |

The found item flows from `search_results[0]` into `selected_item` and on into both
LLM tools — verified: `session["selected_item"] is session["search_results"][0]` is
`True`. `run_agent` returns the session; callers check `session["error"]` first.

---

## Error Handling

Every tool owns its failure mode; the loop never crashes or passes empty data
downstream.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No match (returns `[]`) | Loop sets `error` with a concrete fix ("raising your $5 budget, or dropping the size XXS filter, or using broader keywords") and stops before any downstream tool. |
| `suggest_outfit` | Empty wardrobe (`items == []`) | Switches to a general-styling prompt; always returns a non-empty string. LLM error → short fallback styling note. |
| `create_fit_card` | Empty/whitespace outfit | Skips the LLM, returns "⚠️ Can't write a fit card without an outfit suggestion…". LLM error → minimal caption from item title + price. |

**Concrete example from testing** (Milestone 5):
```
$ python -c "from tools import search_listings; print(search_listings('designer ballgown', size='XXS', max_price=5))"
[]

$ # full agent on the same query:
ERROR: No listings matched "designer ballgown". Try raising your $5 budget,
       or dropping the size XXS filter, or using broader keywords.
fit_card is None: True
```
The empty list never becomes an exception, and the agent gives the user actionable
next steps instead of a dead end.

---

## AI Usage
**1. Designing the spec in `planning.md` (Milestone 2).** I used AI to turn my
rough tool ideas into implementation-ready spec blocks, giving it the data fields
from `listings.json`/`wardrobe_schema.json` and the required function names. I had
it draft the planning-loop branches, the state table, the error table, and the
Mermaid architecture diagram. I reviewed every signature against what I actually
wanted and corrected the planning loop so the no-results case *stops* instead of
calling `suggest_outfit` with empty input — the most important design decision in
the project.

**2. Implementing `search_listings` (Milestone 3, pure Python).** I gave the AI the
Tool 1 spec block (inputs, return type, failure mode) plus the docstring and asked
it to implement the function using `load_listings()`. I reviewed that it filtered on
all three parameters and returned `[]` (not `None`) on no match, then tested it
against 4 queries including an impossible one. I kept the weighted keyword scoring
(title/tags above description) because it ranked the graphic tees correctly, and I
changed the size filter to match substrings in both directions so `"M"` would catch
`"S/M"`.

**3. Implementing the LLM tools `suggest_outfit` and `create_fit_card`
(Milestone 3).** I directed the AI to write the prompts and Groq calls, specifying
the empty-wardrobe branch for `suggest_outfit` and the empty-outfit guard for
`create_fit_card`. I reviewed and adjusted the prompts so the outfit named specific
wardrobe pieces (not generic advice) and the fit card sounded like a real OOTD post
rather than a product description. I raised `create_fit_card`'s temperature to 1.0
after testing showed near-identical captions at lower values, and I added the
fallback returns so neither tool can raise or return an empty string.

**4. Implementing the planning loop (Milestone 4).** I gave the AI the Architecture
diagram and the Planning Loop + State Management sections and asked it to implement
`run_agent()`. Before trusting it I verified it branched on the empty
`search_results` (returning early) and stored every value in the session dict rather
than threading variables through. I added the regex `_parse_query` helper (the spec
left parsing to me) and cleaned up stray punctuation it left in the description.

**5. Implementing the four stretch features.** I gave the AI the Stretch Features
section of `planning.md` (retry order, price-fairness thresholds, trend data shape,
memory storage format) and asked it to implement them across `tools.py`, `agent.py`,
and a new `memory.py`. I reviewed that the price tool returns `"unknown"` instead of
a fake verdict when there are too few comparables, that trend tags are actually
passed into the `suggest_outfit` prompt (not computed and ignored), and that style
memory never overrides a value the user typed explicitly. I set the retry loosening
order myself (size before price, keywords never dropped) and made the trends file an
honest mock feed rather than claiming a live scrape.

**6. Writing tests and documentation.** I had the AI generate the pytest suite
(`tests/`) and draft this README, then reviewed both: I made sure there was at least
one test per failure mode, that the LLM-tool tests assert on the contract (non-empty
string / error string) rather than exact wording so they stay stable, and that the
documented tool signatures exactly match the code.

---

## Spec Reflection

**One way the spec helped:** Writing the State Management table in `planning.md`
before coding meant `run_agent()` was almost mechanical to implement — every field
already had a defined writer and reader, so I never had to stop and decide where a
value should live. It also made the "no re-entry" requirement easy to verify: I just
checked that `selected_item` was the same object as `search_results[0]`.

**One divergence and why:** My spec didn't include a query parser — the planning
loop assumed it would receive a clean `description`, `size`, and `max_price`. In
practice the Gradio UI passes one free-text string, so I added `_parse_query()`
(regex for "under $X" and "size M") that the spec never mentioned. I documented the
choice in the AI Tool Plan section after the fact. Everything else matched the spec.

---

## Stretch Features (all four implemented)

**1. Retry logic with fallback.** When `search_listings` returns nothing,
`run_agent` (`_search_with_retry`) automatically loosens constraints and retries —
first dropping the size filter, then the price ceiling (keywords are never dropped,
since that would change intent). It records what it relaxed in
`session["adjustments"]` and the UI shows *"Heads up — I had to remove the size X
filter to find a match."* A truly impossible query (`designer ballgown size XXS
under $5`) still fails gracefully with suggestions.

**2. Price comparison tool** (`estimate_price_fairness`). For the selected item it
gathers all **same-category** listings (excluding the item itself), averages their
prices, and compares: ≥15% below average → *good deal*, within ±15% → *fair*, ≥15%
above → *overpriced*. The verdict + reasoning (e.g. *"$24 is 11% above the $22
average of 14 comparable tops listings — fair"*) appears in the listing panel. With
fewer than 3 comparables it returns `unknown` rather than a misleading verdict.

**3. Trend awareness tool** (`get_trending_styles`). **Data source:**
`data/trends.json` — a curated snapshot standing in for a public fashion-platform's
trending tags (e.g. Depop/Pinterest). It is a **mock seed file, not a live scrape**.
The tool returns trending tags for the user's size bucket; those tags are passed
into `suggest_outfit`, which leans into any trend that fits the item and names it —
so the influence is visible in the outfit text ("...for a grunge-inspired look
that's currently trending").

**4. Style profile memory** (`memory.py`). **Storage:** a JSON file
(`data/style_profile.json`, git-ignored) holding the user's last `size`, last
`max_price`, and a frequency count of liked `style_tags`. When the "🧠 Remember my
style" box is checked, `run_agent` fills gaps the current query left blank from the
profile (an explicit value always wins) and learns from each finished run. Demo:
search *"vintage graphic tee size M under $30"*, then search *"show me a flannel"* —
the second search reuses size M, the $30 budget, and the "y2k/vintage" lean without
re-entry, shown as *"🧠 From your saved style: used your usual size M…"*.

Each stretch feature has its own failure mode and tests (`tests/test_tools.py`,
`tests/test_agent.py`); all 20 tests pass.

