# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Data mirror of the [台語信望愛客語辭典 (Hak-fa FHL Dictionary)](https://hakka.fhl.net/dict/index_hakka.html). Scrapes ~23,500 dictionary entries in both numeric-tone and Unicode-diacritic PFS (Pha̍k-fa-sṳ) formats, generates IPA, and publishes versioned CSV/JSON to GitHub Pages.

## Commands

```bash
# Run scraper (takes hours — 23,500 IDs × 2 HTTP requests each)
python3 script/scraper.py

# Test IPA conversion
python3 test_ipa.py
```

No external dependencies — Python 3 stdlib only.

## Architecture

- `script/scraper.py` — single-file scraper, IPA converter, and manifest updater
- `public/manifest.json` — source of truth for latest version
- Each scraper run creates a new timestamped version directory (YYYYMMDD-HHMM) with two siblings:
  - `public/{version_id}/tangloo/HakfaFHLDict.csv` — **raw FHL archive** (no conversion): `ID, FHL_DICT_Numeric, FHL_DICT_Unicode, Hanzi, Tai-gi, Hakfa-exp, Hua-gi, Eng-gi`
  - `public/{version_id}/bunji/HakfaFHLDict.csv` — **derived output**: original FHL + PFS (numeric+Unicode) + IPA
- CSV is generated first, then converted to JSON for each output
- The scraper fetches each ID twice: `graph=0` (numeric tones) and `graph=2` (Unicode diacritics)
- DNS for `hakka.fhl.net` is overridden to the Google Cloud origin IP (`35.221.176.32`) to bypass an nginx 403 returned via Cloudflare's HK edge for `/dict/*`. SNI and cert verification still go via `hakka.fhl.net` (Let's Encrypt `*.fhl.net`).

## Terminology

- Use **Hak-fa** (not "Hakka") in prose; use **Hakfa** (no hyphen) in identifiers and filenames
- Use **Si-yen** (not "Sixian") for 四縣
- Use **Taigi** (not "Taiwanese" or "Hokkien") for the Taigi language
- Use **Roman Orthography** (not "Romanization")

## Tone System (Critical)

