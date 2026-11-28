#!/usr/bin/env python3
"""The page's static contract: exact strings scripts and design decisions pin in
site/index.html (and a few in the builder), so a later edit that quietly drops a
guard, a label or an honesty sentence fails here first.

Until 2026-09-30 these pins lived at the end of test_procs.py, the Lightspire
trinket Lab's test; that feature is gone (owner: "remove the lightspire trinket
labs feature and stop any data collection for it") and the pins moved here.
Run: python3 scripts/test_site_contract.py
"""
import json
import pathlib
import re as _re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# --- static client contract -------------------------------------------------------
html = (ROOT / "site" / "index.html").read_text()
assert "Sampled payload:" in html and '"sample": dict(SAMPLE_INFO)' in (ROOT / "scripts" / "build_site_data.py").read_text(), "never a silent sample"
# 2026-09-08 fleet S1-S5: what a sidecar rung withheld is printed, never silent
assert 'id="frame-dead"' in html and "function windowNote(win,idx)" in html and "enchants not published" in html, "sidecar notices"
# 2026-09-09: the chart selects with the sort and then cuts; bucket labels come from the
# rows the bucket holds; the pooled fold names its ranked-11+ tail; every cap says N of M
assert "let view=pool.slice(0,CHART_MAX)" in html and "CHART_CUT=cutWord" in html, "chart cuts AFTER the sort"
assert "function bucketLo(w)" in html and "usB0-7*w" not in html.split("function bucketLo")[1][:4000].split("function renderTrend")[0].replace("return s?s.lo:usB0-7*w","").replace("return s?s.hi:usB0-7*w+7",""), "week labels read bucketSpan, not the US bound"
assert "function resetNote()" in html and "no runs there in this reset yet" in html, "per-region reset straddle note"
assert "more listed '+lc" in html and "const E=new Set(M.ent.map(x=>x.k))" in html, "pooled fold names its tail"
# 2026-09-30 (owner: "how many people have trinket 1 AND trinket 2 equipped, versus just what
# percentage have trinket 1 and what percentage have trinket 2"): the pooled fold-out's By-pair
# reading — a partition of the gear-known rows, its own sortable table, every residual a named row
assert 'let screenPoolView="each";' in html and html.index('screenPoolView="each";', html.index("function resetScreenPerSpec(")) < html.index("function setScreenSpec("), "pair reading is a per-spec reading, reset with the fold"
assert 'id="cs-poolview"' in html and 'data-pv="each"' in html and 'data-pv="pair"' in html and '#cs-poolview button[data-pv]' in html, "By trinket | By pair control, wired"
assert ">By pair</button>" in html and ">By '+lc+'</button>" in html, "seg labels name the unit"
assert "function csPairModel(M)" in html and 'const csPairKey=(a,b)=>a<b?a+"\\u0000"+b:b+"\\u0000"+a;' in html, "pair key is order-free and NUL-joined"
assert "const CS_PAIR_ROWS=10;" in html and ".slice(0,CS_PAIR_ROWS)" in html and html.index(".slice(0,CS_PAIR_ROWS)") < html.index('sortState("cs:pair"'), "pair cap applied BEFORE the sort; its complement is a rendered row"
assert 'data-sid="cs:pair"' in html and 'sortState("cs:pair","both",cols)' in html and "const csPairVal=(r,k)=>r.pin?null" in html, "pair table sorts under its own registry id; pinned rows outside the sort"
assert '["both","Both",""' in html and '["each","Each",""' in html and '["pair","Pair","txt"' in html, "pair columns: Pair · Both · Each · n"
assert "' more pair'" in html and " below the n≥'+CS_PAIR_MIN+' floor</span>" in html and "other / none in both slots</span>" in html and '" in both slots":"other / none"' in html, "every residual is a named row; A + other / none and same-item are ranked classes"
# 2026-09-30 (owner: "reduce the minimum n to 1. it is still useful to see if even one person is using a trinket pair."):
# the pair floor is 1, members come from the UNFILTERED tally, the By-trinket face keeps n>=3
assert "const CS_PAIR_MIN=1;" in html and "if(o.c<CS_PAIR_MIN||!a||(o.b&&!b))" in html and "const src=M.all||M.ent;" in html and "return {ent, all, rowKeys, rowRaw, n:d.gearIdx.length, ok:sa>=0&&sb>=0};" in html and "if(c<CS_ENTRY_MIN) continue;                   // same floor as the per-slot fold" in html, "pair floor 1 over the unfiltered tally; By-trinket floor unchanged"
assert "with #1 " in html and "wear both the most used and the 2nd most used " in html and "const pairOf=p=>PAIRS[p]||(PAIRS[p]=csPairModel(poolOf(p)));" in html and "PAIRS[screenFold]" in html, "rank-2 pooled tile carries the #1+#2 pair share with its denominator, from the one memoised model"
assert "or the pairs worn together" in html, "the pooled tile names its By-pair reading"
_pt = html[html.index("function csPairTR"):html.index("function csPairBodyHTML")]
assert "ilvl" not in _pt and "csFoldCols(" not in _pt, "no item level, no lean columns on a pair row"
assert ".cs-fold td.fi2{width:62px" in html and "#cs-fold .fh.pv .fht{min-width:0; flex:1 1 auto}" in html, "pair CSS: two-icon cell; the header wraps, never ellipsizes"
assert "ratings rounded to steps of" in html and "not published in this build (size ladder)" in html, "stats scale / withheld labels"
_b = (ROOT / "scripts" / "build_site_data.py").read_text()
assert '"caps": {"items": item_cap' in _b and '"stats_all": list(SIDECAR_STATS)' in _b and _b.count("_retention_header(df, ") >= 2, "sidecar headers carry caps/window/stats_all, the window from retention"
# 2026-09-14 retention: the build drops rows; the payload ships what it dropped and where it cut,
# every sidecar window is BOUND to the retention constant, and the sentence leads with the policy
assert '"retention": dict(RETENTION_INFO)' in _b and "SIDECAR_WINDOW_RESETS = RETENTION_RESETS" in _b and "BUILDS_WINDOW_RESETS = RETENTION_RESETS" in _b, "retention facts ship; sidecar windows bind to retention"
assert '"coverage": coverage' in (ROOT / "scripts" / "build_site_data.py").read_text() and 'persist_sweep_stats({"sweep.public_runs"' in (ROOT / "scripts" / "fetch_data.py").read_text(), "coverage facts flow fetch -> build; the payload carries them and the page prints none of it"
# 2026-09-14 retention (owner): the page buckets on the builder's anchor and has no control that reaches
# past the resets the policy keeps (two until 2026-11-28, three since). (It no longer STATES the window in a
# paragraph: owner, 2026-11-26.)
assert "D.retention" in html, "the page reads the payload's retention block"
assert '["Everything kept", ()=>[]]' in html and '["This reset", ()=>[0]]' in html and '["Last reset", ()=>[1]]' in html and '["2 resets ago", ()=>[2]]' in html, "the period presets: no filter, and one per kept reset"
# 2026-11-28 (owner: "start keeping 3 weeks of data (+3 days to accomodate resets)"): every reset the policy keeps
# can be selected on its own. A three-reset payload on the two-chip page held 47% of its rows in a reset that could
# only be read blended into "Everything kept".
import sys as _sys
_sys.path.insert(0, str(ROOT / "scripts"))
import retention as _ret
for _w in range(_ret.RETENTION_RESETS):
    assert ", ()=>[%d]],\n" % _w in html[html.index("const PRESETS=["):html.index("function presetWeeks")], f"the policy keeps {_ret.RETENTION_RESETS} resets and the page has no period chip for reset {_w}"
for gone in ('"All season"', '"Last month"', '"Prev month"', '"Last 2 months"', "Custom weeks", 'id="f-weeksA"', "whole season", "pulseSpark(", "wkBag", "Last vs prev month"):
    assert gone not in html, f"{gone!r} must not survive the retention window"
