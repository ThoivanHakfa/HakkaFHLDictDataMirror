import sys
import unicodedata
sys.path.append('script')
from scraper import (
    pfs_to_ipa,
    fhl_numeric_to_pfs_numeric,
    is_pfs_numeric_corpus,
    normalize_kppy_uv_onglide_to_pfs,
)

# FHL → PFS conversion. The function always converts; caller is responsible
# for ensuring input is FHL form.
fhl_to_pfs_cases = [
    ('si5',  'si1'),    # PFS 1 (sî)
    ('si3',  'si2'),    # PFS 2 (sì)
    ('si2',  'si3'),    # PFS 3 (sí)
    ('si',   'si4'),    # PFS 4 (si)
    ('sek8', 'sek5'),   # PFS 5 (se̍k)
    ('sek',  'sek6'),   # PFS 6 (sek)
    ('hap8-ki5-la5-san5', 'hap5-ki1-la1-san1'),
    ('mi2-mi5', 'mi3-mi1'),
    ('ham3', 'ham2'),   # previously-ambiguous; now correctly converts
]
for fhl, expected in fhl_to_pfs_cases:
    got = fhl_numeric_to_pfs_numeric(fhl)
    assert got == expected, f"FHL→PFS: {fhl!r} → {got!r}, expected {expected!r}"

# Corpus-level form detection. PFS corpora must contain at least one strictly
# PFS-only shape (open+1, open+4, stop+5, stop+6) to be reliably detected;
# real dictionary corpora always do.
pfs_corpus = ['si1', 'si4', 'sek5', 'sek6', 'ham2', 'hap5-ki1-la1-san1']
fhl_corpus = ['si5', 'si',  'sek8', 'sek',  'ham3', 'hap8-ki5-la5-san5']
mixed_pfs_only_ambig = ['ham3', 'mi2', 'lo2-ho3']  # all (open, 2/3) — ambiguous corpus

assert is_pfs_numeric_corpus(pfs_corpus), "PFS corpus should be detected as PFS"
assert not is_pfs_numeric_corpus(fhl_corpus), "FHL corpus should be detected as not-PFS"
assert is_pfs_numeric_corpus(mixed_pfs_only_ambig), \
    "Ambiguous all-(open,2/3) corpus is structurally valid PFS — defaults to True"
assert not is_pfs_numeric_corpus([]), "Empty input → False (no signal)"
assert not is_pfs_numeric_corpus(['', None or '']), "All-empty input → False"

# Idempotent bulk re-conversion: gate the call with the corpus detector.
def safe_bulk_convert(texts):
    return texts if is_pfs_numeric_corpus(texts) else [fhl_numeric_to_pfs_numeric(t) for t in texts]

assert safe_bulk_convert(fhl_corpus) == pfs_corpus, "First pass: FHL → PFS"
assert safe_bulk_convert(pfs_corpus) == pfs_corpus, "Second pass: PFS → PFS unchanged"

# PFS-numeric → IPA tone values for Si-yen
pfs_to_ipa_cases = [
    ('si1',  '[ɕi˨˦]'),
    ('si2',  '[ɕi˩˩]'),
    ('si3',  '[ɕi˧˩]'),
    ('si4',  '[ɕi˥˥]'),
    ('sek5', '[sek̚˥]'),
    ('sek6', '[sek̚˨]'),
    # ii (PFS_INPUT for ṳ) → ɨ; must NOT trigger palatalization
    ('sii4',   '[sɨ˥˥]'),
    ('sii1',   '[sɨ˨˦]'),
    ('chii3',  '[tsɨ˧˩]'),
    ('chhii4', '[tsʰɨ˥˥]'),
    # ṳ (PFS_UNICODE) → ɨ
    ('sṳ4',    '[sɨ˥˥]'),
    # ts/tsh aliases (palatalize before /i/, same as ch/chh)
    ('tsi1',   '[tɕi˨˦]'),
    ('tshi1',  '[tɕʰi˨˦]'),
    ('tsha4',  '[tsʰa˥˥]'),
    # unreleased stop codas
    ('hap5',   '[hap̚˥]'),
    ('hat5',   '[hat̚˥]'),
    ('hak6',   '[hak̚˨]'),
]
for pfs, expected in pfs_to_ipa_cases:
    got = pfs_to_ipa(pfs)
    assert got == expected, f"PFS→IPA: {pfs!r} → {got!r}, expected {expected!r}"

# KPPY u-onglide → canonical PFS o-onglide normalization. The nine target rimes
# (ua/uai/uan/uang/uat/uak/ue/uen/uet) collapse to oV; genuine PFS u-rimes
# (ui/un/ung/uk/ut) and ṳ-rimes (ii/iim/iin/iip/iit) stay.
uv_normalize_cases = [
    # Numeric form
    ('kua4',   'koa4'),
    ('kuai3',  'koai3'),
    ('kuan1',  'koan1'),
    ('kuet6',  'koet6'),
    ('hoi3-kuan1', 'hoi3-koan1'),
    # Unicode form: tone-mark on `u` relocates to `o`
    ('khúa',   'khóa'),
    # Unicode form: tone-mark on a different letter stays put
    ('kuân',   'koân'),
    ('nguàn',  'ngoàn'),
    ('koâi',   'koâi'),     # already canonical
    # Case preservation
    ('Kuet6',  'Koet6'),
    ('KUET6',  'KOET6'),
    ('Chung1-kuet6', 'Chung1-koet6'),
    # Idempotency
    ('koa4',   'koa4'),
    ('koet6',  'koet6'),
    # Genuine PFS u-rimes must NOT shift
    ('kui3',   'kui3'),
    ('kun1',   'kun1'),
    ('kung1',  'kung1'),
    ('kuk6',   'kuk6'),
    ('kut5',   'kut5'),
    # ṳ ≠ u: ṳ-rimes (NFC and NFD) must NOT mangle
    ('sṳ4',    'sṳ4'),
    ('chṳn1',  'chṳn1'),
    (unicodedata.normalize('NFD', 'chṳan1'), 'chṳan1'),  # synthetic NFD ṳan
    # Edge cases
    ('',       ''),
    ('ngai1',  'ngai1'),
]
for inp, expected in uv_normalize_cases:
    got = normalize_kppy_uv_onglide_to_pfs(inp)
    got_n = unicodedata.normalize('NFC', got)
    expected_n = unicodedata.normalize('NFC', expected)
    assert got_n == expected_n, f"uv-normalize: {inp!r} → {got!r}, expected {expected!r}"

print("OK — all FHL→PFS, corpus-detect, bulk-idempotency, PFS→IPA, and uv-normalize assertions passed.")