The `Hakfa_Numeric` column stores **PFS tone numbers 1~6** (converted from the FHL site's raw POJ-style digits by the scraper). The scraper's `fhl_numeric_to_pfs_numeric()` handles the conversion; see the FHL→PFS digit map below.

| PFS # | Diacritic | Example | IPA (Chao) | FHL raw digit | KPPY # |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | ˆ circumflex | sî | ˨˦ | 5 | 1 |
| 2 | ` grave | sì | ˩˩ | 3 | 2 |
| 3 | ´ acute | sí | ˧˩ | 2 | 3 |
| 4 | (none) | si | ˥˥ | 1 (omitted) | 4 |
| 5 | ̍ vertical line + stop | se̍k | ˥ | 8 | 5 |
| 6 | (none) + stop | sek | ˨ | 4 (omitted) | 6 |

## Tone-Mark Placement (Critical)

The FHL dictionary's `graph=2` (Unicode) column does **not** follow standard POJ tone-mark placement. We re-place per the canonical POJ rule below for the `PFS_Unicode` column in `bunji/`; `tangloo/`'s `FHL_DICT_Unicode` stays raw (FHL's own placement) for traceability.

The `bunji/` pipeline also normalizes KPPY-style u-onglide rimes (`ua`, `uai`, `uan`, `uang`, `uat`, `uak`, `ue`, `uen`, `uet`) to their canonical PFS `o`-onglide form (`oa`, `oai`, `oan`, `oang`, `oat`, `oak`, `oe`, `oen`, `oet`). FHL occasionally spells these KPPY-style; canonical PFS uniformly writes the rising medial as `oV`. Genuine PFS u-rimes (`ui`, `un`, `ung`, `uk`, `ut`) are phonemically distinct and untouched. See `normalize_kppy_uv_onglide_to_pfs()` in `script/scraper.py`.

### Placement rule (used for `bunji/` PFS_Unicode)

Borrowed verbatim from canonical POJ Section 21 (see [Taigibun/taigibun-agent-skills · linguistic_rules.md](https://github.com/Taigibun/taigibun-agent-skills/blob/main/taigi-roman-orthography-converter/references/linguistic_rules.md)), applied letter-by-letter to Hak-fa syllables:

1. **Single vowel:** Mark the vowel.
2. **No vowel:** Mark the nasal (`m`, `n`, `ng` — treat `ng` as 1 unit).
3. **Compound vowels:** Mark the **2nd letter from the right** (treating `ng` as 1 unit).
   - **Exception 1:** If 2nd from right is `i` → mark 1st (rightmost) letter instead.
   - **Exception 2:** Checked syllable + 2nd from right is `i`/`u` (but not `iu` / `iu`+stop) → mark 3rd letter.
   - **Special:** `iu`+stop → mark 2nd from right (no exception).

Hak-fa note: ⁿ does not occur in Hak-fa Si-yen, so the "skip ⁿ" clause is moot. `ṳ` (numeric `ii`) is treated as a plain `u` for placement; the trema-below (U+0324) is preserved under the tone diacritic via NFC stacking (`u` + U+0324 + tone-combining → e.g. `ṳ̂`). Checked codas in Hak-fa are `-p`/`-t`/`-k` (no `-h`).

### Worked examples

| Syllable | Letters (ng as 1) | 2nd-from-right | Rule | Marked |
|:---|:---|:---|:---|:---|
| `khua`  | k,h,u,a       | u | Rule 3 default | `khúa` |
| `koai`  | k,o,a,i       | a | Rule 3 default | `koái` |
| `kuai`  | k,u,a,i       | a | Rule 3 default | `kuái` |
| `khoan` | k,h,o,a,n     | a | Rule 3 default | `khoán` |
| `koet`  | k,o,e,t       | e | Rule 3 default | `koét` / `koe̍t` |
| `liang` | l,i,a,**ng**  | a | Rule 3 default | `liâng` |
| `siong` | s,i,o,**ng**  | o | Rule 3 default | `siông` |
| `sia`   | s,i,a         | i | Exception 1 → rightmost | `siá` |
| `liu`   | l,i,u         | i | Exception 1 → rightmost | `liù` |
| `kui`   | k,u,i         | u | Rule 3 default | `kúi` |
| `liuk`  | l,i,u,k       | u | Special (`iu`+stop) → 2nd-from-right | `liu̍k` |
| `ng`    | ng            | — | Rule 2 (no vowel) | `ǹg` (mark on n) |
| `sṳ`    | s,u (+U+0324) | u | Rule 1 (single vowel) | `sṳ̂` (NFC stack) |

### FHL's actual rule (empirical, ~30,420 marked syllables)

> Mark the **first vowel** of the vowel cluster, but skip a leading `i` if another vowel follows (treat it as a medial glide). Syllabic `ng` → mark `n`. The trema-below in `ṳ` is preserved across tone marks (FHL stores `sṳ̂` as NFC `s` + `ṳ` (U+1E73) + `̂` (U+0302), which NFD-decomposes to `s u U+0324 U+0302`).

This is roughly "2nd-from-right of the vowel cluster" — but loses information when a final consonant pushes the actual 2nd-from-right *letter* further left. That is why FHL and canonical POJ agree on open syllables (`khúa`, `kôa`, `liù`) but diverge on closed ones (`khoan`, `koet`).

### Divergence cells (where FHL needs correction)

| Cluster | Correct position | FHL position | Example | After correction | Volume |
|:---:|:---:|:---:|:---|:---|:---:|
| `oai`        | `a` (2nd of 3)  | `o` (1st)        | `kóai`    | `koái`    | 16 |
| `uai`        | `a` (2nd of 3)  | `u` (1st)        | `kúai`    | `kuái`    | 1 |
| `oa`+coda    | `a` (2nd letter from right) | `o` (1st vowel) | `khòan`, `khôang` | `khoàn`, `khoâng` | 31 |
| `oe`+coda    | `e` (2nd letter from right) | `o` (1st vowel) | `ko̍et`   | `koe̍t`  | 2 |
| `ua`+coda    | `a` (2nd letter from right) | `u` (1st vowel) | `kûan`, `ngùan` | `kuân`, `nguàn` | 2 |

Total: **52 syllables** across the corpus need repositioning. All other clusters — including `khúa`, `khôa`, `koa`, `liù`, `kúi`, `siá`, syllabic `ng`, and `ṳ`-bearing syllables — already match the canonical placement.

The `_reposition_syllable()` function in `script/scraper.py` defensively restores `ṳ` (trema-below) from numeric `ii` if it ever finds a mismatch; on current FHL data this is a no-op (FHL already preserves the trema).

## Data Schema

### `tangloo/HakfaFHLDict.csv` (raw archive)

| Column | Description |
|---|---|
| `ID` | FHL database ID |
| `FHL_DICT_Numeric` | Original FHL `graph=0` (POJ-style digits 1–8), no conversion |
| `FHL_DICT_Unicode` | Original FHL `graph=2` (Unicode diacritics), no conversion |
| `Hanzi` | 漢字 |
| `Tai-gi` | 台語解說 |
| `Hakfa-exp` | 客語解說 |
| `Hua-gi` | 華語解說 |
| `Eng-gi` | 英語解說 |

### `bunji/HakfaFHLDict.csv` (derived)

| Column | Description |
|---|---|
| `ID` | FHL database ID |
| `FHL_DICT_Numeric` | Original FHL format (POJ-style digits 1–8) — kept for traceability |
| `PFS_Numeric` | PFS 1~6 numeric (derived from `FHL_DICT_Numeric`) |
| `PFS_Unicode` | PFS Unicode diacritics with **POJ-corrected** tone-mark placement and restored `ṳ` (differs from `FHL_DICT_Unicode` for `ua`/`oai`/`uai` and `ṳ`-syllables; see *Tone-Mark Placement*) |
| `Hakfa_IPA` | IPA with Chao tone letters, bracketed per-syllable |
| `Hanzi` | 漢字 |
| `Tai-gi` | 台語解說 |
| `Hakfa-exp` | 客語解說 |
| `Hua-gi` | 華語解說 |
| `Eng-gi` | 英語解說 |

KPPY columns are intentionally omitted from `bunji/`. For PFS ↔ KPPY ↔ IPA conversion, use `lib/KonvertToPFS` (Kotlin Multiplatform, GPL-3.0). The Python scraper reimplements a subset of FHL→PFS+IPA inline; cross-check against the lib when changing tone/conversion logic.

## License

CC BY-NC-SA 3.0 Taiwan. Original data from 台語信望愛客語辭典.
