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

ACCENT = "#2a78d6"          # single-hue magnitude encoding for bar charts
CHART_MAX = 40              # bars per chart
# a region is excluded from the default Region filter when more than this
# share of its rows lack combatant info (i.e. hero talent was unresolvable)
REGION_MISSING_CUTOFF = 0.25

# weekly M+ reset boundaries as (weekday Mon=0, hour UTC) per region
RESET_RULES = {"US": (1, 15), "EU": (2, 4)}   # US: Tue 15:00, EU: Wed 04:00
RESET_DEFAULT = (2, 22)                       # KR/TW/CN/unknown: ~Wed 22:00

# attack style by (class, spec); healers are bucketed by how they attack
# (Holy Paladin / Mistweaver melee, the rest ranged)
MELEE_SPECS = {
    ("DeathKnight", "Blood"), ("DeathKnight", "Frost"), ("DeathKnight", "Unholy"),
    ("DemonHunter", "Devourer"), ("DemonHunter", "Havoc"), ("DemonHunter", "Vengeance"),
    ("Druid", "Feral"), ("Druid", "Guardian"),
    ("Hunter", "Survival"),
    ("Monk", "Brewmaster"), ("Monk", "Mistweaver"), ("Monk", "Windwalker"),
    ("Paladin", "Holy"), ("Paladin", "Protection"), ("Paladin", "Retribution"),
    ("Rogue", "Assassination"), ("Rogue", "Outlaw"), ("Rogue", "Subtlety"),
    ("Shaman", "Enhancement"),
    ("Warrior", "Arms"), ("Warrior", "Fury"), ("Warrior", "Protection"),
}
RANGED_SPECS = {
    ("Druid", "Balance"), ("Druid", "Restoration"),
    ("Evoker", "Augmentation"), ("Evoker", "Devastation"), ("Evoker", "Preservation"),
    ("Hunter", "BeastMastery"), ("Hunter", "Marksmanship"),
    ("Mage", "Arcane"), ("Mage", "Fire"), ("Mage", "Frost"),
    ("Priest", "Discipline"), ("Priest", "Holy"), ("Priest", "Shadow"),
    ("Shaman", "Elemental"), ("Shaman", "Restoration"),
    ("Warlock", "Affliction"), ("Warlock", "Demonology"), ("Warlock", "Destruction"),
}


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
def load_data():
    """Returns (dataframe, per-region missing-combatant-info share)."""
    try:
        df = pd.read_csv(CSV_FILE)
    except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame(), {}
    if df.empty:
        return pd.DataFrame(), {}
    df["key_level"] = df["key_level"].astype(int)
    df["deaths"] = df["deaths"].astype(int)
    for col in ("class", "spec", "hero_talent", "role", "region"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    df["started_at"] = _parse_started_at(df["started_at"])
    df["attack_type"] = [
        "Melee" if k in MELEE_SPECS else "Ranged" if k in RANGED_SPECS else "Unknown"
        for k in zip(df["class"], df["spec"])
    ]
    # measured BEFORE merging, so the region-quality rule sees the truth
    region_missing = df.groupby("region")["hero_talent"] \
        .agg(lambda s: (s == "Unknown").mean()).to_dict()
    df = _merge_unknown_heroes(df)
    return df, region_missing


def _theme_is_dark() -> bool:
    """Resolve the active theme from config (deterministic at import time,
    unlike st.context which needs a live session)."""
    try:
        return (st.get_option("theme.base") or "dark") == "dark"
    except Exception:
        return True  # matches the shipped .streamlit/config.toml


DARK = _theme_is_dark()
SURFACE = "#15171C" if DARK else "#fcfcfb"
TEXT_INK = "#E8E6E3" if DARK else "#1f1e1d"
TICK_COLOR = "#c9c7c2" if DARK else "#52514e"
GOLD = "#F8B700"
MUTED = "#8f8d88" if DARK else "#6b6963"
CARD_BG = "#1F232B" if DARK else "#f4f2ee"
GRID_COLOR = "rgba(255,255,255,.07)" if DARK else "rgba(0,0,0,.08)"
CARD_BORDER = "rgba(248,183,0,.16)" if DARK else "rgba(140,110,0,.25)"

# Blizzard's canonical class colors (designed for dark surfaces); Priest's
# white is dimmed just enough per theme to stay visible as a bar
CLASS_COLORS = {
    "DeathKnight": "#C41E3A", "DemonHunter": "#A330C9", "Druid": "#FF7C0A",
    "Evoker": "#33937F", "Hunter": "#AAD372", "Mage": "#3FC7EB",
    "Monk": "#00FF98", "Paladin": "#F48CBA",
    "Priest": "#DCDCE2" if DARK else "#B6B6BE",
    "Rogue": "#FFF468", "Shaman": "#0070DD", "Warlock": "#8788EE",
    "Warrior": "#C69B6D", "Unknown": "#999999",
}


def _mix(c1: str, c2: str, t: float) -> str:
    """Linear blend of two hex colors, t=0 -> c1, t=1 -> c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def shade_class_color(cls: str, t: float) -> str:
    """Class hue kept, intensity carries a second measure: on the dark theme
    dim -> bright as t goes 0 -> 1 (on light, pale -> deep)."""
    base = CLASS_COLORS.get(cls, "#999999")
    if DARK:
        lo, hi = _mix(base, SURFACE, 0.60), _mix(base, "#ffffff", 0.20)
    else:
        lo, hi = _mix(base, "#ffffff", 0.60), _mix(base, "#000000", 0.30)
    return _mix(lo, hi, t)


def _ink_for(color: str) -> str:
    """Dark or white text ink depending on a bar color's brightness."""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return "#1f1e1d" if (0.2126 * r + 0.7152 * g + 0.0722 * b) > 0.45 else "white"

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
              sort_mode: str, top_n: int,
              inlay_col: str | None = None, shade_col: str | None = None):
    """Horizontal bars in WoW class colors, with the value printed at each
    bar end and, when `other_col` is given (same units only), a neutral tick
    overlaying that counterpart metric. `shade_col` modulates each bar's
    intensity (dim -> bright) by a complementary measure and `inlay_col`
    prints its exact numbers inside the bar — DPS charts carry the death
    story and death charts carry the DPS story, without losing the class."""
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

    # Do NOT let Vega sort: channel- and field-based sorts on layered specs
    # have version-dependent fallbacks (alphabetical label order — which,
    # with rank prefixes, reads as 1, 10, 11, ... 2, 20). Pre-sorting the
    # dataframe and passing sort=None (data order) renders identically on
    # every Vega build.
    if sort_mode == "Name (A → Z)":
        top = top.sort_values("name_key")
    elif sort_mode == "Value (low → high)":
        top = top.sort_values(value_col, ascending=True)
    # else: already sorted by value_col descending (rank order)
    # labelOverlap must be pinned off: some Vega builds default to dropping
    # every other band label, which reads as "ranks skip numbers"
    y = alt.Y("label:N", sort=None, title=None,
              axis=alt.Axis(labelLimit=320, labelOverlap=False,
                            labelFontSize=11.5))
    # headroom so end-of-bar labels never clip and out-lying ticks stay visible
    xmax = float(top[cols].max().max()) * 1.18
    xmin = min(0.0, float(top[cols].min().min()) * 1.18)
    x_scale = alt.Scale(domain=[xmin, xmax if xmax > 0 else 1], nice=False)

    # identity + magnitude in one mark: class hue names the class (so does
    # the axis label), intensity carries the complementary measure
    if shade_col:
        smin, smax = float(top[shade_col].min()), float(top[shade_col].max())
        rng = (smax - smin) or 1.0
        tvals = (top[shade_col] - smin) / rng
    else:
        tvals = pd.Series(0.72, index=top.index)
    top["bar_color"] = [shade_class_color(c, float(t))
                        for c, t in zip(top["class"], tvals)]
    top["ink"] = top["bar_color"].map(_ink_for)

    base = alt.Chart(top)
    bars = base.mark_bar(size=16, cornerRadiusEnd=4).encode(
        x=alt.X(f"{value_col}:Q", title=title, scale=x_scale,
                axis=alt.Axis(format=fmt)),
        y=y, tooltip=TOOLTIPS,
        color=alt.Color("bar_color:N", scale=None, legend=None),
    )
    labels = base.mark_text(align="left", dx=7, color=TEXT_INK,
                            fontSize=12.5, fontWeight="bold").encode(
        x=alt.X("label_x:Q", scale=x_scale), y=y, text="value_text:N",
    )
    layers = bars + labels
    if inlay_col:
        layers += base.mark_text(align="left", fontSize=10.5,
                                 fontWeight=500).encode(
            x=alt.value(8),  # pinned just inside the bar's left edge
            y=y, text=f"{inlay_col}:N", tooltip=TOOLTIPS,
            color=alt.Color("ink:N", scale=None),
        )
    if other_col:
        layers += base.mark_tick(color=TICK_COLOR, thickness=2.5, size=15).encode(
            x=alt.X(f"{other_col}:Q", scale=x_scale,
                    title=f"{title} (tick: {other_title})"),
            y=y, tooltip=TOOLTIPS,
        )
    return (layers.properties(height=max(32 * len(top), 120))
            .configure_view(strokeWidth=0)
            .configure_axis(domainOpacity=0, tickOpacity=0,
                            gridColor=GRID_COLOR,
                            labelColor=MUTED, titleColor=MUTED))


