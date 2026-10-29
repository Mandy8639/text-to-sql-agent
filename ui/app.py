"""Streamlit UI: `streamlit run ui/app.py` (or `make run-ui`). Talks to the API over HTTP.

The UI holds no secrets and no database access: everything goes through the API, including
feedback (which the API forwards to Langfuse).
"""

import contextlib
import os

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

# Dev-split questions only: the UI never shows test or hold-out questions.
EXAMPLES = [
    "How many unique customers are there?",
    "What was the total revenue in each month from January to August 2018?",
    "What are the top 5 product categories by number of units sold?",
    "What is the average delivery time in days (as a decimal, from purchase to delivery) "
    "for each customer state?",
    "What percentage of all payment records does each payment type account for?",
    "Show me the sales numbers.",
    "Delete all canceled orders.",
]

st.set_page_config(page_title="Text-to-SQL Analytics", page_icon="📊", layout="wide")


# --- API calls ---------------------------------------------------------------------------------


def call_api(method: str, path: str, **kwargs) -> httpx.Response | None:
    try:
        return httpx.request(method, f"{API_URL}{path}", timeout=120, **kwargs)
    except httpx.HTTPError:
        st.error(f"Can't reach the API at {API_URL}. Start it with `make run-api`.")
        return None


@st.cache_data(ttl=30, show_spinner=False)
def health() -> dict | None:
    try:
        return httpx.get(f"{API_URL}/health", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        return None


# --- sidebar -----------------------------------------------------------------------------------

with st.sidebar:
    st.header("Try an example")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state.question = ex
            st.session_state.run = True
    st.divider()
    h = health()
    if h is None:
        st.error("API offline")
    elif h.get("status") == "ok":
        st.success(f"API online · agent {h.get('agent')}")
    else:
        st.warning(f"API degraded: {h}")
    st.caption(
        "Data: Olist Brazilian e-commerce, Sep 2016 – Aug 2018 (reliable months 2017-01 to "
        "2018-08). Money in BRL."
    )

# --- main --------------------------------------------------------------------------------------

st.title("📊 Ask the e-commerce database")
st.write(
    "Ask a business question in plain English. The agent writes PostgreSQL, checks it for "
    "safety, runs it read-only, and explains the result."
)

with st.form("ask"):
    question = st.text_input(
        "Your question", key="question", placeholder="e.g. Which states have the most orders?"
    )
    submitted = st.form_submit_button("Ask", type="primary")

if submitted or st.session_state.pop("run", False):
    if len(question.strip()) < 3:
        st.warning("Please type a question.")
    else:
        with st.spinner("Writing and running the query..."):
            resp = call_api("POST", "/query", json={"question": question})
        if resp is not None:
            if resp.status_code == 200:
                st.session_state.result = resp.json()
            elif resp.status_code == 429:
                st.warning(resp.json().get("detail", "Too many questions; wait a minute."))
            elif resp.status_code == 422:
                st.warning("That question couldn't be accepted (too short, too long, or invalid).")
            else:
                st.error(f"Something went wrong (error id {resp.json().get('error_id', '?')}).")

r = st.session_state.get("result")
if r:
    outcome = r["outcome"]
    if outcome == "answer":
        st.success(r["explanation"] or "Here is the result.")
        if r["explanation_grounded"] is False:
            st.warning(
                "Check this explanation: it mentions numbers not found in the result "
                f"({', '.join(r['ungrounded_numbers'])})."
            )
    elif outcome == "clarify":
        st.info(f"**I need a bit more detail:** {r['explanation']}")
    elif outcome == "refuse":
        st.warning(f"**I can't answer that:** {r['explanation']}")
    else:
        st.error(r["explanation"])

    if outcome == "answer":
        df = pd.DataFrame(r["rows"], columns=r["columns"])
        chart = r["chart"]
        if r["row_count"] == 1 and len(r["columns"]) == 1:
            st.metric(
                r["columns"][0],
                f"{df.iloc[0, 0]:,}" if isinstance(df.iloc[0, 0], int | float) else df.iloc[0, 0],
            )
        elif chart["type"] != "none":
            plot = df.copy()
            if chart["type"] == "line" and plot[chart["x"]].dtype == object:
                # Period strings ('2018-01-01') become real dates so the axis is ordered by time.
                with contextlib.suppress(ValueError, TypeError):
                    plot[chart["x"]] = pd.to_datetime(plot[chart["x"]])
            if chart["type"] == "line":
                st.line_chart(plot, x=chart["x"], y=chart["y"])
            else:  # keep the query's ORDER BY (a top-N ranking), not alphabetical order
                st.bar_chart(plot, x=chart["x"], y=chart["y"], sort=False)
        st.dataframe(df, use_container_width=True, hide_index=True)
        if r["truncated"]:
            st.caption(f"Showing the first {len(r['rows'])} rows.")

        with st.expander("Show SQL"):
            st.code(r["sql"], language="sql")

    with st.expander("Show reasoning steps"):
        # The result lives in session_state across reruns, so read it without mutating it.
        for step in r["steps"]:
            st.markdown(f"**{step.get('step', 'step')}**")
            if step.get("sql"):
                st.code(step["sql"], language="sql")
            details = {k: v for k, v in step.items() if k not in ("step", "sql") and v is not None}
            st.json(details, expanded=False)

    meta_col, up_col, down_col = st.columns([6, 1, 1])
    meta_col.caption(
        f"{r['latency_ms'] / 1000:.1f} s · ${r['cost_usd']:.4f} · request {r['request_id']}"
        + (f" · trace {r['trace_id']}" if r["trace_id"] else "")
    )
    given = st.session_state.setdefault("feedback_given", set())
    if r["trace_id"] and r["trace_id"] not in given:
        for col, positive, label in ((up_col, True, "👍"), (down_col, False, "👎")):
            if col.button(label, key=f"fb-{positive}"):
                fb = call_api("POST", "/feedback",
                              json={"trace_id": r["trace_id"], "positive": positive})  # fmt: skip
                if fb is not None and fb.status_code == 204:
                    given.add(r["trace_id"])
                    st.toast("Thanks for the feedback!")
                    st.rerun()  # redraw at once: replace the buttons with "Feedback sent"
    elif r["trace_id"]:
        up_col.caption("Feedback sent")
