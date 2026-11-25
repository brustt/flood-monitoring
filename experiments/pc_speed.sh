#!/usr/bin/env bash
# Download throughput from Planetary Computer (sentinel-1-rtc COGs), 1 stream then N in parallel (issue #42).
# Whole-file downloads to /dev/null: the link's ceiling, not the ranged 512 px block reads of the load stage
# (experiments/pc_throughput.py measures those, but needs the project's environment). This needs only curl, jq and
# bc, so it runs on a bare machine such as the VPS. Measured 2026-11-25: home 5.2 / 4.8 MB/s, OVH VPS 42.2 / 48.6 MB/s.
# usage: pc_speed.sh [seconds per test, default 30] [parallel streams, default 8]
set -uo pipefail
T=${1:-30}
P=${2:-8}
STAC=https://planetarycomputer.microsoft.com/api/stac/v1
for tool in curl jq bc; do command -v $tool >/dev/null || { echo "missing: $tool (sudo apt install -y $tool)"; exit 1; }; done

# Some vv COGs of relative orbit 8 over the Charente
hrefs=($(curl -sf -X POST "$STAC/search" -H 'Content-Type: application/json' -d '{
  "collections": ["sentinel-1-rtc"], "bbox": [-1.2, 45.5, 0.5, 46.2],
  "datetime": "2026-01-01/2026-03-01", "limit": '"$P"',
  "query": {"sat:relative_orbit": {"eq": 8}}}' | jq -r '.features[].assets.vv.href'))
[ ${#hrefs[@]} -gt 0 ] || { echo "STAC search returned no items"; exit 1; }
token=$(curl -sf https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc | jq -r .token)
echo "${#hrefs[@]} COGs found; two tests of ${T}s, about $((2 * T + 5))s in total"

# Bytes/s of one download stopped after T seconds
fetch() { curl -s -o /dev/null --connect-timeout 10 --max-time "$T" -w '%{speed_download}\n' "$1?$token"; }

echo "testing 1 stream..."
one=$(fetch "${hrefs[0]}")
printf '1 stream:   %6.1f MB/s\n' "$(echo "$one / 1000000" | bc -l)"

echo "testing $P streams..."
total=0
for s in $(for i in $(seq 0 $((P - 1))); do fetch "${hrefs[$((i % ${#hrefs[@]}))]}" & done; wait); do
  total=$(echo "$total + $s" | bc -l)
done
printf '%d streams: %6.1f MB/s\n' "$P" "$(echo "$total / 1000000" | bc -l)"
