"""
app.py

Gradio interface for FitFindr. The layout and wiring are already set up —
your job is to fill in handle_query() so it calls run_agent() and maps
the session results to the three output panels.

Run with:
    python app.py

Then open the localhost URL shown in your terminal (usually http://localhost:7860,
but check your terminal — the port may differ).
"""

import gradio as gr

from agent import run_agent
from memory import load_profile, save_profile
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── query handler ─────────────────────────────────────────────────────────────

def handle_query(
    user_query: str, wardrobe_choice: str, remember: bool = False
) -> tuple[str, str, str]:
    """
    Called by Gradio when the user submits a query.

    Args:
        user_query:      The text the user typed into the search box.
        wardrobe_choice: Either "Example wardrobe" or "Empty wardrobe (new user)".
        remember:        Stretch (style memory) — when True, load the saved style
                         profile, let it fill gaps in the query, and persist what
                         the agent learns for next time.

    Returns:
        A tuple of three strings:
            (listing_text, outfit_suggestion, fit_card)
        Each string maps to one of the three output panels in the UI.
    """
    # 1. Guard against an empty query.
    if not user_query or not user_query.strip():
        return "Please type what you're looking for first.", "", ""

    # 2. Select the wardrobe based on the radio choice.
    if wardrobe_choice == "Empty wardrobe (new user)":
        wardrobe = get_empty_wardrobe()
    else:
        wardrobe = get_example_wardrobe()

    # 3. Run the agent (with the remembered profile if memory is on).
    profile = load_profile() if remember else None
    session = run_agent(user_query, wardrobe, profile=profile)

    # Persist anything learned this session.
    if remember and session.get("profile") is not None:
        save_profile(session["profile"])

    # 4. Error path: show the message in the first panel, leave the rest empty.
    if session["error"]:
        return f"⚠️ {session['error']}", "", ""

    # 5. Success path: format the chosen listing + stretch-feature info.
    item = session["selected_item"]
    brand = item.get("brand") or "—"
    lines = []

    # Retry note (stretch): tell the user what was loosened, if anything.
    if session.get("adjustments"):
        lines.append("ℹ️ Heads up — I had to " + " and ".join(session["adjustments"])
                     + " to find a match.\n")
    # Memory note (stretch): what remembered preferences contributed.
    if session.get("memory_notes"):
        lines.append("🧠 From your saved style: " + "; ".join(session["memory_notes"])
                     + ".\n")

    lines.append(
        f"{item['title']}\n"
        f"${item['price']:g} · {item['platform']} · {item['condition']} condition\n"
        f"Size: {item['size']} · Brand: {brand}\n"
        f"Style: {', '.join(item['style_tags'])}\n\n"
        f"{item['description']}"
    )

    # Price comparison (stretch).
    pa = session.get("price_assessment")
    if pa:
        lines.append(f"\n💰 Price check [{pa['verdict']}]: {pa['reasoning']}")

    # Trend awareness (stretch).
    if session.get("trends"):
        lines.append(f"\n🔥 Trending now: {', '.join(session['trends'])}")

    return "\n".join(lines), session["outfit_suggestion"], session["fit_card"]


# ── interface ─────────────────────────────────────────────────────────────────

EXAMPLE_QUERIES = [
    "vintage graphic tee under $30",
    "90s track jacket in size M",
    "flowy midi skirt under $40",
    "black combat boots size 8",
    "designer ballgown size XXS under $5",   # deliberate no-results test
]

def build_interface():
    with gr.Blocks(title="FitFindr") as demo:
        gr.Markdown("""
# FitFindr 🛍️
Find secondhand pieces and get outfit ideas based on your wardrobe.
Describe what you're looking for — include size and price if you want to filter.
        """)

        with gr.Row():
            query_input = gr.Textbox(
                label="What are you looking for?",
                placeholder="e.g. vintage graphic tee under $30, size M",
                lines=2,
                scale=3,
            )
            wardrobe_choice = gr.Radio(
                choices=["Example wardrobe", "Empty wardrobe (new user)"],
                value="Example wardrobe",
                label="Wardrobe",
                scale=1,
            )

        remember = gr.Checkbox(
            label="🧠 Remember my style (reuse size / budget / style across searches)",
            value=False,
        )

        submit_btn = gr.Button("Find it", variant="primary")

        with gr.Row():
            listing_output = gr.Textbox(
                label="🛍️ Top listing found",
                lines=8,
                interactive=False,
            )
            outfit_output = gr.Textbox(
                label="👗 Outfit idea",
                lines=8,
                interactive=False,
            )
            fitcard_output = gr.Textbox(
                label="✨ Your fit card",
                lines=8,
                interactive=False,
            )

        gr.Examples(
            examples=[[q, "Example wardrobe"] for q in EXAMPLE_QUERIES],
            inputs=[query_input, wardrobe_choice],
            label="Try these queries",
        )

        submit_btn.click(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice, remember],
            outputs=[listing_output, outfit_output, fitcard_output],
        )
        query_input.submit(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice, remember],
            outputs=[listing_output, outfit_output, fitcard_output],
        )

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch()
