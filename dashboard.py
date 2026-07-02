#!/usr/bin/env python3
"""Streamlit dashboard for Midnight Season 1 Mythic+ performance data.

Run with:  streamlit run dashboard.py
Data:      data/mythic_runs.csv  (produced by scripts/fetch_data.py)
"""
import pathlib

import altair as alt
import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent
CSV_FILE = ROOT / "data" / "mythic_runs.csv"

ACCENT = "#2a78d6"  # single-hue magnitude encoding for the bar chart

st.set_page_config(
    page_title="Midnight S1 Mythic+ Dashboard",
    page_icon="⚔️",
    layout="wide",
)


@st.cache_data(show_spinner="Loading run data…")
def load_data() -> pd.DataFrame:
    try:
        df = pd.read_csv(CSV_FILE)
    except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df["key_level"] = df["key_level"].astype(int)
    df["deaths"] = df["deaths"].astype(int)
    for col in ("class", "spec", "hero_talent", "role", "region"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    df["started_at"] = pd.to_datetime(df["started_at"], errors="coerce")
    return df


def main() -> None:
    st.title("⚔️ Mythic+ Performance — Midnight Season 1")

    df = load_data()
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

        klo, khi = int(df["key_level"].min()), int(df["key_level"].max())
        if klo < khi:
            key_range = st.slider(
                "Key Level", klo, khi, (klo, khi),
                help="Runs outside this keystone range are excluded")
        else:
            key_range = (klo, khi)
            st.caption(f"Key Level: all runs are +{klo}")

        min_runs = st.slider(
            "Minimum Runs Threshold", 1, 50, 3,
            help="Hide Class/Spec/Hero Talent rows with fewer than this many runs")

        roles = st.multiselect(
            "Role", ["DPS", "Healer", "Tank"], default=[],
            help="Optional: limit to a role (empty = all)")

        region_opts = sorted(df["region"].unique())
        region_default = [r for r in ("US", "EU") if r in region_opts]
        regions_sel = st.multiselect(
            "Region", region_opts,
            default=region_default if region_default else [],
            help="Player region from the report itself (empty = all)")

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
    if roles:
        mask &= df["role"].isin(roles)
    if regions_sel:
        mask &= df["region"].isin(regions_sel)
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
    newest = view["started_at"].max()
    c4.metric("Newest run", newest.strftime("%Y-%m-%d") if pd.notna(newest) else "—")

    # ------------------------------------------------------------------ aggregate
    agg = (
        view.groupby(["class", "spec", "hero_talent"])
        .agg(
            total_runs=("dps", "size"),
            avg_dps=("dps", "mean"),
            median_dps=("dps", "median"),
            avg_deaths=("deaths", "mean"),
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
        }),
        width="stretch",
        hide_index=True,
        column_config={
            "Total Runs": st.column_config.NumberColumn(format="localized"),
            "Average DPS": st.column_config.NumberColumn(format="localized"),
            "Median DPS": st.column_config.NumberColumn(format="localized"),
            "Average Deaths": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    # ------------------------------------------------------------------ chart
    CHART_MAX = 40
    st.subheader("Average DPS" + (f" — top {CHART_MAX} groups" if len(agg) > CHART_MAX else ""))
    chart_df = agg.head(CHART_MAX).copy()
    chart_df["label"] = chart_df["spec"] + " " + chart_df["class"] + " — " + chart_df["hero_talent"]
    chart_df["avg_dps_r"] = chart_df["avg_dps"]
    chart_df["median_dps_r"] = chart_df["median_dps"]
    chart_df["avg_deaths_r"] = chart_df["avg_deaths"].round(2)

    bars = (
        alt.Chart(chart_df)
        .mark_bar(size=16, cornerRadiusEnd=4, color=ACCENT)
        .encode(
            x=alt.X("avg_dps_r:Q", title="Average DPS", axis=alt.Axis(format=",.0f")),
            y=alt.Y("label:N", sort="-x", title=None,
                    axis=alt.Axis(labelLimit=320)),
            tooltip=[
                alt.Tooltip("class:N", title="Class"),
                alt.Tooltip("spec:N", title="Spec"),
                alt.Tooltip("hero_talent:N", title="Hero Talent"),
                alt.Tooltip("total_runs:Q", title="Total Runs", format=","),
                alt.Tooltip("avg_dps_r:Q", title="Average DPS", format=","),
                alt.Tooltip("median_dps_r:Q", title="Median DPS", format=","),
                alt.Tooltip("avg_deaths_r:Q", title="Average Deaths"),
            ],
        )
        .properties(height=max(28 * len(chart_df), 120))
    )
    st.altair_chart(bars, width="stretch")

    st.caption(
        f"{len(agg):,} groups shown (≥ {min_runs} runs each). DPS is per-player "
        "overall damage ÷ run duration; deaths are per player per run, parsed "
        "from each report's death events."
    )


if __name__ == "__main__":
    main()
