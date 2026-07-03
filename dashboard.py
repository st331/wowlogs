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
import numpy as np
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

# weekly M+ reset boundaries as (weekday Mon=0, hour UTC) per region
RESET_RULES = {"US": (1, 15), "EU": (2, 4)}   # US: Tue 15:00, EU: Wed 04:00
RESET_DEFAULT = (2, 22)                       # KR/TW/CN/unknown: ~Wed 22:00


def latest_reset(now: pd.Timestamp, weekday: int, hour: int) -> pd.Timestamp:
    """Most recent weekly reset boundary at or before `now` (UTC, naive)."""
    b = now.normalize() + pd.Timedelta(hours=hour)
    b -= pd.Timedelta(days=(now.weekday() - weekday) % 7)
    if b > now:
        b -= pd.Timedelta(days=7)
    return b


def resets_ago(df: pd.DataFrame) -> pd.Series:
    """0 = current reset period, 1 = previous, ...; -1 = undated row.
    Each run is bucketed against ITS region's own reset boundary."""
    now = pd.Timestamp.now()
    bounds = {r: latest_reset(now, *RESET_RULES.get(r, RESET_DEFAULT))
              for r in df["region"].unique()}
    secs = (df["region"].map(bounds) - df["started_at"]).dt.total_seconds()
    out = pd.Series(np.where(secs <= 0, 0, secs // (7 * 86400) + 1),
                    index=df.index)
    out[df["started_at"].isna()] = -1
    return out.astype(int)

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
    alt.Tooltip("dps_diff:Q", title="Mean − Median DPS", format="+,"),
    alt.Tooltip("avg_deaths:Q", title="Average Deaths", format=".2f"),
    alt.Tooltip("median_deaths:Q", title="Median Deaths", format=".1f"),
    alt.Tooltip("deathless:Q", title="Deathless runs %", format=".1f"),
]


