#!/bin/sh
# Run from frontend/. Regenerates the offline Zambia map (needs the `pmtiles` CLI: github.com/protomaps/go-pmtiles).
# Raise --maxzoom for more detail (z12 ~54 MB). Uses a Protomaps daily OSM build; change the date if it 404s.
pmtiles extract "https://build.protomaps.com/$(date -d yesterday +%Y%m%d).pmtiles" zambia.pmtiles \
  --bbox=21.99,-18.08,33.71,-8.22 --maxzoom=12