assert "if(w===OUTW) return false;" in html and "KEEP_BUCKETS=(Number.isFinite(+r)&&+r>0)?+r:2" in html, "rows past the window never pass, and the clamp fails closed on an old payload"
assert "const now=(aD&&!isNaN(+aD))?aD:wallNow;" in html and "Math.floor((wallNow-epoch)/36e5)" in html, "buckets on the builder's anchor (NaN-guarded); reset-age on the wall clock"
assert "function retPhrase()" in html, "the window is read from the payload"
assert "Compare needs two resets" in html, "compare refuses without a baseline inside the window"
assert "Avg Player Rating (season)" in html, "the one season-wide figure says so where it prints"
print("client      : sample banner")
# 2026-09-17 item-level range filter (owner: "add a filter for ilevel as well"): the same
# shape as the key range, applied in rowPass through baseMasks (so every surface that
# filters rows inherits it), printed in the scope line, the frame scope and a scope chip
# whenever narrowed, parked and restored by the Archon replica, hidden on a payload
# without rows.ilvl, and honest about the unknown rows a narrowed range excludes.
assert 'id="ilo"' in html and 'id="ihi"' in html and 'id="ilvl-fill"' in html and 'id="ilvl-v"' in html, "item-level dual slider"
assert "il:il?true:(ilvlNarrowed()||gearOn())" in html and "if(m.il){const v=R.ilvl[i]; if(v<m.ilo||v>m.ihi) return false;}" in html, "rowPass applies the range only when narrowed or under the Gear axis; unknown (0) fails a narrowed range"
assert html.count("else if(ilvlNarrowed()) p.push(ilvlText());") == 2 and 'if(gearOn()) p.push(ilvlAText()+" (cohort A)");' in html and 'if(gearOn()) p.push(withB?ilvlAText()+" vs "+ilvlBText():ilvlAText()+" (cohort A)");' in html and '$("frame-scope").textContent=frameScope(true);' in html, "printed in the scope line (cohort A) and the frame scope (A vs B) under the Gear axis"
assert 'scopeChip(box,ilvlText()' in html, "the scope chip names the item-level range"
assert "ilo:ILVL.min, ihi:ILVL.max," in html and "&& !ilvlNarrowed()" in html and "ilo:state.ilo, ihi:state.ihi," in html, "Archon parks, matches and snapshots the range"
assert "ibox.hidden=true;" in html, "hidden on a payload without rows.ilvl"
assert "'the key, '+(ILVL.has?'item-level, ':'')" in html, "the fixed-cohort warning names item level when the page has it"
assert "if(ILVL.has&&(DEF.ilo>ILVL.min||DEF.ihi<ILVL.max))" in html, "the trust-gate reference pool mirrors the item-level default"
assert '"ilvl": ilvl_arr' in _b, "the builder ships rows.ilvl"
print("client      : item-level range: dual slider under Key Level; rowPass gate; scope line + frame scope; the hint counts parses with no item level; Archon park/match/snap; hidden without rows.ilvl")