def _inject_css() -> None:
    st.markdown(f"""<style>
    .block-container {{padding-top: 2.4rem; padding-bottom: 4rem; max-width: 1280px;}}
    .hero-title {{font-family: 'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
                  font-size: 2.5rem; font-weight: 650; letter-spacing: .02em;
                  margin: 0; line-height: 1.15;}}
    .hero-title .gold {{color: {GOLD};}}
    .hero-sub {{color: {MUTED}; font-size: .95rem; letter-spacing: .05em;
                margin: .4rem 0 0 2px;}}
    .hero-rule {{height: 2px; border: 0; margin: 16px 0 6px;
                 background: linear-gradient(90deg, {GOLD}88, transparent 65%);}}
    [data-testid="stMetric"] {{background: {CARD_BG};
        border: 1px solid {CARD_BORDER}; border-radius: 12px;
        padding: .85rem 1.1rem;}}
    [data-testid="stMetricLabel"] p {{text-transform: uppercase;
        letter-spacing: .14em; font-size: .7rem; color: {MUTED};}}
    [data-testid="stMetricValue"] {{font-weight: 650; font-size: 1.7rem;}}
    .sec {{display: flex; align-items: baseline; gap: .8rem;
           margin: 2.2rem 0 .3rem; padding-bottom: .45rem;
           border-bottom: 1px solid {GRID_COLOR};}}
    .sec-label {{text-transform: uppercase; letter-spacing: .18em;
                 font-size: .82rem; font-weight: 700; color: {GOLD};}}
    .sec-sub {{color: {MUTED}; font-size: .8rem;}}
    .side-label {{text-transform: uppercase; letter-spacing: .16em;
                  font-size: .7rem; font-weight: 700; color: {GOLD};
                  margin: 1.05rem 0 .15rem;}}
    [data-testid="stSidebar"] {{border-right: 1px solid {GRID_COLOR};}}
    </style>""", unsafe_allow_html=True)


