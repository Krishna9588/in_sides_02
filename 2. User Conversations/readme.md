#### Architecture
```
   Data Flow: Scraper → Extractor → Normalizer → Analyzer → Output
                                                  ↓
                                          (standalone mode)
```

#### Output Format
```json
    {
      "extraction_metadata": {
        "source": "Platform",
        "extracted_at": "2026-04-20T...",
        "extraction_version": "v3.0",
        "fields_extracted": 45,
        "data_completeness": 0.95,
        "extraction_time_ms": 5432,
        "errors": []
      },
      "raw_data": {},
      "extracted_data": {},
      "analysis": {}
    }
```

#### CLI Examples
```bash
# Extract + Analyze
python app_store_v3.py -u "Instagram" --deep-extract --analyze

# Analyze pre-scraped data
python app_store_v3.py --analyze-only data/instagram_extracted.json

# Bulk processing
python play_store_v3.py --bulk apps.txt --analyze

# Deep Reddit analysis
python reddit_v3.py -u "r/python" --deep-extract --days 90

# Full YouTube extraction
python youtube_v3.py -u "@channel" --extract-all --analyze

# Standalone analyzer
python analyzer_v3.py --input extracted/ --mode comprehensive
```