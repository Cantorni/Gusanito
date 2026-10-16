# Gusanito

Early detection of fall armyworm reservoirs: extension officers photograph green dry-season patches, the server analyses each photo with Claude and keeps the result for review.

```
frontend/   PWA (vanilla JS): map, photo report, receiver page, service worker, offline map
backend/    Azure Functions app (report, analyze-point, maps-token) + local dev server
```

## Run locally
```sh
cd backend && pnpm install
cp .env.example .env      # set ANTHROPIC_API_KEY and REPORT_TOKEN
node server.js            # http://127.0.0.1:8787 (serves ../frontend and the API)
```
The offline map is not committed: from `frontend/` run `scripts/make-tiles.sh` (needs the `pmtiles` CLI).

## Backend notes (for deployment)
- Functions: `backend/report`, `backend/analyze-point`, `backend/maps-token` (v3 model, `function.json` per route, `host.json`).
- App settings: `ANTHROPIC_API_KEY`, `REPORT_TOKEN`, optional `CLAUDE_MODEL`, `DATA_DIR`.
- `report` stores photos and results on local disk (`DATA_DIR`). Replace with Blob/Table storage when deployed.
- The frontend calls relative `/api/...` paths.
