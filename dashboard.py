#!/usr/bin/env python3
"""Streamlit dashboard for Midnight Season 1 Mythic+ performance data.

Run with:  streamlit run dashboard.py
Data:      data/mythic_runs.csv  (produced by scripts/fetch_data.py)

All shaping below (hero-talent merging, date parsing, filters) happens in
memory at load time — the CSV on disk is never modified.
"""
import os
import pathlib

import altair as alt
import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent
CSV_FILE = pathlib.Path(os.environ.get("WOWLOGS_CSV", ROOT / "data" / "mythic_runs.csv"))
DEMO_FILE = ROOT / "data" / "demo_supplement.csv"

ACCENT = "#2a78d6"          # single-hue magnitude encoding for bar charts
CHART_MAX = 40              # bars per chart
# a region is excluded from the default Region filter when more than this
# share of its rows lack combatant info (i.e. hero talent was unresolvable)
REGION_MISSING_CUTOFF = 0.25

st.set_page_config(
    page_title="Midnight S1 Mythic+ Dashboard",
    page_icon="⚔️",
    layout="wide",
)


def _parse_started_at(raw: pd.Series) -> pd.Series:
    """Robust date parsing.

    The pipeline stores WCL fight start times as epoch *milliseconds*; naive
    pd.to_datetime() interprets integers as nanoseconds, which lands every
    run in January 1970. Demo/legacy rows may carry ISO strings instead, so
    both forms are handled, and anything outside the plausible window is
    treated as missing rather than displayed.
    """
    num = pd.to_numeric(raw, errors="coerce")
    dt = pd.to_datetime(num, unit="ms", errors="coerce")
    str_mask = num.isna() & raw.notna()
    if str_mask.any():
        dt.loc[str_mask] = pd.to_datetime(raw[str_mask], errors="coerce")
    return dt.where((dt.dt.year >= 2020) & (dt <= pd.Timestamp.now() + pd.Timedelta(days=2)))


def _merge_unknown_heroes(df: pd.DataFrame) -> pd.DataFrame:
    """Fold 'Unknown' hero talents (logs uploaded without combatant info)
    into the most-used hero talent of the same spec, falling back to the
    most-used of the class. Display-time only — the CSV keeps the truth."""
    known = df[df["hero_talent"] != "Unknown"]
    if known.empty:
        return df
    spec_mode = known.groupby(["class", "spec"])["hero_talent"] \
        .agg(lambda s: s.value_counts().idxmax()).to_dict()
    class_mode = known.groupby("class")["hero_talent"] \
        .agg(lambda s: s.value_counts().idxmax()).to_dict()
    unk = df["hero_talent"] == "Unknown"
    if unk.any():
        df.loc[unk, "hero_talent"] = df.loc[unk].apply(
            lambda r: spec_mode.get((r["class"], r["spec"]),
                                    class_mode.get(r["class"], "Unknown")),
            axis=1)
    return df


