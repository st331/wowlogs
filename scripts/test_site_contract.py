#!/usr/bin/env python3
"""The page's static contract: exact strings scripts and design decisions pin in
site/index.html (and a few in the builder), so a later edit that quietly drops a
guard, a label or an honesty sentence fails here first.

Until 2026-09-30 these pins lived at the end of test_procs.py, the Lightspire
trinket Lab's test; that feature is gone (owner: "remove the lightspire trinket
labs feature and stop any data collection for it") and the pins moved here.
Run: python3 scripts/test_site_contract.py
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# --- static client contract -------------------------------------------------------
html = (ROOT / "site" / "index.html").read_text()
assert "Sampled payload:" in html and '"sample": dict(SAMPLE_INFO)' in (ROOT / "scripts" / "build_site_data.py").read_text(), "never a silent sample"
assert 'id="coverage-note"' in html and "function renderCoverageNote" in html and "renderCoverageNote();" in html, "coverage note"
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
assert "pairs worn together in window" in html and "Every player is in exactly one row, so this column sums to 100%" in html and "so Both can never exceed the smaller of the two" in html, "pair caption states the partition and the marginals"
assert "' more pair'" in html and " below the n≥'+CS_PAIR_MIN+' floor</span>" in html and "other / none in both slots</span>" in html and '" in both slots":"other / none"' in html, "every residual is a named row; A + other / none and same-item are ranked classes"
# 2026-09-30 (owner: "reduce the minimum n to 1. it is still useful to see if even one person is using a trinket pair."):
# the pair floor is 1, members come from the UNFILTERED tally, the By-trinket face keeps n>=3
assert "const CS_PAIR_MIN=1;" in html and "if(o.c<CS_PAIR_MIN||!a||(o.b&&!b))" in html and "const src=M.all||M.ent;" in html and "return {ent, all, rowKeys, rowRaw, n:d.gearIdx.length, ok:sa>=0&&sb>=0};" in html and "if(c<CS_ENTRY_MIN) continue;                   // same floor as the per-slot fold" in html, "pair floor 1 over the unfiltered tally; By-trinket floor unchanged"
assert "Every pair worn by at least one player counts" in html, "the caption says the floor is one"
assert " listed for its slot, or an empty slot" in html and "const capTxt=[...new Set(pr.map(id=>((d.vocab.items||[])[slots.indexOf(id)]||[]).length))]" in html, "vocab cap named where other/none is defined, read from the live vocab lengths"
assert "with #1 " in html and "wear both the most used and the 2nd most used " in html and "const pairOf=p=>PAIRS[p]||(PAIRS[p]=csPairModel(poolOf(p)));" in html and "PAIRS[screenFold]" in html, "rank-2 pooled tile carries the #1+#2 pair share with its denominator, from the one memoised model"
assert "or the pairs worn together" in html and "s — combined distribution in window" in html, "pooled tile names the reading; the By-trinket header is verbatim"
_pt = html[html.index("function csPairTR"):html.index("function csPairBodyHTML")]
assert "ilvl" not in _pt and "csFoldCols(" not in _pt, "no item level, no lean columns on a pair row"
assert ".cs-fold td.fi2{width:62px" in html and "#cs-fold .fh.pv .fht{min-width:0; flex:1 1 auto}" in html, "pair CSS: two-icon cell; the header wraps, never ellipsizes"
assert "showing the top \"+COMPS_MAX+\" on this sort" in html and "Showing \"+shown+\" of \"+ofN+\" groups that pass the gate" in html, "comps + trajectory caps state N of M"
assert "ratings rounded to steps of" in html and "not published in this build (size ladder)" in html, "stats scale / withheld labels"
_b = (ROOT / "scripts" / "build_site_data.py").read_text()
assert '"caps": {"items": item_cap' in _b and '"stats_all": list(SIDECAR_STATS)' in _b and _b.count("_retention_header(df, ") >= 2, "sidecar headers carry caps/window/stats_all, the window from retention"
# 2026-09-14 retention: the build drops rows; the payload ships what it dropped and where it cut,
# every sidecar window is BOUND to the retention constant, and the sentence leads with the policy
assert '"retention": dict(RETENTION_INFO)' in _b and "SIDECAR_WINDOW_RESETS = RETENTION_RESETS" in _b and "BUILDS_WINDOW_RESETS = RETENTION_RESETS" in _b, "retention facts ship; sidecar windows bind to retention"
assert '"coverage": coverage' in (ROOT / "scripts" / "build_site_data.py").read_text() and 'persist_sweep_stats({"sweep.public_runs"' in (ROOT / "scripts" / "fetch_data.py").read_text(), "coverage facts flow fetch -> build -> page"
# 2026-09-14 retention (owner): the page STATES the window on every build from
# payload.retention, buckets on the builder's anchor, and has no control that
# reaches past the newest two resets
assert "D.retention" in html and 'id="retention-note"' in html and "function renderRetentionNote" in html and "renderRetentionNote();" in html, "the retention note is rendered every build"
_rn = html[html.index("function renderRetentionNote"):]; _rn = _rn[:_rn.index("\nfunction renderCoverageNote")]
assert "weekly reset" in _rn and _rn.index("weekly reset") < _rn.index("dropped"), "the window sentence leads; dropped counts trail"
assert '["Everything kept", ()=>[]]' in html and '["This reset", ()=>[0]]' in html and '["Last reset", ()=>[1]]' in html, "the three period presets"
for gone in ('"All season"', '"Last month"', '"Prev month"', '"Last 2 months"', "Custom weeks", 'id="f-weeksA"', "whole season", "pulseSpark(", "wkBag", "Last vs prev month"):
    assert gone not in html, f"{gone!r} must not survive the two-week window"
assert "if(w===OUTW) return false;" in html and "KEEP_BUCKETS=(Number.isFinite(+r)&&+r>0)?+r:2" in html, "rows past the window never pass, and the clamp fails closed on an old payload"
assert "const now=(aD&&!isNaN(+aD))?aD:wallNow;" in html and "Math.floor((wallNow-epoch)/36e5)" in html, "buckets on the builder's anchor (NaN-guarded); reset-age on the wall clock"
assert 'replace("{window}",retPhrase())' in html and "function retPhrase()" in html, "captions read the window from the payload"
assert "Compare needs two resets" in html, "compare refuses without a baseline inside the window"
assert "Rating is a Raider.IO season total" in html and "Avg Player Rating (season)" in html, "the one season-wide figure says so where it prints"
print("client      : sample banner; coverage note")
# 2026-09-17 item-level range filter (owner: "add a filter for ilevel as well"): the same
# shape as the key range, applied in rowPass through baseMasks (so every surface that
# filters rows inherits it), printed in the scope line, the frame scope and a scope chip
# whenever narrowed, parked and restored by the Archon replica, hidden on a payload
# without rows.ilvl, and honest about the unknown rows a narrowed range excludes.
assert 'id="ilo"' in html and 'id="ihi"' in html and 'id="ilvl-fill"' in html and 'id="ilvl-v"' in html, "item-level dual slider"
assert "il:il?true:(ilvlNarrowed()||gearOn())" in html and "if(m.il){const v=R.ilvl[i]; if(v<m.ilo||v>m.ihi) return false;}" in html, "rowPass applies the range only when narrowed or under the Gear axis; unknown (0) fails a narrowed range"
assert html.count("else if(ilvlNarrowed()) p.push(ilvlText());") == 2 and 'if(gearOn()) p.push(ilvlAText()+" (cohort A)");' in html and 'if(gearOn()) p.push(withB?ilvlAText()+" vs "+ilvlBText()+" (ghost)":ilvlAText()+" (cohort A)");' in html and '$("frame-scope").textContent=frameScope(true);' in html, "printed in the scope line (cohort A) and the frame scope (A vs B) under the Gear axis"
assert 'scopeChip(box,ilvlText()' in html and '" parses in this build with no item level cannot match)"' in html and '" here carry no item level and "' in html and '(gear?"sit in neither cohort":"are left out")' in html, "scope chip names the build; the slider hint names this selection (cohort wording under the Gear axis)"
assert "ilo:ILVL.min, ihi:ILVL.max," in html and "&& !ilvlNarrowed()" in html and "ilo:state.ilo, ihi:state.ihi," in html, "Archon parks, matches and snapshots the range"
assert "ibox.hidden=true;" in html, "hidden on a payload without rows.ilvl"
assert html.count('(ILVL.has?') >= 3 and "item level, dungeon, region" in html and "'the key, '+(ILVL.has?'item-level, ':'')" in html, "the bypassed/narrowing/fixed-cohort prose names item level (the disclaimer only when the page has it)"
assert "if(ILVL.has&&(DEF.ilo>ILVL.min||DEF.ihi<ILVL.max))" in html, "the trust-gate reference pool mirrors the item-level default"
assert '"ilvl": ilvl_arr' in _b, "the builder ships rows.ilvl"
print("client      : item-level range: dual slider under Key Level; rowPass gate; scope line + frame scope; chip + hint name the unknown count; Archon park/match/snap; hidden without rows.ilvl")

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
assert "function gearIlvls()" in html and "function gearMedian()" in html and "const gearQuantile=f=>" in html and "function gearSplitApply(announce)" in html and "function setGear(on)" in html and "function gearOverlapNote()" in html and "function gearCensus()" in html, "gear engine"
assert "const m=baseMasks(); m.il=false;" in html and "const x=R.ilvl[i]; if(x>0) v.push(x);" in html and "state.ilo=med; state.ihi=ILVL.max; state.ilob=ILVL.min; state.ihib=med-1;" in html and "if(below){ state.ilob=ILVL.min; state.ihib=state.ilo-1; }" in html and "else { state.ilob=state.ihi+1; state.ihib=ILVL.max; }" in html and "if(gearPrevA&&state.ilo===gearPrevA.setLo&&state.ihi===gearPrevA.setHi){" in html and "ilo:il?il.lo:state.ilo, ihi:il?il.hi:state.ihi};" in html, "the arithmetic: median over the un-narrowed selection, known item levels only, lower median; split and complement cohorts adjacent and disjoint; B's bounds ride the mask; leaving restores A only while the axis's own range still stands"
assert "gearPrevA=null;   // a fresh activation" in html, "a fresh activation never inherits a restore memory"
assert "at or above the selection's median item level at the split" in html and '"), B = "+ilvlBText()+" (below it)' in html and 'A kept at "+ilvlAText()+" (solid), B = "+ilvlBText()' in html and '" A, grey ghost)."' in html and "Gear compare needs item levels — this build carries none." in html and "cannot be split at its median item level" in html and "no parse in it sits below that" in html, "the notices say what was set, kept or refused"
assert "function aggregate(weeks,il){" in html and "const m=baseMasks(il), cut=periodCut(weeks);" in html and "aggregate(state.weeksA,{lo:state.ilob,hi:state.ihib})" in html, "B is period A aggregated over the B cohort"
assert "const cmpA=twoSided()||skillTab;" in html and "if(twoSided()) b=B?B.groups.get(key)||null:null;" in html and "b:(twoSided()&&FRAME_B)?(FRAME_B.groups.get(key)??null):null," in html and "const compare=twoSided()||skill;" in html and html.count("if(twoSided())") == 4, "every two-aggregation surface reads twoSided(), not state.compare: the join, the caption gate, the frame ctx and its three identity rows, the table"
assert '<b>Gear compare:</b> solid = item level A ("+ilvlSpan(state.ilo,state.ihi)+")' in html and "parses with no item level sit in neither cohort." in html and "Item level climbs with key level, so keep the key range narrow to read gear alone" in html and "A and B overlap on ilvl" in html, "period note: cohorts, unknowns, the key confound, overlap"
assert 'cap="Solid bar: item level A ("+ilvlSpan(state.ilo,state.ihi)+")' in html and 'scopeChip(box,"compare: gear vs "+ilvlBText(),"blockG")' in html and "'<b>A</b><br><span style=\"white-space:nowrap\">'+esc(ilvlAText())+'</span>'" in html and '" at item level A ("+ilvlSpan(state.ilo,state.ihi)+") vs B ("' in html, "caption, scope chip, tooltip header and table sub-line name the ranges"
assert "(gearOn()?'no B':'new')" in html and 'gearOn()?" · item-level cohort A":""' in html, "no time words under the Gear axis"
assert 'state.gear=("gear" in st)?!!st.gear:false;' in html and 'if("ilob" in st){state.ilob=st.ilob; state.ihib=st.ihib;}' in html and "gear:state.gear, ilob:state.ilob, ihib:state.ihib," in html and "&& state.merge && !state.compare && !state.gear" in html and "function setGear(on){\n  if(state.elite) return;" in html, "Archon parks the Gear axis and restores it with both cohorts; the axis cannot be turned on inside the replica"
assert '$("axis-gear-btn").style.display=ILVL.has?"":"none";' in html and "state.gear=false; state.ilob=0; state.ihib=0;" in html and "const any=state.compare||state.skill||state.gear;" in html and 'gear?(band?"Item Level A (solid)":"Item Level split"):"Item Level"' in html and '$("axis-gear-chip").textContent="B: "+ilvlBText();' in html, "hidden without rows.ilvl; opens off; gain/loss sorts, the A label and the B chip follow the axis"
# review follow-up (2026-09-18): the split is tried before the other axis is switched off; the
# degenerate case is "nothing below the median"; the restore memory rides the Archon snapshot;
# leaving the axis always says where A stands; the selection-scoped unknown count; shared
# characters; Pulse names the cohort; compare columns say Parses; the B slider is grey.
assert "const open=!ilvlNarrowed();" in html and "if(open&&!gearSplitApply(true)){ syncAxisUI(); return; }" in html, "a refused split leaves Time/Skill on"
assert "return {med:v[Math.floor((v.length-1)/2)], lo:v[0]};" in html and "if(r==null||r.lo>=med){" in html, "refused when no parse sits below the median"
assert "gearPrev:gearPrevA," in html and 'gearPrevA=("gearPrev" in st)?st.gearPrev:null;' in html, "the restore memory travels with the Archon snapshot"
assert 'the item-level range stays at "+(ilvlNarrowed()?ilvlAText():"any")' in html and '+(wasGear?" "+$("notice").textContent:""));' in html, "leaving the axis always speaks; the quick-compare chip keeps both lines"
assert "const gc=gearCensus(), aHigh=state.ilo>state.ihib, aLow=state.ihi<state.ilob;" in html and '" In this selection "+fmtInt(gc.unk)+" parses with no item level sit in neither cohort."' in html and '" fall between the two ranges and sit in neither cohort.</b>"' in html and '" <b>Median item level "+gc.ilA+" in A vs "+gc.ilB+" in B · median key +"+gc.kA+" in A vs +"+gc.kB+" in B.</b>"' in html and '"% per item level</b>"' in html and "Across the whole selection A's " in html and "the key gap above is inside every badge; <b>⚑ Hold key</b> pins both cohorts to one key level" in html, "the census: selection-scoped unknowns, the gap between the ranges, each cohort's median item level and key"
assert '["⚡ Median split",' in html and '["⚡ Quartile split",' in html and '["⇄ Swap A and B",' in html and "gearTrack(hi,ILVL.max); state.ilo=hi; state.ihi=ILVL.max; state.ilob=ILVL.min; state.ihib=lo;" in html, "quick chips: median split, quartile split, swap"
assert 'mil:(g.il.sort((a,b)=>a-b),g.il[Math.floor((g.il.length-1)/2)]),' in html and "if(ILVL.has) g.il.push(R.ilvl[i]);" in html and 'h+=row("Median item level", f(r.a,"mil",' in html and 'h+=row("Median key", f(r.a,"mkey",' in html, "the tooltip prints the spec's own cohort centres under Gear"
assert "thinB:!!(b&&b.chars<effMinB)});" in html and '">thin B</span>' in html, "a B side under the gate is badged, never hidden"
assert 'scopeChip(box,"compare: gear vs "+ilvlBText(),"blockG")' in html, "the gear scope chip jumps to the B slider"
assert "function gearSharedCharsNote(A,B)" in html and "charSet:charSeen," in html and "a character with parses in both item-level ranges counts in both columns" in html, "shared characters are said in the note and the tooltip"
assert '"sort by Gained most to rank specs by what they gain from gear."' in html and "sort by Lost most" in html and "the two item-level ranges stay put when other filters move" in html, "the sort advice follows which side is the higher gear; the split is a snapshot"
assert 'notes.push("item-level cohort A only ("+ilvlAText()+") in both windows — B is not on this board");' in html, "Pulse names the cohort"
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
assert 'ilBand:false,' in html and "function ilvlOneSays()" in html and "const band=state.ilBand||!ilvlOneSays();" in html and '$("ilvl-single").hidden=band; $("ilvl-band").hidden=!band;' in html and "is a band, and one thumb can only say" in html and "are not one split — one thumb can " in html, "the two-range form stays up whenever one thumb could not say the setting, and the toggle is refused with a reason rather than printing a cut nobody set"
assert html.count("state.ilBand=true;   //") == 3, "the quartile split and the swap show both ranges, and a refused toggle keeps the flag with the form, because one thumb cannot say any of them"
assert '(inA?"a":inB?"b":"")' in html and "' parses in this selection\"></i>'" in html and "thinner parses sit outside this span and are still reachable at the ends" in html, "the density strip colours A and B and says what sits beyond its ends"
assert '"· "+Math.round(shA.pct)+"% of the selection"' in html and '"scale "+w.lo+"–"+w.hi+", "' in html and '" thinner parses outside it (type a number to reach them)"' in html and '" here carry no item level and "' in html and '" ("+fmtInt(ILVL.unknown)+" parses in the whole build carry none)"' in html, "the readout is selection-scoped and the build-wide count stays in the title"
assert 'id="ilvl-num"' in html and "const numCommit=e=>{" in html and "numTimer=setTimeout(" in html and '$("ilvl-num").onchange=e=>{ clearTimeout(numTimer); numCommit(e); };' in html and "num.min=gear?ILVL.min+1:ILVL.min; num.max=ILVL.max;" in html, "a typed number lands exactly, reaches the levels the drawn scale trims, and does not re-filter the page on every keystroke"
# review follow-up (2026-09-19): the trim is visible, not only in a title; the share's denominator
# is named; the axis reports its clip per side over a CONTIGUOUS domain and counts the parses it
# cannot place; the confound is judged across every drawn bucket, not just the two ends.
assert "const fat=all.filter(v=>ilTot.get(v)>=pool*TREND_ILVL_SHARE);" in html and "for(let v=fat[0];v<=fat[fat.length-1];v++) if(ilsSeen.has(v)) buckets.push(v);" in html, "the item-level axis draws a contiguous span, so the x-axis cannot lie about distance"
assert "ilBelow+=ilTot.get(v);" in html and "ilAbove+=ilTot.get(v);" in html and '" parse"+(ilBelow===1?"":"s")+" below it (down to ilvl "' in html, "the clip is reported per side, never as a range that spans the drawn band"
assert "ilNoLvl++; continue; }" in html and '" no item level and cannot sit on this axis at all."' in html, "the axis counts the parses it cannot place"
assert "let kLo=Infinity, kHi=-Infinity;" in html and "const flat=isFinite(kLo)&&kLo===kHi;" in html and "at both ends but ranges +" in html and "in between, so part of the shape is key level, not gear." in html, "the key confound is judged across every drawn bucket"
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
assert "const TREND_ILVL_MINP = 15;" in html and "const TREND_ILVL_SHARE = 0.005;" in html and "TREND_CAP_ILVL" in html, "its point floor, domain share and caption"
assert 'const ilAxis=state.trendAxis==="ilvl"&&ILVL.has;' in html and "if(ilAxis&&!(R.ilvl[i]>0)){ ilNoLvl++; continue; }" in html, "the axis needs item levels, and a parse without one has no place on it (and is counted)"
assert "const fat=all.filter(v=>ilTot.get(v)>=pool*TREND_ILVL_SHARE);" in html and "if(fat.length<2) buckets=all;" in html and "ilClipped+=ilTot.get(v);" in html, "the domain is where the players are, and it fails open"
assert '" — the levels holding enough of this selection to read"' in html and '" are not drawn"' in html, "the caption says what the domain clipped"
assert '" Median key rises from +"+k0+" at ilvl "+b0+" to +"+k1+" at ilvl "+b1' in html and "part of every rise here is key level, not gear" in html, "the key-level confound is printed on the chart that invites it"
assert "const shTot=ilAxis?ilTot:keyAxis?kTot:(daily?dTot:wTot);" in html, "Share reads the item-level denominator"
assert '" The Gear compare axis is on, so this curve is item-level cohort A only ("' in html and '" The item-level range limits the band drawn ("+ilvlText()+")."' in html, "the axis says when the item-level filter or the Gear cohort clips the very band it plots"
assert "if(!keyAxis&&!ilAxis&&hasTune&&D.tuning&&D.tuning.date){" in html and "if(!keyAxis&&!ilAxis&&buckets.length){" in html, "tuning hairlines and outage spans are time-axis furniture"
assert '$("trendaxis-ilvl").style.display=ILVL.has?"":"none";' in html and 'if(state.trendAxis==="ilvl"&&!ILVL.has){ state.trendAxis="time";' in html, "hidden without item levels, and never left selected"
assert '"so the specs that gain most per item level come first"' in html and 'axis==="ilvl"?TREND_CAP_ILVL' in html and 'if(axis==="ilvl")' in html, "the slope sort, caption and span name the axis"
print("client      : Trajectory item-level axis: one point per item level; domain = the populated band, clip counted; per-point floor 15; key confound printed; Share/Rank/Retention inherit; hidden without rows.ilvl")

# the gear read's two honesty controls (2026-09-19, from the design panel): the note says what a
# point of item level is worth across the selection, and ⚑ Hold key pins both cohorts to one rung
# of the key ladder so the rest of the badge is gear — released explicitly, and on leaving the axis.
assert "dA:q(dpsA), dB:q(dpsB), kAll:med(kAll)" in html and "const q=a=>{ if(!a.length) return NaN;" in html, "the census carries each cohort's pooled read and the selection's median key"
assert 'state.keyHeld?"↺ Release key":"⚑ Hold key"' in html and "state.keyHeld={klo:state.klo, khi:state.khi};" in html and "state.klo=state.khi=gc.kAll;" in html, "Hold key pins the ladder and remembers what to give back"
assert '"A "+fmtInt(beforeA)+" → "+fmtInt(afterA)+" parses, B "+fmtInt(beforeB)+" → "+fmtInt(afterB)' in html, "the hold prints what it costs in sample"
assert '(state.keyHeld?" (held for the gear read)":"")' in html and 'if(state.keyHeld){   // the hold belongs to the gear read' in html, "a held key range is labelled, and never outlives the axis"
assert "function ilvlSpan(lo,hi)" in html and 'atHi?"≥ "+lo:atLo?"≤ "+hi:lo+"–"+hi' in html, "a floor is printed as a floor, not as a range against the payload's end"
print("client      : gear read: % per item level in the note; ⚑ Hold key pins both cohorts to one key level and says what it costs; floors print as ≥/≤")

print("client      : Gear compare axis: Off|Time|Skill|Gear XOR; B = period A over the B item-level cohort; median split / complement / swap; period note, caption, chip, tooltip, table, frame; Archon park+restore; hidden without rows.ilvl")
print("client      : retention note every build; presets Everything kept / This reset / Last reset; anchor-bucketed; no month presets, custom weeks, season sparkline or 'whole season' text")

print("\nPASS")
