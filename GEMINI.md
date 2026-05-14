# HakfaFHLDictDataMirror Instructions

## Project Overview
This project is a data mirror for the [台語信望愛客語辭典 (Hak-fa FHL Dictionary)](https://hakka.fhl.net/dict/index_hakka.html). It captures and structures Hak-fa dictionary data for archival and direct access.

### Core Technologies
- **Python 3**: Scraper script using standard libraries (`urllib`, `html.parser`, `csv`, `json`).
- **Data Formats**: CSV and JSON.
- **Hosting**: Designed for GitHub Pages (includes `.nojekyll` and `index.md`).

## Project Architecture
- `public/`: The data store.
  - `manifest.json`: Tracks the `latest_version` and all available versions.
  - `{version_id}/bunji/`: Contains the actual dictionary data (CSV/JSON) for a specific run.
- `script/`: Automation tools.
  - `scraper.py`: The main script to fetch data, generate JSON, and update the manifest.
- `index.md`: Homepage for GitHub Pages.
- `README.md`: Public-facing documentation.

## Development Workflows

### Running the Scraper
To perform a new data mirror run:
```bash
python3 script/scraper.py
```
**Behavior:**
1. Generates a new `version_id` based on the current timestamp (YYYYMMDD-HHMM).
2. Iterates through the database IDs (1 to ~23,500).
3. Fetches each entry in both **Numeric Tone** and **Unicode Diacritic** formats.
4. Saves to `public/{version_id}/bunji/HakfaFHLDict.csv`.
5. Generates a JSON version of the same data.
6. Updates `public/manifest.json` with the new version.

### Data Conventions
- **Roman Orthography**: Always use the term "Roman Orthography".
- **Orthography Formats**:
  - `Hakfa_Numeric`: Uses traditional 長老教會 PFS tone numbers 1~6 (e.g., `mi3` = `mí`, PFS 3).
  - `Hakfa_Unicode`: Uses Unicode diacritics for tones (e.g., `mí`).
- **Tone numbering**: PFS 1~6, canonical example set `sî sì sí si se̍k sek`. **Not** POJ 1–8 — the digit↔diacritic correspondence differs. The scraper converts FHL's POJ-style digits to PFS 1~6 on capture (`fhl_numeric_to_pfs_numeric` in `script/scraper.py`).
- **Language**: Refer to the language as **Taigi** (for the Taigi explanations) and **Hak-fa** (for the main entries).

## Key Files
- `script/scraper.py`: The engine of the project. It handles rate-limiting, retries, and data mapping.
- `public/manifest.json`: The source of truth for the latest mirrored data.
- `README.md`: Contains the schema definition for the columns.