@st.cache_data(show_spinner="Loading run data…")
def load_data(include_demo: bool = False):
    """Returns (dataframe, per-region missing-combatant-info share)."""
    try:
        df = pd.read_csv(CSV_FILE)
    except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame(), {}
    if include_demo and DEMO_FILE.exists():
        df = pd.concat([df, pd.read_csv(DEMO_FILE)], ignore_index=True)
    if df.empty:
        return pd.DataFrame(), {}
    df["key_level"] = df["key_level"].astype(int)
    df["deaths"] = df["deaths"].astype(int)
    for col in ("class", "spec", "hero_talent", "role", "region"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    df["started_at"] = _parse_started_at(df["started_at"])
    # measured BEFORE merging, so the region-quality rule sees the truth
    region_missing = df.groupby("region")["hero_talent"] \
        .agg(lambda s: (s == "Unknown").mean()).to_dict()
    df = _merge_unknown_heroes(df)
    return df, region_missing


TICK_COLOR = "#52514e"  # neutral ink for the counterpart-metric marker

TOOLTIPS = [
    alt.Tooltip("class:N", title="Class"),
    alt.Tooltip("spec:N", title="Spec"),
    alt.Tooltip("hero_talent:N", title="Hero Talent"),
    alt.Tooltip("total_runs:Q", title="Total Runs", format=","),
    alt.Tooltip("avg_dps:Q", title="Average DPS", format=","),
    alt.Tooltip("median_dps:Q", title="Median DPS", format=","),
    alt.Tooltip("avg_deaths:Q", title="Average Deaths", format=".2f"),
    alt.Tooltip("median_deaths:Q", title="Median Deaths", format=".1f"),
    alt.Tooltip("deathless:Q", title="Deathless runs %", format=".1f"),
]


def bar_chart(data: pd.DataFrame, value_col: str, other_col: str | None,
              title: str, other_title: str, fmt: str,
              sort_mode: str, top_n: int):
    """Horizontal bars of `value_col` with the value printed at each bar end
    and, when `other_col` is given (same units only), a neutral tick
    overlaying that counterpart metric."""
    top = data.sort_values(value_col, ascending=False).head(top_n).copy()
    top["label"] = top["spec"] + " " + top["class"] + " — " + top["hero_talent"]
    top["value_text"] = top[value_col].map(lambda v: format(v, fmt))
    # anchor the printed value past BOTH the bar and the tick so they never collide
    cols = [value_col] + ([other_col] if other_col else [])
    top["label_x"] = top[cols].max(axis=1)

    if sort_mode == "Name (A → Z)":
        top = top.sort_values("label")
        y_sort = None
    elif sort_mode == "Value (low → high)":
        y_sort = "x"
    else:
        y_sort = "-x"
    y = alt.Y("label:N", sort=y_sort, title=None, axis=alt.Axis(labelLimit=320))
    # headroom so end-of-bar labels never clip and out-lying ticks stay visible
    xmax = float(top[cols].max().max()) * 1.18
    x_scale = alt.Scale(domain=[0, xmax if xmax > 0 else 1], nice=False)

    base = alt.Chart(top)
    bars = base.mark_bar(size=16, cornerRadiusEnd=4, color=ACCENT).encode(
        x=alt.X(f"{value_col}:Q", title=title, scale=x_scale,
                axis=alt.Axis(format=fmt)),
        y=y, tooltip=TOOLTIPS,
    )
    labels = base.mark_text(align="left", dx=7, color=TICK_COLOR).encode(
        x=alt.X("label_x:Q", scale=x_scale), y=y, text="value_text:N",
    )
    if not other_col:
        return (bars + labels).properties(height=max(28 * len(top), 120))
    ticks = base.mark_tick(color=TICK_COLOR, thickness=2.5, size=15).encode(
        x=alt.X(f"{other_col}:Q", scale=x_scale,
                title=f"{title} (tick: {other_title})"),
        y=y, tooltip=TOOLTIPS,
    )
    return (bars + labels + ticks).properties(height=max(28 * len(top), 120))


def main() -> None:
    st.title("⚔️ Mythic+ Performance — Midnight Season 1")

    include_demo = st.sidebar.checkbox(
        "Include demo rows (fake)", value=False,
        help="Adds a small synthetic supplement (key-25 runs, KR/TW regions) "
             "for testing UI functionality; marked report_code=FAKEDEMO",
    ) if DEMO_FILE.exists() else False

    df, region_missing = load_data(include_demo)
    if df.empty:
        st.error(
            "No data available yet. Run `python3 scripts/fetch_data.py` to build "
            "`data/mythic_runs.csv`, then hit **Refresh Data**."
        )
        if st.button("🔄 Refresh Data"):
            load_data.clear()
            st.rerun()
        st.stop()

    # ------------------------------------------------------------------ sidebar
    with st.sidebar:
        st.header("Filters")

        if st.button("🔄 Refresh Data", width="stretch",
                     help="Clear the cache and reload the CSV from disk"):
            load_data.clear()
            st.rerun()

        classes = st.multiselect(
            "Class", sorted(df["class"].dropna().unique()), default=[])
        pool = df if not classes else df[df["class"].isin(classes)]

        specs = st.multiselect(
            "Spec", sorted(pool["spec"].dropna().unique()), default=[])
        pool = pool if not specs else pool[pool["spec"].isin(specs)]

        heroes = st.multiselect(
            "Hero Talent", sorted(pool["hero_talent"].dropna().unique()), default=[])

        dungeons = st.multiselect(
            "Dungeon", sorted(df["dungeon"].dropna().unique()), default=[])

        klo, khi = int(df["key_level"].min()), int(df["key_level"].max())
        if klo < khi:
            key_range = st.slider(
                "Key Level", klo, khi, (klo, khi),
                help="Runs outside this keystone range are excluded")
        else:
            key_range = (klo, khi)
            st.caption(f"Key Level: all runs are +{klo}")

        # ---- date range (week granularity, oldest week -> today) ----
        dated = df["started_at"].dropna()
        week_range = None
        if not dated.empty:
            first_week = (dated.min() - pd.Timedelta(days=int(dated.min().weekday()))).normalize()
            today = pd.Timestamp.now().normalize()
            week_starts = list(pd.date_range(first_week, today, freq="7D"))
            if len(week_starts) >= 2:
                labels = [w.strftime("%Y-%m-%d") for w in week_starts]
                sel = st.select_slider(
                    "Date Range (week starting)", options=labels,
                    value=(labels[0], labels[-1]),
                    help="Include only runs whose start falls inside the "
                         "selected weeks (inclusive)")
                if (sel[0], sel[1]) != (labels[0], labels[-1]):
                    week_range = (pd.Timestamp(sel[0]),
                                  pd.Timestamp(sel[1]) + pd.Timedelta(days=7))
            else:
                st.caption("Date Range: all data is from the current week")

        min_runs = st.slider(
            "Minimum Runs Threshold", 1, 500, 3,
            help="Hide Class/Spec/Hero Talent rows with fewer than this many runs")

        roles = st.multiselect(
            "Role", ["DPS", "Healer", "Tank"], default=[],
            help="Optional: limit to a role (empty = all)")

        region_opts = sorted(df["region"].unique())
        good_regions = [r for r in region_opts
                        if region_missing.get(r, 0) <= REGION_MISSING_CUTOFF]
        regions_sel = st.multiselect(
            "Region", region_opts,
            default=good_regions if len(good_regions) < len(region_opts) else [],
            help="All regions are included by default; a region is pre-excluded "
                 f"only if over {REGION_MISSING_CUTOFF:.0%} of its reports lack "
                 "combatant data (none currently do)")

        st.caption(
            "Empty multiselects mean *no filter*. Data: Warcraft Logs "
            "fight rankings, keys 12–25+."
        )

    # ------------------------------------------------------------------ filter
    mask = df["key_level"].between(*key_range)
    if classes:
        mask &= df["class"].isin(classes)
    if specs:
        mask &= df["spec"].isin(specs)
    if heroes:
        mask &= df["hero_talent"].isin(heroes)
    if dungeons:
        mask &= df["dungeon"].isin(dungeons)
    if roles:
        mask &= df["role"].isin(roles)
    if regions_sel:
        mask &= df["region"].isin(regions_sel)
    if week_range is not None:
        mask &= df["started_at"].ge(week_range[0]) & df["started_at"].lt(week_range[1])
    view = df[mask]

    if view.empty:
        st.warning("No runs match the current filters.")
        st.stop()

    # ------------------------------------------------------------------ headline
    n_runs = view[["report_code", "fight_id"]].drop_duplicates().shape[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Dungeon runs", f"{n_runs:,}")
    c2.metric("Player parses", f"{len(view):,}")
    c3.metric("Dungeons", f"{view['dungeon'].nunique()}")
    oldest, newest = view["started_at"].min(), view["started_at"].max()
    if pd.notna(oldest) and pd.notna(newest):
        c4.metric("Run dates", f"{oldest:%b %d} – {newest:%b %d}")
    else:
        c4.metric("Run dates", "—")

    # ------------------------------------------------------------------ aggregate
    agg = (
        view.groupby(["class", "spec", "hero_talent"])
        .agg(
            total_runs=("dps", "size"),
            avg_dps=("dps", "mean"),
            median_dps=("dps", "median"),
            avg_deaths=("deaths", "mean"),
            median_deaths=("deaths", "median"),
            deathless=("deaths", lambda s: (s == 0).mean() * 100),
        )
        .reset_index()
    )
    agg = agg[agg["total_runs"] >= min_runs]
    agg["avg_dps"] = agg["avg_dps"].round(0).astype(int)
    agg["median_dps"] = agg["median_dps"].round(0).astype(int)
    agg = agg.sort_values("avg_dps", ascending=False).reset_index(drop=True)

    if agg.empty:
        st.warning(
            "Every group fell below the minimum-runs threshold "
            f"({min_runs}). Lower the slider to see sparser combinations."
        )
        st.stop()

    st.subheader("Performance by Class / Spec / Hero Talent")
    st.dataframe(
        agg.rename(columns={
            "class": "Class", "spec": "Spec", "hero_talent": "Hero Talent",
            "total_runs": "Total Runs", "avg_dps": "Average DPS",
            "median_dps": "Median DPS", "avg_deaths": "Average Deaths",
            "median_deaths": "Median Deaths", "deathless": "Deathless %",
        }),
        width="stretch",
        hide_index=True,
        column_config={
            "Total Runs": st.column_config.NumberColumn(format="localized"),
            "Average DPS": st.column_config.NumberColumn(format="localized"),
            "Median DPS": st.column_config.NumberColumn(format="localized"),
            "Average Deaths": st.column_config.NumberColumn(format="%.2f"),
            "Median Deaths": st.column_config.NumberColumn(format="%.1f"),
            "Deathless %": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

    # ------------------------------------------------------------------ charts
    st.subheader("Group comparisons")
    ctl1, ctl2 = st.columns([1, 1])
    sort_mode = ctl1.selectbox(
        "Sort bars by",
        ["Value (high → low)", "Value (low → high)", "Name (A → Z)"],
        help="Bars are always the top groups by the tab's metric; this "
             "controls their display order")
    top_n = ctl2.slider(
        "Groups shown", 5, max(min(len(agg), 100), 6), min(CHART_MAX, len(agg)),
        help="How many of the top groups (by the tab's metric) to draw")

    specs_charts = [
        ("Average DPS", "avg_dps", "median_dps", "Median DPS", ",.0f"),
        ("Median DPS", "median_dps", "avg_dps", "Average DPS", ",.0f"),
        ("Average Deaths", "avg_deaths", "median_deaths", "Median Deaths", ".2f"),
        ("Deathless Runs %", "deathless", None, "", ".1f"),
    ]
    for tab, (title, col, other, other_title, fmt) in zip(
            st.tabs([s[0] for s in specs_charts]), specs_charts):
        with tab:
            st.altair_chart(
                bar_chart(agg, col, other, title, other_title, fmt,
                          sort_mode, top_n),
                width="stretch")
            if other:
                st.caption(f"Numbers at bar ends are the **{title}**; the "
                           f"grey tick on each bar marks the **{other_title}**.")
            else:
                st.caption(f"Numbers at bar ends are the **{title}** — the "
                           "share of runs where the player did not die once.")

    st.caption(
        f"{len(agg):,} groups pass the threshold (≥ {min_runs} runs each). DPS "
        "is per-player overall damage ÷ run duration; deaths are per player "
        "per run, parsed from each report's death events. Median deaths are "
        "0 for most groups because ~75% of all parses have zero deaths — "
        "hover any bar for the share of deathless runs. Rows whose log lacked "
        "combatant info are counted under the most-used hero talent of their "
        "spec."
    )


if __name__ == "__main__":
    main()
