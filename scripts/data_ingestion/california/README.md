# California Data Ingestion

Each script runs one source. Documents are saved locally and uploaded to `legal-docs-raw`.
From the project root.

## California Law (codes)

One file per code. Run each separately.

```bash
python scripts/data_ingestion/california/california_law/run_family_code.py
python scripts/data_ingestion/california/california_law/run_civil_code.py
python scripts/data_ingestion/california/california_law/run_code_of_civil_procedure.py
python scripts/data_ingestion/california/california_law/run_evidence_code.py
```

Output: `scripts/data_ingestion/california/california_law/output/california_law/*.json`

## California Rules of Court

Requires Azure Document Intelligence (Form Recognizer) for PDF extraction. Add to `.env`:
```
FORM_RECOGNIZER_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com/
FORM_RECOGNIZER_KEY=<your-key>
```

```bash
python scripts/data_ingestion/california/california_rules_of_court/run_to_json.py
```

Output: `scripts/data_ingestion/california/california_rules_of_court/output/`

## Court Forms

One file per category. Run each separately.

```bash
python scripts/data_ingestion/california/court_forms/run_child_custody.py
python scripts/data_ingestion/california/court_forms/run_child_support.py
python scripts/data_ingestion/california/court_forms/run_divorce.py
python scripts/data_ingestion/california/court_forms/run_domestic_violence.py
python scripts/data_ingestion/california/court_forms/run_parentage.py
```

Output: `scripts/data_ingestion/california/court_forms/output/court_forms/*.json`

## CourtListener (bulk family law opinions)

Use `--limit N` for quick tests.

```bash
python scripts/data_ingestion/california/courtlistener/run_to_json.py
python scripts/data_ingestion/california/courtlistener/run_to_json.py --limit 50
```

Output: `scripts/data_ingestion/california/courtlistener/output/`
