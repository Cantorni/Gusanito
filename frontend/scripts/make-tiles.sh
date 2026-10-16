#!/bin/sh
# Run from frontend/. Regenerates the offline Zambia map (needs the `pmtiles` CLI: github.com/protomaps/go-pmtiles).
# Raise --maxzoom for more detail (z12 ~54 MB). Uses a Protomaps daily OSM build; change the date if it 404s.
# `date -d` is GNU (Linux); macOS/BSD date needs `-v-1d`.
day=$(date -u -d yesterday +%Y%m%d 2>/dev/null || date -u -v-1d +%Y%m%d)
pmtiles extract "https://build.protomaps.com/$day.pmtiles" zambia.pmtiles \
  --bbox=21.99,-18.08,33.71,-8.22 --maxzoom=12
