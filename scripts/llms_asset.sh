#!/usr/bin/env bash
# The LLM export as a Release asset: pack it (llms.yml) and unpack it into the
# site (refresh.yml). partitioned_payload.md section 5 / section 10 PR-1 stage A.
#
#   llms_asset.sh pack <out.tar.gz>     tar site/{llms.txt,llms,robots.txt,
#                                        sitemap.xml} plus a build stamp
#   llms_asset.sh [unpack]              refresh side, see below
#
# UNPACK is best-effort at every step and ALWAYS exits 0 -- a data problem must
# never stop the site publishing -- but it is also SELF-HEALING: a deploy can
# never drop the /llms/ tree the previous deploy carried. Precedence:
#
#   1. a `fresh` drain run with a cached tarball unpacks that without a
#      download, so the latency-critical path is never slower than before;
#   2. otherwise download the Release asset with a HARD cap (30 s) into the
#      cached data/processed, so a fresh runner still holds the last tarball;
#   3. a slow, missing or corrupt asset falls back to the cached tarball;
#   4. NO tarball at all (first run after this landed, a cache-evicted runner
#      during a Release outage) builds the export inline ONCE with
#      `build_site_data.py --llms-only` (~50-150 s today; it is O(season) --
#      the whole-journal tier pass -- so it runs under `timeout` at
#      LLMS_BUILD_MAX_S = 300 s) and packs the result into the cache, so the
#      next run is back to a plain unpack;
#   5. only when that build fails or times out does the deploy go out
#      without llms/ (any half-written tree removed), with a ::warning:: --
#      never red, never a build that can hold the 20-minute cycle hostage.
#
# Health lines appended to <dir>/build_health.txt for every site dir:
#   llms.unpack=fresh|cached|stale|built|none   how the tree got here
#   llms.built=<UTC stamp>|unknown              when the DATA was built
#   llms.age_h=<hours>                          age of that build now
#   llms.files=<n>                              files in llms/
# `fresh` means the download succeeded, NOT that the data is new: read
# llms.built for that. An export older than LLMS_STALE_WARN_H hours (36) is a
# ::warning:: -- that is llms.yml having failed for a day, silently otherwise.
#
# Everything is overridable through the environment so a test can drive it:
#   LLMS_TAR         cached tarball        data/processed/llms.tar.gz
#   LLMS_URL         Release asset URL     https://github.com/$GITHUB_REPOSITORY/releases/download/llms/llms.tar.gz
#   LLMS_MODE        refresh `mode` input  "" ("fresh" skips the download when cached)
#   LLMS_SITE_DIRS   dirs to unpack into   "site docs" (the first is packed from)
#   LLMS_BUILD_CMD   inline build          "python -u scripts/build_site_data.py --llms-only"
#   LLMS_BUILD_MAX_S inline build cap      300 (coreutils timeout; rc 124)
#   LLMS_CURL_MAX_S  download cap          30
#   LLMS_STALE_WARN_H                      36
set -u

TAR="${LLMS_TAR:-data/processed/llms.tar.gz}"
URL="${LLMS_URL:-https://github.com/${GITHUB_REPOSITORY:-st331/wowlogs}/releases/download/llms/llms.tar.gz}"
MODE="${LLMS_MODE:-}"
DIRS="${LLMS_SITE_DIRS:-site docs}"
BUILD_CMD="${LLMS_BUILD_CMD:-python -u scripts/build_site_data.py --llms-only}"
CURL_MAX_S="${LLMS_CURL_MAX_S:-30}"
BUILD_MAX_S="${LLMS_BUILD_MAX_S:-300}"
STALE_WARN_H="${LLMS_STALE_WARN_H:-36}"
PRIMARY="${DIRS%% *}"
STAMP=llms.built          # tarball root -> <dir>/llms.built next to llms.txt

# The ONE list of what the asset carries. robots.txt and sitemap.xml are
# written by build_llms (the sitemap names every llms/ page) and travel with
# the tree so what is served is always one consistent build.
pack() {                  # pack <src dir> <out.tar.gz>
  local src="$1" out="$2"
  test -f "$src/llms.txt" && test -d "$src/llms" || { echo "pack: $src has no llms.txt + llms/"; return 1; }
  date -u +%Y-%m-%dT%H:%M:%SZ > "$src/$STAMP"
  tar -C "$src" -czf "$out.tmp" llms.txt llms robots.txt sitemap.xml "$STAMP" \
    && mv -f "$out.tmp" "$out"
}