def section(label: str, sub: str = "") -> None:
    st.markdown(
        f'<div class="sec"><span class="sec-label">{label}</span>'
        + (f'<span class="sec-sub">{sub}</span>' if sub else "")
        + "</div>",
        unsafe_allow_html=True)


def side_label(label: str) -> None:
    st.markdown(f'<p class="side-label">{label}</p>', unsafe_allow_html=True)


def main() -> None:
    _inject_css()
    st.markdown(
        '<div class="hero-title">⚔️ Mythic+ <span class="gold">Performance</span></div>'
        '<div class="hero-sub">Midnight Season 1 &nbsp;·&nbsp; Keystones +12 – 25 '
        '&nbsp;·&nbsp; built from Warcraft Logs fight rankings</div>'
        '<div class="hero-rule"></div>',
        unsafe_allow_html=True)

    df, region_missing = load_data()
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
        if st.button("🔄 Refresh Data", width="stretch",
                     help="Clear the cache and reload the CSV from disk"):
            load_data.clear()
            st.rerun()

        side_label("Who — class, spec & talents")
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

        roles = st.multiselect(
            "Role", ["DPS", "Healer", "Tank"], default=[],
            help="Optional: limit to a role (empty = all)")

        atk1, atk2 = st.columns(2)
        melee_cb = atk1.checkbox(
            "Melee", value=False,
            help="Attack style — both or neither checked = no filter")
        ranged_cb = atk2.checkbox("Ranged", value=False)

        side_label("Where & when — content, timeframe")
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

        side_label("Quality — sample size, regions")
        min_runs = st.slider(
            "Minimum Runs Threshold", 1, 500, 3,
            help="Hide Class/Spec/Hero Talent rows with fewer than this many runs")

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
    if melee_cb != ranged_cb:
        mask &= df["attack_type"] == ("Melee" if melee_cb else "Ranged")
    if regions_sel:
        mask &= df["region"].isin(regions_sel)
    if reset_period is not None:
        mask &= df["_resets_ago"] == reset_period
    view = df[mask]

    if view.empty:
        st.warning("No runs match the current filters.")
        st.stop()

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

    # ------------------------------------------------------------------ KPI band
    n_runs = view[["report_code", "fight_id"]].drop_duplicates().shape[0]
    oldest, newest = view["started_at"].min(), view["started_at"].max()
    dates = (f"{oldest:%b %d} – {newest:%b %d}"
             if pd.notna(oldest) and pd.notna(newest) else "—")
    for col, (label, value) in zip(st.columns(4), [
        ("Dungeon runs", f"{n_runs:,}"),
        ("Player parses", f"{len(view):,}"),
        ("Groups compared", f"{len(agg):,}"),
        ("Run dates", dates),
    ]):
        col.metric(label, value)

    # ------------------------------------------------------------------ charts
    section("Overview", "top groups per metric — hover any bar for the full story")
    ctl1, _, ctl2 = st.columns([5, 1, 6])
    sort_mode = ctl1.selectbox(
        "Sort bars by",
        ["Value (high → low)", "Value (low → high)", "Name (A → Z)"],
        help="Bars are always the top groups by the tab's metric; this "
             "controls their display order")
    if len(agg) <= 5:
        # a slider with default below its minimum raises; with this few
        # groups there is nothing to trim anyway
        top_n = len(agg)
        ctl2.caption(f"Showing all {len(agg)} groups")
    else:
        top_n = ctl2.slider(
            "Groups shown", 5, min(len(agg), 100), min(CHART_MAX, len(agg)),
            help="How many of the top groups (by the tab's metric) to draw")

    glow = "brighter" if DARK else "deeper"
    specs_charts = [
        # title, value, tick, tick title, fmt, inlay, shade
        ("Average DPS", "avg_dps", "median_dps", "Median DPS", ",.0f",
         "deaths_text", "deathless"),
        ("Median DPS", "median_dps", "avg_dps", "Average DPS", ",.0f",
         "deaths_text", "deathless"),
        ("Mean − Median DPS", "dps_diff", None, "", "+,.0f", None, None),
        ("Average Deaths", "avg_deaths", "median_deaths", "Median Deaths", ".2f",
         "dps_text", "avg_dps"),
        ("Deathless Runs %", "deathless", None, "", ".1f", "dps_text", "avg_dps"),
    ]
    for tab, (title, col, other, other_title, fmt, inlay, shade) in zip(
            st.tabs([s[0] for s in specs_charts]), specs_charts):
        with tab:
            st.altair_chart(
                bar_chart(agg, col, other, title, other_title, fmt,
                          sort_mode, top_n, inlay_col=inlay, shade_col=shade),
                width="stretch")
            tick_part = (f"; grey tick: **{other_title}**" if other else "")
            if inlay == "deaths_text":
                st.caption(f"Bold number at each bar end: **{title}**"
                           f"{tick_part}. Bars wear their class color — the "
                           f"{glow} the bar, the higher its share of "
                           "deathless runs; exact deaths stats are printed "
                           "inside each bar.")
            elif inlay == "dps_text":
                st.caption(f"Bold number at each bar end: **{title}**"
                           f"{tick_part}. Bars wear their class color — the "
                           f"{glow} the bar, the higher its average DPS; "
                           "exact DPS numbers are printed inside each bar.")
            else:
                st.caption("Bars wear their class color. Positive: a minority "
                           "of very high parses pulls the average above the "
                           "typical run. Negative: a low tail drags the "
                           "average below it. Larger magnitude = less "
                           "consistent performance.")

    # ------------------------------------------------------------------ table
    section("Breakdown",
            "every Class / Spec" + ("" if merge_heroes else " / Hero Talent")
            + f" group with ≥ {min_runs} runs — click a column header to sort")
    st.dataframe(
        (agg.drop(columns=["hero_talent"]) if merge_heroes else agg)
        .drop(columns=["dps_text", "deaths_text"], errors="ignore")
        .rename(columns={
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

    st.caption(
        "DPS is per-player overall damage ÷ run duration; deaths are per "
        "player per run, parsed from each report's death events. Median "
        "deaths are 0 for most groups because ~75% of all parses have zero "
        "deaths. Rows whose log lacked combatant info are counted under the "
        "most-used hero talent of their spec. Data: Warcraft Logs fight "
        "rankings (top ~1,000 runs per dungeon × key level), Midnight "
        "Season 1, keys 12–25+."
    )


if __name__ == "__main__":
    main()