# Gear compare axis (owner, 2026-09-18: "compare on ... ilevel. I want to be able to see how
# classes scale with gear"): a third XOR axis next to Time and Skill. The same period and
# filters are aggregated twice, once per item-level cohort (A = the Item Level slider, B = a
# ghost twin in the sidebar), and joined on A's groups exactly like the Time axis. Turning it
# on splits an open slider at the median item level of the current selection (A at or above,
# B below) or keeps a narrowed A and gives B the complement; every surface that prints
# "period A/B" under Time prints the two item-level ranges under Gear; parses with no item
# level sit in neither cohort; Archon parks it and restores it; hidden without rows.ilvl.
assert 'data-a="gear" data-l="Gear" id="axis-gear-btn"' in html and 'id="axis-gear"' in html and 'id="axis-gear-chip"' in html, "Gear segment button + reserved axis sub-slot"
assert 'id="blockG"' in html and 'id="ilob"' in html and 'id="ihib"' in html and 'id="ilvlb-v"' in html and 'id="ilvlb-fill"' in html and 'id="gearquick"' in html, "B cohort panel: dual slider + quick chips"
assert "const gearOn=()=>state.gear&&!state.compare&&!state.skill&&!state.elite&&ILVL.has;" in html and "const twoSided=()=>state.compare||gearOn();" in html, "gearOn and twoSided predicates"
assert "const skillOn=()=>state.skill&&!state.compare&&!state.gear&&!state.elite;" in html and "if(on){state.skill=false; if(state.gear) gearOff();}" in html and "function gearOff()" in html and 'else if(a==="gear"){ if(!state.gear) setGear(true); }' in html, "strict XOR across the three axes"
assert "function gearIlvls()" in html and "function gearMedian()" in html and "const gearQuantile=f=>" in html and "function gearSplitApply(){" in html and "function setGear(on)" in html and "function gearOverlapNote()" in html and "function gearCensus()" in html, "gear engine"
assert "const m=baseMasks(); m.il=false;" in html and "const x=R.ilvl[i]; if(x>0) v.push(x);" in html and "state.ilo=med; state.ihi=ILVL.max; state.ilob=ILVL.min; state.ihib=med-1;" in html and "if(below){ state.ilob=ILVL.min; state.ihib=state.ilo-1; }" in html and "else { state.ilob=state.ihi+1; state.ihib=ILVL.max; }" in html and "if(gearPrevA&&state.ilo===gearPrevA.setLo&&state.ihi===gearPrevA.setHi){" in html and "ilo:il?il.lo:state.ilo, ihi:il?il.hi:state.ihi};" in html, "the arithmetic: median over the un-narrowed selection, known item levels only, lower median; split and complement cohorts adjacent and disjoint; B's bounds ride the mask; leaving restores A only while the axis's own range still stands"
assert "gearPrevA=null;   // a fresh activation" in html, "a fresh activation never inherits a restore memory"
assert "Gear compare needs item levels — this build carries none." in html and "cannot be split at its median item level" in html and "no parse in it sits below that" in html, "a refused gear compare says why"
assert "function aggregate(weeks,il){" in html and "const m=baseMasks(il), cut=periodCut(weeks);" in html and "aggregate(state.weeksA,{lo:state.ilob,hi:state.ihib})" in html, "B is period A aggregated over the B cohort"
assert "const cmpA=twoSided()||skillTab;" in html and "if(twoSided()) b=B?B.groups.get(key)||null:null;" in html and "b:(twoSided()&&FRAME_B)?(FRAME_B.groups.get(key)??null):null," in html and "const compare=twoSided()||skill;" in html and html.count("if(twoSided())") == 4, "every two-aggregation surface reads twoSided(), not state.compare: the join, the caption gate, the frame ctx and its three identity rows, the table"
assert "A and B overlap on ilvl" in html, "period note: an overlap of A and B is said"
assert 'scopeChip(box,"compare: gear vs "+ilvlBText(),"blockG")' in html and "'<b>A</b><br><span style=\"white-space:nowrap\">'+esc(ilvlAText())+'</span>'" in html, "scope chip and tooltip header name the ranges"
assert "(gearOn()?'no B':'new')" in html and 'gearOn()?" · item-level cohort A":""' in html, "no time words under the Gear axis"
assert 'state.gear=("gear" in st)?!!st.gear:false;' in html and 'if("ilob" in st){state.ilob=st.ilob; state.ihib=st.ihib;}' in html and "gear:state.gear, ilob:state.ilob, ihib:state.ihib," in html and "&& state.merge && !state.compare && !state.gear" in html and "function setGear(on){\n  if(state.elite) return;" in html, "Archon parks the Gear axis and restores it with both cohorts; the axis cannot be turned on inside the replica"
assert '$("axis-gear-btn").style.display=ILVL.has?"":"none";' in html and "state.gear=false; state.ilob=0; state.ihib=0;" in html and "const any=state.compare||state.skill||state.gear;" in html and 'gear?(band?"Item Level A":"Item Level split"):"Item Level"' in html and '$("axis-gear-chip").textContent="B: "+ilvlBText();' in html, "hidden without rows.ilvl; opens off; gain/loss sorts, the A label and the B chip follow the axis"
# review follow-up (2026-09-18): the split is tried before the other axis is switched off; the
# degenerate case is "nothing below the median"; the restore memory rides the Archon snapshot;
# leaving the axis always says where A stands; the selection-scoped unknown count; shared
# characters; Pulse names the cohort; compare columns say Parses; the B slider is grey.
assert "const open=!ilvlNarrowed();" in html and "if(open&&!gearSplitApply()){ syncAxisUI(); return; }" in html, "a refused split leaves Time/Skill on"
assert "return {med:v[Math.floor((v.length-1)/2)], lo:v[0]};" in html and "if(r==null||r.lo>=med){" in html, "refused when no parse sits below the median"
assert "gearPrev:gearPrevA," in html and 'gearPrevA=("gearPrev" in st)?st.gearPrev:null;' in html, "the restore memory travels with the Archon snapshot"
assert '" <b>Median item level "+gc.ilA+" in A vs "+gc.ilB+" in B · median key +"+gc.kA+" in A vs +"+gc.kB+" in B.</b>"' in html and '"% per item level</b>"' in html, "the census: each cohort's median item level and key, and the gain per item level"
assert '["⚡ Median split",' in html and '["⚡ Quartile split",' in html and '["⇄ Swap A and B",' in html and "gearTrack(hi,ILVL.max); state.ilo=hi; state.ihi=ILVL.max; state.ilob=ILVL.min; state.ihib=lo;" in html, "quick chips: median split, quartile split, swap"
assert 'mil:(g.il.sort((a,b)=>a-b),g.il[Math.floor((g.il.length-1)/2)]),' in html and "if(ILVL.has) g.il.push(R.ilvl[i]);" in html and 'h+=row("Median item level", f(r.a,"mil",' in html and 'h+=row("Median key", f(r.a,"mkey",' in html, "the tooltip prints the spec's own cohort centres under Gear"
assert "thinB:!!(b&&b.chars<effMinB)});" in html and '">thin B</span>' in html, "a B side under the gate is badged, never hidden"
assert 'scopeChip(box,"compare: gear vs "+ilvlBText(),"blockG")' in html, "the gear scope chip jumps to the B slider"
assert "function gearSharedCharsNote(A,B)" in html and "charSet:charSeen," in html, "shared characters are counted and said in the note"
assert 'cols.push(["a_n","Parses A"],["b_n","Parses B"],' in html and 'cols.push(["a_n","Parses"],' in html and '"Runs A"' not in html, "compare columns carry parses and say so"
assert "#blockG .dual .fill{background:#8E8C86}" in html and '<b id="ilvlb-v" style="color:#D8D6CF">' in html, "B slider is the ghost side"
assert "@media(max-width:955px){" in html and "@media(min-width:956px){aside{position:sticky;" in html, "the header wraps before the fourth button squeezes it"
# Item-level picker (owner, 2026-09-19: "picking a top and a bottom value, from 70-371, is quite
# difficult ... the slider I want to move should ideally not require two different sliders moving
# independently. for doing a comparison I need to get 4 sliders precisely"). One thumb over the
# levels that actually hold players in the CURRENT selection, with a density strip behind it; the
# two-range form is one click away and still drives the same four inputs, so every downstream
# reader (rowPass, the cohorts, Archon, gearCensus) is untouched.
assert 'id="ilvl-spark"' in html and 'id="ilvl-single"' in html and 'id="ilvl-one"' in html and 'id="ilvl-band"' in html and 'id="ilvl-mode"' in html and 'id="ilvlb-band"' in html, "one-thumb form, density strip, two-range form and the toggle"
assert "const ILVL_DOMAIN_SHARE = 0.005;" in html and "function ilvlWindow()" in html and "function ilvlSig()" in html and "function ilvlShare(lo,hi)" in html, "the window: domain, cache signature and share"
assert "const m=baseMasks(); m.il=false;" in html and "if(!ILW||ILW.sig!==sig){" in html and "coreLo:core.length?core[0]:ILVL.min" in html, "the window ignores the item-level term, so moving the thumb cannot move the ground under it; only the histogram and core are cached, so the drawn ends shrink back"
assert "let core=all.filter(v=>counts.get(v)>=pool*ILVL_DOMAIN_SHARE);" in html and "if(core.length<2) core=all;" in html, "the domain is the populated core, and it fails open"
assert "if(state.ilo>ILVL.min){ lo=Math.min(lo,state.ilo); hi=Math.max(hi,state.ilo); }" in html and "if(gearOn()){   // B's ends belong to the Gear axis" in html and "let lo=ILW.coreLo, hi=ILW.coreHi;" in html, "a value the reader already set is never stranded outside the drawn domain, and the ends are derived fresh so they shrink back"
assert "state.ilo=(v<=w.lo)?ILVL.min:v; state.ihi=ILVL.max;" in html, "off the Gear axis the thumb is a minimum, and its far left clears the filter"
assert "state.ilo=v; state.ihi=ILVL.max; state.ilob=ILVL.min; state.ihib=v-1;" in html and "one.min=gear?w.lo+1:w.lo;" in html, "on the Gear axis the same thumb is the split, and it always leaves B a level"
assert 'ilBand:false,' in html and "function ilvlOneSays()" in html and "const band=state.ilBand||!ilvlOneSays();" in html and '$("ilvl-single").hidden=band; $("ilvl-band").hidden=!band;' in html, "the two-range form stays up whenever one thumb could not say the setting"
assert '" are not one split — two ranges kept."' in html and '" is a band — two ranges kept."' in html, "the toggle is refused with its reason rather than printing a cut nobody set"
assert html.count("state.ilBand=true;   //") == 3, "the quartile split and the swap show both ranges, and a refused toggle keeps the flag with the form, because one thumb cannot say any of them"
assert '(inA?"a":inB?"b":"")' in html and "' parses in this selection\"></i>'" in html and "thinner parses sit outside this span and are still reachable at the ends" in html, "the density strip colours A and B and says what sits beyond its ends"
assert '"· "+Math.round(shA.pct)+"% of the selection"' in html and '" ("+fmtInt(ILVL.unknown)+" parses in the whole build carry none)"' in html, "the readout is selection-scoped and the build-wide count stays in the title"
assert 'id="ilvl-num"' in html and "const numCommit=e=>{" in html and "numTimer=setTimeout(" in html and '$("ilvl-num").onchange=e=>{ clearTimeout(numTimer); numCommit(e); };' in html and "num.min=gear?ILVL.min+1:ILVL.min; num.max=ILVL.max;" in html, "a typed number lands exactly, reaches the levels the drawn scale trims, and does not re-filter the page on every keystroke"
# review follow-up (2026-09-19): the trim is visible, not only in a title; the share's denominator
# is named; the axis reports its clip per side over a CONTIGUOUS domain and counts the parses it
# cannot place; the confound is judged across every drawn bucket, not just the two ends.
assert "const fat=all.filter(v=>ilTot.get(v)>=pool*TREND_ILVL_SHARE);" in html and "for(let v=fat[0];v<=fat[fat.length-1];v++) if(ilsSeen.has(v)) buckets.push(v);" in html, "the item-level axis draws a contiguous span, so the x-axis cannot lie about distance"
assert "ilBelow+=ilTot.get(v);" in html and "ilAbove+=ilTot.get(v);" in html and 'extraCap+=(ilClipped?" Not drawn: "' in html and '" below ilvl "+b0' in html and '" above ilvl "+b1' in html, "the clip is counted and printed per side, never as a range that spans the drawn band"
assert "ilNoLvl++; continue; }" in html, "the axis counts the parses it cannot place"
assert "let kLo=Infinity, kHi=-Infinity;" in html and "const flat=isFinite(kLo)&&kLo===kHi;" in html, "the key confound is judged across every drawn bucket"
assert 'norm==="ret"' in html and "-parse floor at enough points to draw a line here." in html, "the empty chart says which rule stranded it"
assert "#ilvl-single input{pointer-events:auto}" in html and 'sp.style.gap=(w.hi-w.lo)>60?"0":"1px";' in html, "the single track is clickable; a wide window does not draw bars thinner than their gaps"
print("client      : item-level picker: one thumb over the populated window (density strip, share readout); on the Gear axis the same thumb is the split; two-range form one click away over the same four inputs")

