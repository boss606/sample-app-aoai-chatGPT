# California Rules of Court scrapers

- Base: `rules_scraper.py` holds the PDF scraping/cleaning logic.
- Runner: `scrape_rules.py` imports/calls the base directly (no CLI/argparse).
- Local output: `scripts/scrapers/downloads/rules_of_court/` (e.g., `crc_rules_of_court.txt`).

Typical use (Python):
```python
from scripts.scrapers.california_rules_of_court.scrape_rules import run_rules_pdf
run_rules_pdf()
```

Notes:
- No auth token required; plain HTTP + Form Recognizer for PDF text.
- Upload: `run_rules_pdf` defaults to `upload=True` and will use `LegalDocsStorage` (needs storage envs). To skip blob, call `run_rules_pdf(upload=False)`.
- To change destination, pass `out_path` to `run_rules_pdf`.
