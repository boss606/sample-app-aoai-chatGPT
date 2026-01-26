# California Law scrapers (Codes)

- Base: `code_scraper.py` implements common scraping logic (TOC -> divisions -> parts/chapters/articles -> text).
- Derived scrapers:
  - `scrape_family_code.py` (Family Code)
  - `scrape_civil_code.py` (Civil Code)
  - `scrape_code_of_civil_procedure.py` (Code of Civil Procedure)
  - `scrape_evidence_code.py` (Evidence Code)

Typical use (Python, no CLI/argparse):
```python
from scripts.scrapers.california_law.scrape_family_code import run_family_code
run_family_code()  # saves locally and uploads if storage is configured
```

Local output: `scripts/scrapers/downloads/california_law/`, with prefixes defined per scraper (e.g., `FAM_`).

Notes:
- Blob upload requires `LegalDocsStorage`; otherwise, it only saves locally.
- HTTP calls have retry/backoff; tweak in `code_scraper.py` if needed.
