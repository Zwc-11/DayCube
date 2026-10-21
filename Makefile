DATE ?= 2026-10-20
.PHONY: setup api viewer demo index ingest test eval capture schemas
setup:
	npm run setup
api:
	npm run api
viewer:
	npm run dev
demo:
	npm run demo
index:
	uv run daycube index --date $(DATE)
ingest: index
schemas:
	npm run schemas
test:
	npm run test:api
eval:
	uv run python eval/score_retrieval.py
capture:
	@echo "See services/capture-win/README.md; capture is not enabled by setup."
