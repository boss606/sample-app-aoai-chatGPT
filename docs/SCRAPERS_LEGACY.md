# Legacy Scrapers (Deprecated)

This document describes the old `scripts/scrapers/` pipeline that was removed. The current data ingestion runs through `scripts/data_ingestion/`, which contains equivalent scrapers. This documentation is preserved for reference in case the legacy API-based approach is needed.

---

## CourtListener (API by Court)

The legacy scrapers used the **CourtListener REST API** (`https://www.courtlistener.com/api/rest/v4/search/`) to fetch opinions per court with family-law search terms.

**Base API:** `https://www.courtlistener.com/api/rest/v4/`  
**Auth:** Optional `COURTLISTENER_TOKEN` env var for higher rate limits.

### Courts and Search URLs

| Court ID | Court Name | Search URL |
|----------|------------|------------|
| **scotus** | Supreme Court of the United States | `https://www.courtlistener.com/api/rest/v4/search/?type=o&court=scotus&q=marriage%20OR%20divorce%20OR%20custody%20OR%20visitation%20OR%20paternity%20OR%20%22child%20support%22%20OR%20%22spousal%20support%22%20OR%20%22Family%20Code%22` |
| **cal** | California Supreme Court | `https://www.courtlistener.com/api/rest/v4/search/?type=o&court=cal&q=marriage%20OR%20divorce%20OR%20custody%20OR%20visitation%20OR%20paternity%20OR%20%22child%20support%22%20OR%20%22spousal%20support%22%20OR%20%22Family%20Code%22%20OR%20%22Fam.%20Code%22` |
| **calctapp** | California Court of Appeal | `https://www.courtlistener.com/api/rest/v4/search/?type=o&court=calctapp&q=marriage%20OR%20divorce%20OR%20custody%20OR%20visitation%20OR%20paternity%20OR%20%22child%20support%22%20OR%20%22spousal%20support%22%20OR%20%22Family%20Code%22%20OR%20%22Fam.%20Code%22` |

**Opinion text:** Fetched from `/api/rest/v4/opinions/{id}/` (prefer `plain_text`; fallback to `html`).

**Output:** TXT files under `scripts/scrapers/downloads/courtlistener/` with prefixes `scotus_`, `cal_`, `calctapp_`.

---

## California Law (LegInfo TOC)

Legacy scrapers used the **California Legislative Information** site to scrape legal codes by Table of Contents.

**Base URL:** `https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml`

| Code | tocCode | URL |
|------|---------|-----|
| Family Code | FAM | `https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=FAM&tocTitle=+Family+Code+-+FAM` |
| Code of Civil Procedure | CCP | `https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=CCP&tocTitle=+Code+of+Civil+Procedure+-+CCP` |
| Civil Code | CIV | `https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=CIV&tocTitle=+Civil+Code+-+CIV` |
| Evidence Code | EVID | `https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=EVID&tocTitle=+Evidence+Code+-+EVID` |

**Output:** TXT files under `scripts/scrapers/downloads/california_law/` (e.g. `FAM_family_code.txt`, `CCP_code_of_civil_procedure.txt`).

---

## California Rules of Court (PDF)

**URL:** `https://courts.ca.gov/rules-forms/rules-court`  
**Method:** Fetched master listing, downloaded Title/Appendix PDFs, extracted text via Azure Form Recognizer, concatenated.

**Output:** `downloads/rules_of_court/crc_rules_of_court.txt`

---

## Court Forms (Self-Help)

**Base URL:** `https://selfhelp.courts.ca.gov/find-forms`

| Category | Query | URL |
|----------|-------|-----|
| Parentage | parentage | `https://selfhelp.courts.ca.gov/find-forms?query=parentage` |
| Domestic Violence | domestic violence | `https://selfhelp.courts.ca.gov/find-forms?query=domestic%20violence` |
| Divorce | divorce | `https://selfhelp.courts.ca.gov/find-forms?query=divorce` |
| Child Custody | custody | `https://selfhelp.courts.ca.gov/find-forms?query=custody` |
| Child Support | child support | `https://selfhelp.courts.ca.gov/find-forms?query=child%20support` |

**Output:** TXT files under `scripts/scrapers/downloads/court_forms/` per category.

---

## CourtListener Bulk (Alternative Approach)

The **bulk** approach downloads full CourtListener datasets from `https://storage.courtlistener.com/bulk-data/` (e.g. `opinions-YYYY-MM-DD.csv.bz2`) and filters by court/family law terms. This is the approach used by `scripts/data_ingestion/shared/courtlistener_bulk.py` and `run_to_json.py`.

**Raw root:** `downloads/courtlistener_bulk/teste/raw/<court>/<dataset>/`

---

## Current Replacement

All of the above functionality has been migrated to `scripts/data_ingestion/`:

- **California Law:** `scripts/data_ingestion/california/california_law/`
- **Court Forms:** `scripts/data_ingestion/california/court_forms/`
- **Rules of Court:** `scripts/data_ingestion/california/california_rules_of_court/`
- **CourtListener (bulk):** `scripts/data_ingestion/california/courtlistener/` and `scripts/data_ingestion/shared/courtlistener_bulk.py`

Run the ingestion via `run_*.py` scripts in each module.