# Trajectory item-level axis (owner, 2026-09-19: "I want to be able to see how a class scales
# with item level"). A third option beside Over: time and Key level, out of the same single walk:
# one point per item level, domain = the levels holding at least TREND_ILVL_SHARE of the filtered
# pool (item level is enormously skewed), whatever that clips counted and printed, the key-level
# confound printed as the median key at each end, and time-only furniture (tuning hairline, outage
# sentence) suppressed. Hidden, and never selected, on a payload without rows.ilvl.
assert 'data-x="ilvl" id="trendaxis-ilvl"' in html, "the Trajectory axis segment offers item level"
assert "const TREND_ILVL_MINP = 15;" in html and "const TREND_ILVL_SHARE = 0.005;" in html, "its point floor and domain share"
assert 'const ilAxis=state.trendAxis==="ilvl"&&ILVL.has;' in html and "if(ilAxis&&!(R.ilvl[i]>0)){ ilNoLvl++; continue; }" in html, "the axis needs item levels, and a parse without one has no place on it (and is counted)"
assert "const fat=all.filter(v=>ilTot.get(v)>=pool*TREND_ILVL_SHARE);" in html and "if(fat.length<2) buckets=all;" in html and "ilClipped+=ilTot.get(v);" in html, "the domain is where the players are, and it fails open"
assert "const shTot=ilAxis?ilTot:keyAxis?kTot:(daily?dTot:wTot);" in html, "Share reads the item-level denominator"
assert "if(!keyAxis&&!ilAxis&&hasTune&&D.tuning&&D.tuning.date){" in html and "if(!keyAxis&&!ilAxis&&buckets.length){" in html, "tuning hairlines and outage spans are time-axis furniture"
assert '$("trendaxis-ilvl").style.display=ILVL.has?"":"none";' in html and 'if(state.trendAxis==="ilvl"&&!ILVL.has){ state.trendAxis="time";' in html, "hidden without item levels, and never left selected"
print("client      : Trajectory item-level axis: one point per item level; domain = the populated band, clip counted; per-point floor 15; key confound printed; Share/Rank/Retention inherit; hidden without rows.ilvl")

# the gear read's two honesty controls (2026-09-19, from the design panel): the note says what a
# point of item level is worth across the selection, and ⚑ Hold key pins both cohorts to one rung
# of the key ladder so the rest of the badge is gear — released explicitly, and on leaving the axis.
assert "dA:q(dpsA), dB:q(dpsB), kAll:med(kAll)" in html and "const q=a=>{ if(!a.length) return NaN;" in html, "the census carries each cohort's pooled read and the selection's median key"
assert 'state.keyHeld?"↺ Release key":"⚑ Hold key"' in html and "state.keyHeld={klo:state.klo, khi:state.khi};" in html and "state.klo=state.khi=gc.kAll;" in html, "Hold key pins the ladder and remembers what to give back"
assert "  const held=state.keyHeld;\n  if(held){" in html and '(state.keyHeld?" (held)":"")' in html and "  gearPrevA=null;\n  if(held) buildControls();" in html, "a held key range is labelled in its chip, never outlives the axis, and the key slider is redrawn when the axis gives it back"
assert "function ilvlSpan(lo,hi)" in html and 'atHi?"≥ "+lo:atLo?"≤ "+hi:lo+"–"+hi' in html, "a floor is printed as a floor, not as a range against the payload's end"
print("client      : gear read: % per item level in the note; ⚑ Hold key pins both cohorts to one key level and says what it costs; floors print as ≥/≤")

print("client      : Gear compare axis: Off|Time|Skill|Gear XOR; B = period A over the B item-level cohort; median split / complement / swap; period note, chip, tooltip, table, frame; Archon park+restore; hidden without rows.ilvl")
print("client      : presets Everything kept / This reset / Last reset / 2 resets ago (one per kept reset); anchor-bucketed; no month presets, custom weeks, season sparkline or 'whole season' text")

# 2026-11-13: the page's DPS is the collector's damage / fight clock, the figure Warcraft Logs
# ranks (scripts/diag_rogue_dps.py: 175 of 175 players). The keystone clock carries 15 s per party
# death; the builder recomputed DPS on it until this date and must not start again.
_bsd = (ROOT / "scripts" / "build_site_data.py").read_text()
assert "def dps_clock_note(df: pd.DataFrame, name: str) -> None:" in _bsd and "    dps_clock_note(df, name)\n" in _bsd, "the builder states which clock DPS stands on"
assert "use_keystone_clock" not in _bsd and 'df.loc[ok, "dps"] =' not in _bsd, "nothing in the builder rewrites the dps column"
assert 'health("dps.clock=fight")' in _bsd and "dps.keystone_over_fight_median=" in _bsd, "and says so in build_health.txt, with how far apart the two clocks are"
assert '"dps_clock": "fight",' in _bsd, "the payload names its clock"
assert "DPS is per-player overall damage ÷ run duration" not in html, "no sentence on the page leaves the denominator unnamed"
print("builder     : DPS = damage / fight clock (the figure Warcraft Logs ranks); the keystone clock is printed beside it, never the denominator")

