# CourtListener scrapers (no CLI)

- Base: `CourtListenerOpinionScraper` in `courtlistener_scraper.py` (usa `COURTLISTENER_TOKEN` se estiver setado). Salva em `scripts/scrapers/downloads/courtlistener/`.
- Derived helpers:
  - `run_scotus()` from `scrape_scotus.py`
  - `run_cal_supreme()` from `scrape_cal_supreme.py`
  - `run_cal_ctapp()` from `scrape_cal_ctapp.py`
- Each uses the provided search URL (Court ID + query) and saves TXT files under `scripts/scrapers/downloads/courtlistener/` using filenames prefixed with the court_id (no timestamp).
- Example (Python):\
  `from scripts.scrapers.courtlistener.scrape_scotus import run_scotus`\
  `run_scotus(max_results=5)`  # saves locally