if [ "${1:-unpack}" = "pack" ]; then
  pack "$PRIMARY" "${2:?usage: llms_asset.sh pack <out.tar.gz>}"
  exit $?
fi

# ---- unpack -----------------------------------------------------------------
mkdir -p "$(dirname "$TAR")"
STATE=stale
if [ "$MODE" = "fresh" ] && [ -f "$TAR" ]; then
  # A drain `fresh` run exists to publish the present in minutes; it takes
  # whatever tarball the cache holds and never waits on a download.
  echo "llms: fresh run, unpacking the cached tarball without a download"
  STATE=cached
elif curl -sfL -m "$CURL_MAX_S" -o "$TAR.tmp" "$URL" && tar -tzf "$TAR.tmp" >/dev/null 2>&1; then
  mv -f "$TAR.tmp" "$TAR"; STATE=fresh
else
  rm -f "$TAR.tmp"
  echo "llms: asset not fetched within ${CURL_MAX_S} s; using the cached tarball if there is one"
fi

unpack_all() {            # the cached tarball -> every site dir; false if it is unreadable
  local d
  for d in $DIRS; do
    rm -rf "$d/llms" "$d/llms.txt" "$d/$STAMP"
    mkdir -p "$d"
    tar -xzf "$TAR" -C "$d" || return 1
  done
}

if [ -f "$TAR" ] && ! unpack_all; then
  echo "llms: cached tarball is unreadable; discarding it"
  rm -f "$TAR"
fi

if [ ! -f "$TAR" ]; then
  # SELF-HEAL. Nothing to unpack means the tree that was live a deploy ago
  # would vanish from this one; build it here instead, once. The result is
  # packed into the cache so every later run is a plain unpack again, and
  # the daily llms.yml asset replaces it the next time a download succeeds.
  echo "::warning::no LLM export available (no Release asset, no cached tarball); building it inline once (~1-3 min, capped at ${BUILD_MAX_S} s) so llms/ stays in this deploy"
  T0=$(date +%s)
  # The inline build is O(season) with no bound of its own; `timeout` gives
  # it one (rc 124 past the cap). Without coreutils timeout it runs bare.
  if command -v timeout >/dev/null 2>&1; then
    timeout "$BUILD_MAX_S" $BUILD_CMD; RC=$?
  else
    $BUILD_CMD; RC=$?
  fi
  if [ "$RC" -eq 0 ] && pack "$PRIMARY" "$TAR" && unpack_all; then
    STATE=built
    echo "llms: built inline in $(( $(date +%s) - T0 )) s and cached as $TAR"
  else
    STATE=none
    rm -f "$TAR" "$TAR.tmp"
    for d in $DIRS; do rm -rf "$d/llms" "$d/llms.txt" "$d/$STAMP"; done   # no half tree
    if [ "$RC" -eq 124 ]; then
      echo "::warning::inline LLM build exceeded ${BUILD_MAX_S} s and was stopped; llms/ is not in this deploy (the daily llms.yml owns the O(season) build)"
    else
      echo "::warning::inline LLM build failed as well; llms/ is not in this deploy (see the log above)"
    fi
  fi
fi

# ---- health -----------------------------------------------------------------
BUILT=unknown; AGE_H=-1
if [ "$STATE" != "none" ]; then
  if [ -s "$PRIMARY/$STAMP" ]; then
    BUILT=$(tr -d '[:space:]' < "$PRIMARY/$STAMP")
    B=$(date -u -d "$BUILT" +%s 2>/dev/null) && AGE_H=$(( ($(date +%s) - B) / 3600 ))
  else
    # a tarball packed before the stamp existed: the file's own clock is the
    # best available answer
    BUILT=$(date -u -r "$TAR" +%Y-%m-%dT%H:%M:%SZ)
    AGE_H=$(( ($(date +%s) - $(date -r "$TAR" +%s)) / 3600 ))
  fi
  if [ "$AGE_H" -gt "$STALE_WARN_H" ]; then
    echo "::warning::LLM export is ${AGE_H} h old (built ${BUILT}); llms.yml has not published a newer asset -- check its last runs"
  fi
fi
FILES=$(ls "$PRIMARY/llms" 2>/dev/null | wc -l | tr -d ' ')
echo "llms: ${STATE} -- ${FILES} files, built ${BUILT} (${AGE_H} h ago)"
for d in $DIRS; do
  mkdir -p "$d"
  {
    echo "llms.unpack=$STATE"
    echo "llms.built=$BUILT"
    echo "llms.age_h=$AGE_H"
    echo "llms.files=$FILES"
  } >> "$d/build_health.txt"
done
exit 0