def bar_chart(data: pd.DataFrame, value_col: str, other_col: str | None,
              title: str, other_title: str, fmt: str,
              sort_mode: str, top_n: int, diverging: bool = False,
              gradient: tuple | None = None, inlay_col: str | None = None):
    """Horizontal bars of `value_col` with the value printed at each bar end
    and, when `other_col` is given (same units only), a neutral tick
    overlaying that counterpart metric. `diverging` colors negative bars
    with the opposite pole for signed metrics. `gradient` = (column,
    legend title, legend format) paints a second measure onto the bars as a
    light->dark sequential ramp; `inlay_col` prints that measure's exact
    numbers inside each bar — so DPS charts carry the death story and death
    charts carry the DPS story."""
    top = data.sort_values(value_col, ascending=False).head(top_n).copy()
    top["name_key"] = top["class"] + " " + top["spec"] + " " + top["hero_talent"]
    top["rank"] = range(1, len(top) + 1)  # rank by this graph's metric, best first
    top["label"] = (top["rank"].astype(str) + ". "
                    + top["class"] + " " + top["spec"]
                    + top["hero_talent"].map(
                        lambda h: "" if h in ("", "(all)") else f" — {h}"))
    top["value_text"] = top[value_col].map(lambda v: format(v, fmt))
    top["deaths_text"] = (top["avg_deaths"].map("{:.2f} avg deaths".format)
                          + "  ·  "
                          + top["deathless"].map("{:.0f}% deathless".format))
    top["dps_text"] = (top["avg_dps"].map("{:,.0f} avg".format)
                       + "  ·  "
                       + top["median_dps"].map("{:,.0f} median DPS".format))
    # anchor the printed value past BOTH the bar and the tick so they never
    # collide; never left of zero so negative bars keep their label readable
    cols = [value_col] + ([other_col] if other_col else [])
    top["label_x"] = top[cols].max(axis=1).clip(lower=0)

    # sort must reference the metric FIELD, not the layer's x channel: layers
    # whose x is a pixel value (the deaths inlay) can't resolve '-x' and the
    # whole layered spec silently collapses to zero height
    if sort_mode == "Name (A → Z)":
        y_sort = alt.EncodingSortField(field="name_key", op="min", order="ascending")
    elif sort_mode == "Value (low → high)":
        y_sort = alt.EncodingSortField(field=value_col, op="max", order="ascending")
    else:
        y_sort = alt.EncodingSortField(field=value_col, op="max", order="descending")
    y = alt.Y("label:N", sort=y_sort, title=None, axis=alt.Axis(labelLimit=320))
    # headroom so end-of-bar labels never clip and out-lying ticks stay visible
    xmax = float(top[cols].max().max()) * 1.18
    xmin = min(0.0, float(top[cols].min().min()) * 1.18)
    x_scale = alt.Scale(domain=[xmin, xmax if xmax > 0 else 1], nice=False)

    base = alt.Chart(top)
    bars = base.mark_bar(size=16, cornerRadiusEnd=4, color=ACCENT).encode(
        x=alt.X(f"{value_col}:Q", title=title, scale=x_scale,
                axis=alt.Axis(format=fmt)),
        y=y, tooltip=TOOLTIPS,
    )
    if diverging:
        bars = bars.encode(color=alt.condition(
            alt.datum[value_col] < 0, alt.value("#e34948"), alt.value(ACCENT)))
    ink_flip = None
    grad_col = None
    if gradient:
        # a second measure painted onto the bars: one hue, light -> dark
        # (sequential blue, palette steps 200 -> 700), darker = more of it
        grad_col, grad_title, grad_fmt = gradient
        dmin, dmax = float(top[grad_col].min()), float(top[grad_col].max())
        if dmax <= dmin:
            dmin, dmax = dmin - 1, dmax + 1
        bars = bars.encode(color=alt.Color(
            f"{grad_col}:Q",
            scale=alt.Scale(domain=[dmin, dmax], range=["#9ec5f4", "#0d366b"]),
            legend=alt.Legend(title=grad_title, orient="top",
                              gradientLength=220, format=grad_fmt),
        ))
        # inlay ink flips to dark on the light end of the gradient
        ink_flip = (dmin + dmax) / 2
    labels = base.mark_text(align="left", dx=7, color="#1f1e1d",
                            fontSize=12.5, fontWeight="bold").encode(
        x=alt.X("label_x:Q", scale=x_scale), y=y, text="value_text:N",
    )
    layers = bars + labels
    if inlay_col and grad_col:
        layers += base.mark_text(align="left", fontSize=10.5,
                                 fontWeight=500).encode(
            x=alt.value(8),  # pinned just inside the bar's left edge
            y=y, text=f"{inlay_col}:N", tooltip=TOOLTIPS,
            color=alt.condition(alt.datum[grad_col] < ink_flip,
                                alt.value("#1f1e1d"), alt.value("white")),
        )
    if other_col:
        layers += base.mark_tick(color=TICK_COLOR, thickness=2.5, size=15).encode(
            x=alt.X(f"{other_col}:Q", scale=x_scale,
                    title=f"{title} (tick: {other_title})"),
            y=y, tooltip=TOOLTIPS,
        )
    return layers.properties(height=max(30 * len(top), 120))


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

        merge_heroes = st.checkbox(
            "Merge hero talents into spec", value=False,
            help="Group results by Class/Spec only, ignoring hero talents "
                 "entirely (disables the Hero Talent filter)")
        heroes = [] if merge_heroes else st.multiselect(
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

        # ---- reset period (runs bucketed by their region's weekly reset) ----
        df["_resets_ago"] = resets_ago(df)
        us_b0 = latest_reset(pd.Timestamp.now(), *RESET_RULES["US"])
        period_options = {"All data": None}
        for n, name in ((0, "This reset"), (1, "Last reset"), (2, "Two resets ago")):
            if (df["_resets_ago"] == n).any():
                lo = us_b0 - pd.Timedelta(days=7 * n)
                span = (f"since {lo:%b %d}" if n == 0
                        else f"{lo:%b %d} – {lo + pd.Timedelta(days=7):%b %d}")
                period_options[f"{name} ({span})"] = n
        reset_sel = st.radio(
            "Reset period", list(period_options),
            help="Buckets runs by the weekly M+ reset of the run's own region "
                 "(US: Tue 15:00 UTC, EU: Wed 04:00 UTC, Asia: ~Wed 22:00 UTC); "
                 "dates shown use the US boundary")
        reset_period = period_options[reset_sel]

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
    if reset_period is not None:
        mask &= df["_resets_ago"] == reset_period
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
    group_cols = ["class", "spec"] if merge_heroes else ["class", "spec", "hero_talent"]
    agg = (
        view.groupby(group_cols)
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
    if merge_heroes:
        agg["hero_talent"] = "(all)"
    agg = agg[agg["total_runs"] >= min_runs]
    agg["avg_dps"] = agg["avg_dps"].round(0).astype(int)
    agg["median_dps"] = agg["median_dps"].round(0).astype(int)
    agg["dps_diff"] = agg["avg_dps"] - agg["median_dps"]
    agg = agg.sort_values("avg_dps", ascending=False).reset_index(drop=True)

    if agg.empty:
        st.warning(
            "Every group fell below the minimum-runs threshold "
            f"({min_runs}). Lower the slider to see sparser combinations."
        )
        st.stop()

    st.subheader("Performance by Class / Spec" +
                 ("" if merge_heroes else " / Hero Talent"))
    st.dataframe(
        (agg.drop(columns=["hero_talent"]) if merge_heroes else agg).rename(columns={
            "class": "Class", "spec": "Spec", "hero_talent": "Hero Talent",
            "total_runs": "Total Runs", "avg_dps": "Average DPS",
            "median_dps": "Median DPS", "dps_diff": "Mean − Median DPS",
            "avg_deaths": "Average Deaths",
            "median_deaths": "Median Deaths", "deathless": "Deathless %",
        }),
        width="stretch",
        hide_index=True,
        column_config={
            "Total Runs": st.column_config.NumberColumn(format="localized"),
            "Average DPS": st.column_config.NumberColumn(format="localized"),
            "Median DPS": st.column_config.NumberColumn(format="localized"),
            "Mean − Median DPS": st.column_config.NumberColumn(format="localized"),
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

    DEATH_GRAD = ("deathless", "Deathless runs %", ".0f")
    DPS_GRAD = ("avg_dps", "Average DPS", "~s")
    specs_charts = [
        # title, value, tick, tick title, fmt, diverging, gradient, inlay
        ("Average DPS", "avg_dps", "median_dps", "Median DPS", ",.0f",
         False, DEATH_GRAD, "deaths_text"),
        ("Median DPS", "median_dps", "avg_dps", "Average DPS", ",.0f",
         False, DEATH_GRAD, "deaths_text"),
        ("Mean − Median DPS", "dps_diff", None, "", "+,.0f",
         True, None, None),
        ("Average Deaths", "avg_deaths", "median_deaths", "Median Deaths", ".2f",
         False, DPS_GRAD, "dps_text"),
        ("Deathless Runs %", "deathless", None, "", ".1f",
         False, DPS_GRAD, "dps_text"),
    ]
    for tab, (title, col, other, other_title, fmt, div, grad, inlay) in zip(
            st.tabs([s[0] for s in specs_charts]), specs_charts):
        with tab:
            st.altair_chart(
                bar_chart(agg, col, other, title, other_title, fmt,
                          sort_mode, top_n, diverging=div,
                          gradient=grad, inlay_col=inlay),
                width="stretch")
            tick_part = (f"; grey tick: **{other_title}**" if other else "")
            if grad is DEATH_GRAD:
                st.caption(f"Bold number at each bar end: **{title}**"
                           f"{tick_part}. Survivability is painted onto each "
                           "bar: darker blue = higher share of deathless runs "
                           "(see legend), with the exact deaths stats printed "
                           "inside the bar.")
            elif grad is DPS_GRAD:
                st.caption(f"Bold number at each bar end: **{title}**"
                           f"{tick_part}. Damage is painted onto each bar: "
                           "darker blue = higher average DPS (see legend), "
                           "with the exact DPS numbers printed inside the bar.")
            elif col == "dps_diff":
                st.caption("Positive (blue): a minority of very high parses "
                           "pulls the average above the typical run. Negative "
                           "(red): a low tail drags the average below it. "
                           "Larger magnitude = less consistent performance.")

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