# 2026-11-13 (owner: "the DPS for assassination rogue shows as much higher than sub, for almost any key
# level, including archon mode ... investigate this and see if there is something to fix"): pooled
# across dungeons, a spec is ranked on the dungeons it is PLAYED in. Every parse is scaled by its
# dungeon's par before anything is pooled, through the ONE accessor every consumer reads; the logged
# figure, the factors, each spec's dungeon mix and where its parses sit on the key ladder are printed
# where the figure is; one tick restores the page as it was.
assert "const dpsAt=i=>R.dps[i]*projMul(i)*dnMul(i);" in html and "const dnMul=i=>(state.dnorm&&DNF)?DNF[i]:1;" in html, "ONE accessor carries the factor: no consumer can pool unadjusted values by accident"
assert "dnorm:true," in html and 'id="dnorm" checked' in html and "  refreshDnf();   // ⚖ factors follow the dungeons in view" in html and "  syncDnUI();   // after the Archon predicate above" in html, "on by default, a visible control, refreshed before anything reads dpsAt"
assert "function buildDnPar(){" in html and "buildDnPar();   //" in html and "if(R.rbucket[i]===OUTW) continue;          // never shown, so never part of a par" in html, "par is built once per payload from the rows the window keeps, whatever the filters say"
assert "const DN_MIN=30;" in html and "const DN_CI=0.04;" in html and "const {nd,nr,par,cnt,ok}=DNP;" in html and "for(const d of S) fac[(r*DN_K+k)*nd+d]=(s/S.length)/par[(r*DN_K+use)*nd+d];" in html, "factor = mean par of the dungeons in view / own dungeon's par; one dungeon in view = 1; a thin level borrows"
# a par counts only when its median is KNOWN (30+ parses and a distribution-free 95% bracket within 4%): thirty parses
# alone let a healer cell of 100 parses hand 5-9% of noise to every parse. A role with no such level stays as logged.
assert "ok[c]=(n>=DN_MIN&&m>0&&m-a[Math.max(0,Math.floor(n/2-h))]<=DN_CI*m" in html and "const full=k=>{for(const d of S) if(!ok[(r*DN_K+k)*nd+d]) return false; return true};" in html, "a cell sets a par only when its median is known to +-4%; a level is full only when every dungeon in view passes"
assert "any[r]=good.length?1:0;" in html and "const dnOn=()=>state.dnorm&&!!DNI&&DNI.on&&dnRoles().some(r=>DNI.any[r]);" in html, "a role with no measurable factors is left as logged, and the page says so instead of calling it adjusted"
assert "{const rv=R.dps[i]*dnMul(i); g.rsum+=rv; g.rdps.push(rv)}" in html and html.count("{const rv=R.dps[i]*dnMul(i); g.rsum+=rv;") == 2 and "...dnFields(g,state.pctl/100)});" in html and "...dnFields(g,ELITE_PCTL)});" in html, "both aggregations carry the mix and the logged figure; the recorded figure rides the same scale"
# the tooltip must stay on a 1080 screen: the mix is a full-width line (as a value cell it wrapped every label and
# doubled the box), the box is clamped on its measured height, and a tag under a Spec name never sizes its column
assert 'tip.style.top=Math.max(8,Math.min(y+14,innerHeight-tip.offsetHeight-8))+"px";' in html and '<tr><td class="tmix" colspan="' in html and "#tip td.tmix{" in html, "the tooltip is clamped on its measured height and the dungeon mix sits on a full-width line"
assert ".dmxtag.in{margin-left:0; display:inline-block; max-width:132px; overflow:hidden; text-overflow:ellipsis;" in html, "a long dungeon name in the tag cannot push a column off a 1920 screen, and the headline tag (63% in Ruby Life Pools, 127.05px) is not cut"
assert '+(DNI&&DNI.on?" · "+(dnOn()?"⚖ adjusted DPS":"DPS as logged"):"")+labStamp("pulse");' in html and '["dps","DPS @ p"+state.pctl+dsfx()]' in html, "Pulse says which DPS its figures, deltas and verdicts stand on"
assert "const dnFewPars=()=>!!DNI&&!DNI.on&&DNI.thin.length>0&&DNI.S.length+DNI.thin.length>1;" in html, "dungeons without a par are never reported as 'one dungeon in view'"
# the Archon replica is compared with archon.gg on LEVEL, and a percentile read in the average dungeon sits about 5% under the
# pooled one for 23 of 25 specs. So the replica reads its percentiles off each kept parse counted once in EVERY dungeon
# in view: an evenly played spec then reads what the pooled recipe gave, and only a lopsided mix moves.
assert "function dnSpread(out,i,v){" in html and "for(const d of DNI.S) out.push(v/f[b+d]);" in html, "the replica's pool: the adjusted parse at every dungeon's par"
assert "if(dnL){dnSpread(g.dps,i,dv); if(state.proj) dnSpread(g.rdps,i,rv)} else {g.dps.push(dv); g.rdps.push(rv)}}" in html, "aggregateElite reads percentiles off the spread pool; sums (averages) are per parse either way"
assert "const sdps=order.map(dpsAt);" in html, "the lens band prints its members' range (a range read off the pool held 29% of them)"
assert "if(state.elite&&dnOn()){dps=[]; for(const i of a) dnSpread(dps,i,dpsAt(i))} else dps=a.map(dpsAt);" in html, "in the replica a build's median is read off the same pool as the bar"
assert "const can=d=>{for(let c=d;c<ok.length;c+=nd) if(ok[c]) return true; return false};" in html and "const S=S0.filter(can), thin=S0.filter(d=>!can(d)), on=S.length>1;" in html and "if(!DNI.inS[R.dun[i]]){for(let j=0;j<DNI.S.length;j++) out.push(v); return}" in html, "a dungeon with no par at all is left out of the average and named; it never switches the adjustment off for the rest"
assert "for(const r of roles){            // every role in view: each row is scaled with its OWN role's factors" in html, "borrowed levels are listed for every role in view"
assert 'const dnLoggedLbl=()=>state.proj?"Projected, before ⚖":"As logged";' in html and 'h+=row("Recorded average"+dsfxA(r.a), fmtInt(r.a.ravg), "–");' in html, "with the tuning projection on, no row is called logged that is not, and the recorded rows carry the mark"
assert "+dnNote()+" in html and 'return dnFewPars()?"<b>⚖ DPS as logged here:</b> "+esc(dnOneTxt().replace(/^N/,"n"))+". ":"";' in html, "the period note says when several dungeons are in view and too few pars are known to adjust any of it"
assert "const someAdj=dnOn()&&(!FRAME_A||!FRAME_A.groups||!FRAME_A.groups.size||[...FRAME_A.groups.values()].some(g=>g.adj!==false));" in html and """+'">⚖</span>';""" in html and 'let s=esc(p.join(" · "))+dnFrag+labStamp(sec);' in html and 'p.push(adj?"⚖ adjusted DPS":"DPS as logged");' in html and "for(let i=0;i<N;i++) if(groupKey(i)===state.frameKey){adj=!!DNI.any[R.role[i]]; break}" in html and 'scopeChip(box,"DPS as logged","dnorm-chip")' in html, "every section caption, the frame and the strip say which DPS is on screen"
assert "function dnKeyRise(){" in html and ':["akey","Avg key",,dnAkeyTitle()],' in html, "what a key level is worth is read off the pars and sits on the Avg key header"
assert 'h+=row(dnLoggedLbl()+", average"' in html and "the same read before the dungeon adjustment" in html and "'+dnLoggedLbl()+(skill?" in html, "the logged figure stays beside the adjusted one in the tooltip and the Spec Frame"
assert 'case "dmix": return r.a.dshare;' in html and ".data th.kf,.data td.kf{padding-left:2.6rem}" in html and ':["akey","Avg key",,' in html, "the table carries each spec's dungeon mix and its place on the key ladder, and both sort"
assert """if(k==="floorK") return v>=0?'<span style="white-space:nowrap">≥ +'+v+" · "+Math.round(100*r.a.kshare)+"%</span>""" in html and 'h+=wide("Key floor +"+r.a.floorK+": keeps "+Math.round(100*r.a.kshare)+"% of the spec’s "+fmtInt(r.a.kpool)+" parses");' in html and "kshare:g.n/hist.get(k).n, kpool:hist.get(k).n," in html, "the replica's floor is whole key levels, so the share of a spec's parses it keeps is printed beside it"
assert "if(o.adj&&dmixHot(o)){" in html and 'const dmixHasSplit=a=>!!a&&a.din!=null&&a.dout!=null;' in html and "function dmixLine(a){" in html and """rows+='<tr><td class="fmix" colspan="2">Dungeon mix: '+dmixCell(g)""" in html and ".frows td.fmix{text-align:left;" in html, "a spec that rests on one dungeon shows the same read there and in the rest, on the same scale"
assert "const DN_FLAG=0.35;" in html and "const dnHotAt=()=>Math.min(0.8,Math.max(DN_FLAG,2.5/(DNI.S.length+DNI.thin.length)));" in html and "const dmixHot=a=>!!DNI&&DNI.on&&Math.round(100*a.dshare)>=Math.round(100*dnHotAt());" in html and "+(dmixHot(r.a)?dmixTag(r.a):'');" in html and 'dmixHot(r.a)?"<br>"+dmixTag(r.a,"in"):""' in html, "a spec played mostly in one dungeon is named on its bar and under its Spec"
assert '(a.dshare>=1?"all in ":Math.round(100*a.dshare)+"% in ")' in html, "the dungeon-mix tag reads the share and the dungeon"
assert "adj:dnOn()&&g.ix.length>0&&!!DNI.any[R.role[g.ix[0]]]};" in html and "if(o.adj&&g.ldps.length){" in html and 'const dsfxA=a=>a&&a.adj?' in html and html.count("dsfx()") == 1, "the ⚖ mark and the logged pair are decided per GROUP: a role with no factors (healers) is as logged and carries neither; only Pulse's header reads the view-level mark"
assert "built before 2026-11-13" not in html, "the footer makes no calendar claim the build stamp beside it can contradict"
print("client      : ⚖ dungeon-adjusted DPS through dpsAt; par per role x key x dungeon, counted only when known to +-4%, other levels borrow, a role with none stays as logged, one dungeon = as logged; note, chip, tooltip, frame; Avg key / Key floor / Dungeon mix columns; concentrated specs named; Archon replica reads its percentiles off each parse counted once in every dungeon")

