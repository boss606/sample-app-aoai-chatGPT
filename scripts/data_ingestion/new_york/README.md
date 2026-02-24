# New York Data Ingestion

## CourtListener (bulk family law opinions)

Use `--limit N` for quick tests.

```bash
python scripts/data_ingestion/new_york/courtlistener/run_to_json.py
python scripts/data_ingestion/new_york/courtlistener/run_to_json.py --limit 50
```

Output: `scripts/data_ingestion/new_york/courtlistener/output/`