# 2026-11-21 (owner: "the talent build for devourer fel scarred ... seems wider than other specs like rogue. fix
# this. also add a button to export talents for a selected talent build"). The panes are drawn from the union of
# talents a spec's players took, and a record carries the spec Warcraft Logs gives the player: 15 of 274,007
# records held another spec's talents, and one is enough to put that spec's whole tree and a third hero tree
# on screen (six specs). A node is drawn for a spec only if the GAME shows it to that spec.
_ft = (ROOT / "scripts" / "fetch_traits.py").read_text()
_ts = (ROOT / "scripts" / "talent_string.py").read_text()
assert "if own_sub is not None and st not in own_sub:" in _bsd and "elif (vis is not None and str(nid) in vis\n                  and sid not in vis[str(nid)]):" in _bsd and "foreign[sk] = n_foreign" in _bsd, "a hero tree the spec's own selector does not offer, and a talent the game does not show the spec, are left out of its panes and counted"
assert "and (g[3], g[0], g[1]) in seen_at):" in _bsd and "_granted_to" not in _bsd and 'if variant in ("per-spec", "classRef"):   # forced, for tests' in _bsd, "on a hero tree two specs share, the other spec's node is left out where this spec has its own; every node left out is counted, with no exception for one the game gives the spec without showing it (a log does not list those)"
assert 'health("talents.foreign_nodes="' in _bsd and 'health(f"talents.unjudged_specs=' in _bsd and '"judged": vis is not None, "sid": sid,' in _bsd, "build_health.txt says how many nodes were left out and how many specs could not be judged"
assert 'cx = sorted(nids - v["cls_nodes"]) if v["judged"] else []' in _bsd and 'v["cls_nodes"] = {n for n in cn if str(n) not in cvis\n                              or v["sid"] in cvis[str(n)]}' in _bsd, "a spec's class pane is every shared class node the game shows it; a cache without the game tables hides nothing"
assert "if(ok(cls)&&Array.isArray(t.cx)&&t.cx.length){" in html and "t.__cls={nodes:cls.nodes.filter(n=>!hide.has(+n.id))," in html and "edges:cls.edges.filter(e=>!hide.has(+e[0])&&!hide.has(+e[1]))};" in html, "the page leaves a spec's hidden class nodes and their edges out at the one place the class pane is read"
# the trees are read at the LIVE game build: asked without one, the tables' site answers with its newest build of
# any product, which on the day was the public test realm's
assert 'and not r.get("is_bgdl")]' in _ft and "and _ver(carried.get(\"build\")) > _ver(geo.get(\"build\"))" in _ft and "and plausible(carried, geo) is None):" in _ft, "a build staged for background download is not the live build; the copy carried between runs is used only when it is newer than the committed file"
assert 'LIVE_PRODUCT = "wow"' in _ft and 'return fn._get_csv(table, {**(params or {}), "build": build})' in _ft and "    if not build:\n        return None\n" in _ft and _ft.count("fn._get_csv(") == 1, "every geometry table is requested at the live build, through one getter; with no build nothing is fetched"
assert 'patched = live is not None and geo.get("build") != live' in _ft and "if args.refresh or not geo.get(\"trees\") or patched:" in _ft and "why = None if fresh is None else plausible(fresh, geo)" in _ft and "lost = playable(old) - playable(fresh)" in _ft, "a new live build re-reads the trees; a snapshot that lost its nodes or a spec's hero selector is refused"
# the export: the game's own import string, written by the builder from the logged tree, proven against 160
# strings the game exported (test_talent_string.py). The page only hands it over.
assert "VERSION = 2" in _ts and 'ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"' in _ts and "tree_hash: bytes = bytes(_HASH_BYTES)" in _ts, "format version 2, the game's alphabet, an all-zero tree hash"
assert "import talent_string  # noqa: E402" in _bsd and "imp_by[(sk, build)] = talent_string.encode(\n                        stree.spec_id, stree.order, picked)" in _bsd and "except (ValueError, KeyError, IndexError, TypeError) as e:" in _bsd and 'b["imp"] = imp' in _bsd and 'entry["game"] = str(geo["build"])' in _bsd, "the builder writes the string per listed build and names the game build it was written for"
assert 'health(f"builds.import_strings={n_imp}")' in _bsd and 'health(f"builds.import_strings_refused={sum(imp_no.values())}")' in _bsd and 'health("builds.import_strings_game_build="' in _bsd, "written, refused and the game build are in build_health.txt"
assert "function csImportOf(vocab,vb){" in html and 'return (typeof vb.imp==="string"&&vb.imp)?vb.imp:null;' in html and "imp:csImportOf(d.vocab,vb)," in html, "a build's string is the builder's, or there is none: the page never makes one up"
assert "+((r.imp&&(lit||!r.hash)&&(clip||r.hash))" in html and """?csExportBtn(x+1,r.imp,"Build "+(x+1)+(r.hero?" ("+r.hero+")":""),!r.hash):'')""" in html, "the export button sits on the lit build's chip (a build that is its own string keeps its copy button on every chip)"
assert """'">'+(own?"copy":"export")+'</button>';""" in html and "const CS_COPY_BASE=900;" in html and """?csExportBtn(CS_COPY_BASE,dx.baseImp,"the pinned base"):'')""" in html, "export on a written string, copy on a build that is its own string; the pinned base has its own"
assert 'navigator.clipboard.writeText(s).then(()=>{ if(b.isConnected){ b.textContent="copied"; back() } },fail);' in html and 'b.textContent="below"; back();' in html and "box.value=s; box.focus(); box.select();" in html and "if(!navigator.clipboard){ fail(); return }" in html, "it says copied only when the clipboard took the string; otherwise the string is shown, selected"
# the review of 2026-11-21 (seven findings on the page, six confirmed by a second agent):
assert "      if(e.target!==c) return;\n      if(e.key===\"Enter\"||e.key===\" \"){ e.preventDefault(); light() }" in html, "Enter/Space on a button inside a chip is the button's: export and base work from the keyboard"
assert 'const anchor=host.querySelector("#cs-bldchips")||b.closest("table")||b.parentElement;' in html and 'data-copy="\'+slot+\'" data-what="\'+escA(what)+\'" title="' in html, "the refused-copy box sits under the chip row or under the table, never in a table row, and names the build it holds"
assert '.cbtn[data-copy]{min-width:4.8em; text-align:center}' in html and ".bldchip.base .cbtn{border-color:#4a3b12; color:#241d0e; background:transparent}" in html, "the button keeps one width across export / copied / below, and is drawn for the gold base chip"
assert "    if(screenBuild===-1&&base){\n      const j=T.rows.findIndex(r=>r.s===base.s&&r.sel);\n      if(j>=0) screenBuild=j;\n    }" in html, "a pinned base that is back on the chip row is the lit chip, so it has its export"
print("talents     : a spec's panes hold what the game shows that spec (foreign nodes counted, not drawn); export = the game's own import string per listed build, written at the live game build")

# 2026-11-26 (owner: "there is a ton of text above the overview that is completely useless. examine the UI for all of
# these places you've placed informational text that no one cares about. this is my site, I already know how what
# works. clean it all up. only leave something if it is particularly relevant and important or because it is
# information I have asked you to display in the past."). 296 pieces of prose were decided on: 154 removed, 81 cut
# down to the number they carried, 61 kept. THE RULE FROM HERE ON: the page prints a sentence only when it qualifies
# a figure with a number (a count, a share, n=), or reports something abnormal right now. It does not explain how a
# statistic, a control, a mode or the collection works. The pins below hold one phrase from each removed block (not
# every removed sentence), and a length budget holds the static note elements; one that fails means a paragraph is back.
for _gone, _what in (
        ('id="coverage-note"', "the Coverage paragraph above the Overview"),
        ('id="retention-note"', "the Retention paragraph above the Overview"),
        ("Runs enter this site through", "the Coverage paragraph"),
        ("This dashboard holds the newest", "the Retention paragraph"),
        ("Dungeon-adjusted DPS:</b> each parse", "the ⚖ rule above the Overview"),
        ("Key level is not adjusted", "what a key level is worth, as a paragraph"),
        ('id="minchars-help"', "the trust-gate paragraph in the sidebar"),
        ("How many distinct players must have", "the trust-gate paragraph"),
        ("Untimed (over-timer) runs reach this site", "the timed-only paragraph in the sidebar"),
        ("Empty selections mean", "the sidebar's closing paragraph"),
        ("click any bar for its Spec Frame", "the Overview head's instruction"),
        ("click a column to sort", "sort instructions in section heads"),
        ("DPS is per-player overall damage", "the footer's methodology"),
        ('id="foot-src"', "the footer's source paragraph"),
        ("90 the ceiling view sites like Archon publish", "the chart footnote that explains the lens"),
        ("Timed runs only.", "the period note restating the timed switch"),
        ("controls are bypassed while active", "the Archon recipe in the period note and its card"),
        ("grey ghost", "the solid/ghost legends in the sidebar, the period note and the captions"),
        ('+" (ghost)"', "the last solid/ghost legend in the Spec Frame scope"),
        ("median = the middle DPS", "the Talents footnote that defines a median"),
        ("Rank view: each point is", "the Trajectory rank caption"),
        ("Share view: each point is", "the Trajectory share caption"),
        ("Retention is measured inside the leaderboard", "the Trajectory retention caption"),
        ("one point per key level", "the Trajectory key-axis caption"),
        ("key range released", "a notice confirming Release key"),
        ("Gear compare off", "a notice confirming Compare: Off"),
        ("Lean = the share of this spec", "the Gear tab's Lean footnote"),
        ("dungeon adjustment is for DPS only", "the Top Comps caption on what ⚖ covers"),
        ("Deaths are whole numbers, so percentile reads step", "the deaths footnote on percentiles"),
        ("so the badge is not a clean gear read", "the gear overlap paragraph"),
        ("(solid)", "a solid/ghost legend, in any spelling"),
        ("Brighter bar = higher", "chart footnotes that explain the bar's shade"),
        ("Solid bar: period A", "compare legends under the chart"),
        ("Strength scores each run against", "the Top Comps method caption"),
        ("Sorted by DPS at the current lens by default", "the Pulse method caption"),
        ("Dash patterns distinguish specs", "the Trajectory legend sentence"),
        ("picked talents in full color", "the Talents tab's how-to-read footnote"),
        ("the game's import strings are not present in combat logs", "the Talents tab's provenance footnote"),
        ("click a tile to unfold", "the Gear tab's instruction"),
        ("Lens set to p", "a notice confirming a click on a lens chip"),
        ("Archon replica on — filters rewritten", "a notice confirming the Archon switch"),
        ("built from Warcraft Logs fight rankings", "the long page subtitle")):
    assert _gone not in html, "this explanatory text was removed on the owner's word and must not return: " + _what
assert '<footer>Data from Warcraft Logs · <span id="built"></span> · <a id="rawlink" href="data.json">data.json</a></footer>' in html and '$("built").textContent="built "+d.built;' in html, "the footer is one line: the source, the build time, the payload"
assert 'if(gz.ok){ PAYLOAD_FILE=file+".gz"; return new Response(' in html and 'if(a&&PAYLOAD_FILE){ a.href=PAYLOAD_FILE; a.textContent=PAYLOAD_FILE; }' in html, "the payload link names the file that was loaded: the builder ships it plain under 8 MB and gzipped above, and the link used to be dead in the first days of a reset"
assert 'sub:"Midnight Season 2",' in html and '<div class="hero-sub" id="hero-sub">Midnight Season 2</div>' in html, "the page is titled with the season and nothing else"
assert ".hint:empty,.caption:empty,.kpi-note:empty,.cs-foot:empty{display:none}" in html, "a note element with nothing to say takes no room"
# what still speaks: numbers beside the figures they qualify, and abnormal states
assert "function retentionWarnHTML(){" in html and '+" "+retentionWarnHTML()).replace(/ {2,}/g," ").trim();' in html and "⚠ Stale: the newest run held is older than " in html, "stale data and a late newest run are still said, in the period note, only when they happen"
assert "⚠ Sampled payload: " in html and "⚠ Collection was down " in html, "a sampled payload and a collection outage inside the window are still said"
assert 'p.push(sec==="trajectory"?"all resets":describePeriod(state.weeksA));' in html and '$("overview-sub").innerHTML=scopeLine("overview");' in html and '$("trend-sub").innerHTML=scopeLine("trajectory");' in html, "section heads keep the state they reflect (lens, period, keys, timed, ⚖) and nothing after it; Trajectory reads all resets"
assert '?["floorK","Key floor","kf"]' in html and '["dmix","Dungeon mix","txt"]);' in html, "the Data Table keeps its Dungeon mix and Key floor columns"
assert "let __noticeHold=false;" in html and 'function setNotice(t){ const n=$("notice"); if(n) n.textContent=t; __noticeHold=!!t; }' in html and 'for(const ev of ["pointerdown","keydown","click","input","change"]) window.addEventListener(ev,()=>{ __noticeHold=false; },true);' in html and '  if(!__noticeHold){ const n=$("notice"); if(n&&n.textContent) n.textContent=""; }' in html, "a notice lives until the reader's next action: render clears it unless it is held, and the next press, key, click or input releases the hold before its own handler runs"
assert "__renderGen" not in html and not _re.search(r"""setNotice\(\s*(""|\'\')\s*\)""", html) and not _re.search(r"setTimeout\([^)]*__noticeHold", html), "no handler clears the slot by hand and no timer releases it: the render counter of 2026-11-26 kept a notice through the action that undid it (Hold key, then Release key), and the zero timer that replaced it lost to a second click queued behind a slow handler and let a resize wipe a notice nobody had acted on"
assert 'function syncTimedHint(){ const el=$("timed-hint"); if(el) el.innerHTML=state.timedOnly?"":TIMED_HINT; }' in html, "the key levels with no untimed run are named only while All completed is selected, which is what they qualify"
print("text        : the page explains nothing; it prints a sentence only beside a number it qualifies or for something abnormal")

# 2026-11-26, the independent review of the cleanup (four reviewers, each finding put to a skeptic). What it found
# in the page and what now holds it:
import re as _re
from html.parser import HTMLParser as _HTMLParser
# THE STATIC PAGE HOLDS LABELS: every text node of the markup the page ships (everything before the script) is at
# most 60 characters and every title attribute at most 120. Before the cleanup nine text nodes were longer (the
# longest 473) and two titles. The first form of this check read three note classes only and missed a paragraph in
# a class-less <p>, in the footer, in a nested div and in a title.
class _Static(_HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.texts, self.titles = [], []
    def handle_starttag(self, tag, attrs):
        t = dict(attrs).get("title")
        if t: self.titles.append(t)
    def handle_data(self, data):
        t = _re.sub(r"\s+", " ", data).strip()
        if t: self.texts.append(t)
_sp = _Static(); _sp.feed(html[html.index("<body"):html.index("<script")])
assert len(_sp.texts) > 80 and max(map(len, _sp.texts)) <= 60, "static text is a label, never a paragraph: " + repr(max(_sp.texts, key=len))
assert _sp.titles and max(map(len, _sp.titles)) <= 120, "a static hover title stays short: " + repr(max(_sp.titles, key=len))
# EVERYTHING THAT WAS REMOVED STAYS REMOVED: the fixture lists each prose fragment that sat in a string or a text node
# before the cleanup and is nowhere in the page now. The 43 hand-written pins above name the blocks; this holds the rest.
_removed = json.loads((ROOT / "scripts" / "fixtures" / "removed_prose.json").read_text(encoding="utf-8"))["phrases"]
assert len(_removed) > 300, "the removed-prose fixture is truncated"
_flat = _re.sub(r"\s+", " ", html)   # line breaks collapsed: a paragraph may be wrapped in the source
_back = [_p for _p in _removed if _p in _flat]
assert not _back, "text the owner had removed is back on the page (or quoted in a comment of it): " + repr(_back[:3])
# the gear compare's per-item-level figure is read at the lens, so it carries the lens's own label: the cut typed
# "median DPS" over a figure that was the p85 read at p85 (11.5% printed under "median" where the median gap was 13.7%, on the build of 08:37 UTC)
assert '? " A "+dpsLabel().replace(/^M/,"m")+" "+(gc.dA>=gc.dB?"+":"")' in html and '" A median DPS "' not in html, "the gear note names the statistic it prints: the lens's own label"
# one reset boundary per run, as the builder cuts: a row filed under no region took the default boundary and was
# hidden past a window its four group mates were inside (1 row on the 2026-11-26 payload)
assert 'const unkRi=D.regions.indexOf("Unknown"), runReg=new Int16Array(runCount).fill(-1);' in html and "const rr=runReg[R.run[i]], rg=rr>=0?rr:R.reg[i];" in html and "b0=boundsH[rg];" in html and "const b0=boundsD[rg];" in html and "boundsH[R.reg[i]]" not in html, "a row is bucketed by its run's region, the rule build_site_data.py _run_regions cuts on"
assert "' parse'+(outWindowRows===1?'':'s')+' past the kept resets, not shown.</b>'" in html and "if(outWindowRows>0) h+=" in html and 'console.warn("parses past' not in html, "parses the payload carries past the window are counted on the page while there are any, not in the console"
assert 'h+=\'<div class="foot">\'+esc((D.specstats.keyMin?"timed +"+D.specstats.keyMin+" and higher · ":"")+"n="+fmtInt(S.n))+\'</div>\';' in html, "the Spec Frame's fixed stats print their key floor and their n, not the builder's method paragraph"
assert 'note.textContent="";\n    sub.textContent="not available in the Archon replica";' in html, "Pulse under Archon says so in its head, not as a lone dash over a one-line body"
assert 'if(k==="a_runs"||k==="a_n"||k==="chars") return fmtInt(v);' in html, "Runs takes the thousands separator Parses and Characters have"
assert 'id="comps-caption"' not in html and "function dnNote(){" in html, "what the removed text left behind is gone: an element only ever written empty, parameters nothing read"
# what still speaks, pinned as it now reads (the review deleted each of these from a copy and the suite stayed green)
for _kept, _what in (
        ('+(rows.length>COMPS_MAX?" · top "+COMPS_MAX+" shown":""))', "Top Comps says when it lists the top N only"),
        ('" distinct field it, top "+sorted.length+" shown"', "Top Comps, With a spec: how many comps field it and how many are listed"),
        ('return ofN>shown?"Showing "+shown+" of "+ofN+" groups.":"";', "Trajectory says when it draws fewer groups than qualify"),
        ('if(ranked.length>CHART_MAX) cap+=" Showing "+CHART_CUT+" of "+ranked.length+" groups.";', "the Overview chart says when it draws fewer groups than qualify"),
        ('$("showncount").textContent=fmtInt(rows.length)+" of "+fmtInt(A.groups.size)+" shown";', "the strip counts the groups shown of those in view"),
        ('setNotice("Gear compare: key held at +"+gc.kAll', "Hold key prints what the hold costs in parses"),
        ('+" · A "+fmtInt(beforeA)+" → "+fmtInt(afterA)+" parses · B "+fmtInt(beforeB)+" → "+fmtInt(afterB)+".");', "Hold key prints both cohorts before and after"),
        ('"⚖ on — nothing in view can be adjusted."', "ticking ⚖ with nothing to adjust says so"),
        ('return \'<div class="cs-foot">No export string for \'+esc(lab)+\'</div>\';', "a build with no export string says so"),
        ('+(on&&w.unk?" · "+fmtInt(w.unk)+" no ilvl":"");', "the item-level hint counts parses with no item level"),
        ('+(gc.unk?" "+fmtInt(gc.unk)+" parses have no item level.":"")', "the gear note counts parses with no item level"),
        ('+\'</b> hidden (under \'+ELITE_MIN+\' qualifying parses)\';', "the Archon card counts the specs the replica hides"),
        ('return "B: "+ilvlBText()+(B?" · "+fmtInt(B.parses)+" parses":"")+"."', "the gear note prints the B cohort and its parse count"),
        ('if(game&&b.textContent==="export") b.title+=" · game build "+game;', "the export button names the game build its string is written for"),
        ('<span id="frame-dead" class="fs" hidden>Character screen unavailable — builds data missing</span>', "a missing builds sidecar is said where the button was")):
    assert _kept in html, "this count or abnormal-state text is still owed to the reader: " + _what
print("review      : gear note carries the lens label; notices clear on the next action; rows bucket with their run; kept counts pinned as they read")

# the skeptics' round on the fixes above (three readers told to break them)
assert not _re.search(r"specstats\W{1,6}cohort", html), "the builder's method paragraph under the Spec Frame's fixed stats stays off the page, however it is reached"
assert "keyHeld:state.keyHeld," in html and 'state.keyHeld=("keyHeld" in st)?st.keyHeld:null;' in html, "a held key range travels with the Archon snapshot: the Archon route skips gearOff(), and a hold it left behind made a later Gear, Off hand back the wrong key range"
assert "runReg[r]=best;" in html and "const ri=R.reg[i]; if(ri===unkRi) continue;" in html and "if(w>=KEEP_BUCKETS){R.rbucket[i]=OUTW; outWindowRows++; continue}" in html and "R.rreg[i]=rg;" in html, "the run's region is assigned, a row with no region casts no vote, rows past the window are counted (test_reset_buckets.py runs the function against the builder)"
assert "const c=cutMs[D.regions[R.rreg?R.rreg[i]:R.reg[i]]];" in html and "const ri=R.rreg[i];" in html, "the sidecar note and the reset-hole notice judge a row by the region it was bucketed by: the row the fix un-hid was at once reported as 'older than ... and carry none' though it carried everything"
assert "if(R.day[i]<dayLo||R.rbucket[i]===OUTW) return false;" in html and "if(R.day[i]<dayLo||R.key[i]<fk||R.rbucket[i]===OUTW) continue;" in html, "rows past the window are in no view: the Archon replica read them through its own 14-day window while the note said 'not shown'"
assert "function settleB(){" in html and "function nextToA(){" in html and "if(on&&!settleB()){" in html and "c.onclick=()=>periodChipClick(prefix, sel, target);" in html and "if(state.compare&&!settleB()){" in html and 'if(state.compare&&setsEqual(state.weeksA,target)){ setNotice("B is the period A already shows."); return; }' in html and "state.weeksA=new Set(av); state.weeksB=new Set(bv); state.bPicked=true;" in html and "state.weeksB.clear(); state.bPicked=false;" in html and '"Compare needs a reset in A or B — both are on everything kept."' in html and 'setNotice("Compare off — A and B are both everything kept.");' in html and "setNotice(availWeeks.filter(w=>w<999).length<2" in html and "Compare needs one reset in A" not in html, "Compare: Time never shows a period against itself: one rule, settleB(), for the switch and for the period chips; a B chip naming A's period is refused; the refusals give the reason that applies. scripts/test_retention_warn.py runs it over every sequence of up to four clicks"
print("skeptics    : notice hold released by input; key hold parked with Archon; one region per run everywhere; removed prose held by fixture (%d phrases)" % len(_removed))

# 2026-11-28, three weeks of data. While the journals are younger than the window (they had been pruned to 16 days
# and grow a day a day), the oldest reset is part-filled for the regions whose cut is earlier than the oldest row
# held. The page says so, with the hours, only while the selection reads that reset and never under Archon.
assert "function retentionShort(r,only){" in html and "if(only&&!only.has(reg)) continue;" in html and "if(!r||!r.held_from||!r.cut) return null;" in html and "r.oldest_seen" not in html and "const h=(t0-Date.parse(r.cut[reg]))/36e5;" in html and "if(h>=1&&h<168) parts.push(reg+\" \"+Math.min(167,Math.round(h))+\" h\");" in html, "a region is short by the hours between its cut and the instant the journals begin (retention.held_from, never the oldest row: the first three-reset build held a row a week older than its data); a week or more is not a part-filled reset, so the hours printed stop at 167; a region the Region filter leaves out is not named. scripts/test_retention_warn.py runs the function and requires the sentence"
assert "const old=KEEP_BUCKETS-1, reads=w=>!w.size||w.has(old);" in html and "const sh=(!state.elite&&weekCounts.has(old)&&(reads(state.weeksA)||(state.compare&&reads(state.weeksB))))?retentionShort(r,state.reg.size?state.reg:null):null;" in html and "⚠ Oldest reset held from '+sh.from+': short by '+sh.parts.join(\" · \")+'.</b>'" in html, "the part-filled oldest reset is said while period A or B reads it, with the hours short per region"
# The trust gate is a share of the players in view, measured against a reference pool. That pool was "every kept
# row", which was two resets until the policy changed; left alone it would have grown by two thirds and the same
# slider value would have asked this reset's view for 47 characters where it asked for 77 (11 groups on the default
# Overview instead of 7, with not one row changed). The reference stays the newest two resets.
assert "const TRUST_REF_RESETS=2;" in html and "{ const w=R.rbucket[i]; if(w>=TRUST_REF_RESETS&&w<999) continue; }" in html, "how much history is kept does not move the trust gate: its reference is the newest two resets"
print("retention   : %d resets kept, each with its own period chip; a part-filled oldest reset is said with its hours" % _ret.RETENTION_RESETS)

print("\nPASS")
