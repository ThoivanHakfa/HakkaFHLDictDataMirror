import urllib.request
import csv
import http.client
import threading
import time
import sys
import os
import json
import re
import socket
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser

# Concurrency knobs for the per-ID fetch loop. 10 workers with a 30s timeout and
# per-thread persistent HTTPSConnection (keep-alive) is the sweet spot against
# the Google Cloud origin: enough parallelism to finish 23,500 IDs in ~15 min,
# few enough to avoid origin-side queueing that triggers timeouts.
SCRAPE_WORKERS = 10
HTTP_TIMEOUT = 30
HTTP_MAX_ATTEMPTS = 2  # try once, retry once on transient failure (no sleep)

# The Cloudflare edge for hakka.fhl.net returns 403 from origin nginx for /dict/*
# when routed via certain regions (e.g. Hong Kong). The Google Cloud origin behind
# south.fhl.net (35.221.176.32) serves the same vhost and answers /dict/ normally,
# so we override DNS for hakka.fhl.net to hit the origin directly with the cert's
# matching SNI (*.fhl.net Let's Encrypt). If FHL ever moves the origin, update
# _FHL_ORIGIN_IP or remove this block to fall back to public DNS.
_FHL_ORIGIN_IP = '35.221.176.32'
_real_getaddrinfo = socket.getaddrinfo
def _patched_getaddrinfo(host, port, *args, **kwargs):
    if host == 'hakka.fhl.net':
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (_FHL_ORIGIN_IP, port))]
    return _real_getaddrinfo(host, port, *args, **kwargs)
socket.getaddrinfo = _patched_getaddrinfo

class FHLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_td = False
        self.current_data = []
        self.all_data = []

    def handle_starttag(self, tag, attrs):
        if tag == 'td' or tag == 'th':
            self.in_td = True
            self.current_data = []

    def handle_endtag(self, tag):
        if tag == 'td' or tag == 'th':
            self.in_td = False
            text = ''.join(self.current_data).strip()
            text = ' '.join(text.split())
            self.all_data.append(text)

    def handle_data(self, data):
        if self.in_td:
            self.current_data.append(data)

# FHL serves Hak-fa numerics in POJ-style digits (2/3/5/8 marked, 1/4 omitted).
# This project canonicalizes to traditional 長老教會 PFS tone numbers 1~6:
#   1 sî (â circumflex)   2 sì (à grave)   3 sí (á acute)
#   4 si (unmarked)       5 se̍k (a̍ + stop) 6 sek (unmarked + stop)
# POJ→PFS digit map for marked syllables; unmarked syllables disambiguate by coda.
POJ_TO_PFS_DIGIT = {'5': '1', '3': '2', '2': '3', '8': '5'}

# Permissive syllable pattern: includes Latin Extended ranges and combining
# diacritics (̀-ͯ) so a few malformed FHL rows that leak diacritics into the
# numeric column (e.g. IDs 22542, 22646) still get partially processed instead
# of skipped wholesale.
_SYLLABLE_RE = re.compile(r'([A-Za-zÀ-ɏ̀-ͯḀ-ỿṳ]+[0-9]?)')

def fhl_numeric_to_pfs_numeric(text):
    """Convert one FHL POJ-style numeric row → PFS 1~6 numeric. Always converts.

    The caller must ensure `text` is in FHL form (e.g. freshly fetched from
    `hakka.fhl.net`). This function deliberately does NOT detect input form:
    ~12% of real FHL rows are structurally indistinguishable from valid PFS
    in isolation (the `(open, 2)` / `(open, 3)` collision — e.g. `ham3` is
    valid as both FHL hàm and PFS hám), so any per-row guard would
    misidentify them. For idempotent bulk re-conversion, gate the call with
    `is_pfs_numeric_corpus()` over the full corpus instead.
    """
    if not text:
        return text

    def convert(syl):
        m = re.search(r'([0-9])$', syl)
        if m:
            digit = m.group(1)
            if digit not in POJ_TO_PFS_DIGIT:
                # FHL only emits {2,3,5,8}; anything else means upstream drift.
                print(f"WARN: unexpected FHL tone digit {digit!r} in {syl!r}", file=sys.stderr)
                return syl
            return f"{syl[:-1]}{POJ_TO_PFS_DIGIT[digit]}"
        # Unmarked: open syllable → 4 (si), stop coda p/t/k → 6 (sek)
        return f"{syl}{'6' if re.search(r'[ptk]$', syl, re.IGNORECASE) else '4'}"

    return _SYLLABLE_RE.sub(lambda m: convert(m.group(1)), text)

def _is_valid_pfs_syllable(syl):
    # Valid PFS shape: trailing digit 1-4 on open coda, or 5-6 on p/t/k coda.
    m = re.search(r'([0-9])$', syl)
    if not m:
        return False
    digit = m.group(1)
    is_stop = bool(re.search(r'[ptk]$', syl[:-1], re.IGNORECASE))
    return digit in ('5', '6') if is_stop else digit in ('1', '2', '3', '4')

def is_pfs_numeric_corpus(texts):
    """Heuristic corpus-level form detection.

    Returns True iff every non-empty entry in `texts` consists entirely of
    valid PFS shapes (open+1-4, stop+5-6). FHL form is detected by any
    unmarked syllable, any FHL-only digit (7/8), or any invalid-PFS shape
    such as `(open, 5)` or `(stop, 8)` appearing anywhere in the corpus.
    Reliable for full dictionary corpora because real FHL data invariably
    contains many disambiguating shapes; do not call on a single token.
    """
    saw_any_syllable = False
    for text in texts:
        if not text:
            continue
        syllables = _SYLLABLE_RE.findall(text)
        for s in syllables:
            saw_any_syllable = True
            if not _is_valid_pfs_syllable(s):
                return False
    return saw_any_syllable

# --- FHL Unicode → PFS Unicode (POJ-rule placement + ṳ restoration) -----------
# FHL's graph=2 column places the tone diacritic on the first vowel of the
# cluster (skipping a leading medial `i`), and drops the trema-below from `ṳ`
# whenever a tone diacritic is added (sii5 → sû rather than sṳ̂).
# We re-place per POJ rules to produce the bunji/ PFS_Unicode column. Tangloo/
# keeps the FHL form verbatim. See CLAUDE.md §"Tone-Mark Placement" for the
# divergence cells (ua, oai, uai) and rule listing.

_PFS_TONE_COMBINING = {'̂', '̀', '́', '̍'}  # ◌̂ ◌̀ ◌́ ◌̍
_TREMA_BELOW = '̤'  # ṳ = u + U+0324
_VOWELS = set('aeiouṳ')

def _pfs_tone_position(base_lc):
    """Where to place the PFS tone diacritic in `base_lc`.

    The placement rule is borrowed letter-for-letter from canonical Taigi POJ
    Section 21 (Hak-fa PFS adopts the same convention). Reference:
    https://github.com/Taigibun/taigibun-agent-skills/blob/main/taigi-roman-orthography-converter/references/linguistic_rules.md

      1. Single vowel: Mark the vowel.
      2. No vowel:     Mark the nasal (m, n, ng — treat `ng` as 1 unit).
      3. Compound vowels: Mark the 2nd letter from the right
                          (treating `ng` as 1 unit).
           Exception 1: If 2nd from right is `i` → mark 1st (rightmost) letter.
           Exception 2: Checked syllable + 2nd from right is `i`/`u` (but not
                        `iu` / `iu`+stop) → mark 3rd letter.
           Special:     `iu`+stop → mark 2nd from right (no exception).

    `base_lc` is the syllable's base letters, lowercased, with combining marks
    already stripped (so `ṳ` arrives as plain `u`; the trema-below is restored
    on reassembly via NFC).

    Returns the index in `base_lc` where the tone diacritic should land, or
    None if there is no place to put it.
    """
    # Tokenise so that `ng` counts as one letter.
    tokens = []  # [(text, start_index_in_base_lc), ...]
    i = 0
    while i < len(base_lc):
        if base_lc[i:i+2] == 'ng':
            tokens.append(('ng', i)); i += 2
        else:
            tokens.append((base_lc[i], i)); i += 1
    if not tokens:
        return None

    vowel_ks = [k for k, (t, _) in enumerate(tokens) if t in _VOWELS]

    if not vowel_ks:  # Rule 2: no vowel → nasal (ng > m > n)
        for nasal in ('ng', 'm', 'n'):
            for t, pos in tokens:
                if t == nasal:
                    return pos
        return None

    if len(vowel_ks) == 1:  # Rule 1: single vowel
        return tokens[vowel_ks[0]][1]

    # Rule 3: compound vowels — start at 2nd token from the right.
    n = len(tokens)
    k = n - 2
    is_checked = tokens[-1][0] in ('p', 't', 'k')
    vowels_only = ''.join(tokens[vk][0] for vk in vowel_ks)

    # Exception 2: checked + 2nd-from-right is i/u, but NOT `iu`/`iu`+stop.
    if is_checked and tokens[k][0] in ('i', 'u') and vowels_only != 'iu':
        if n - 3 >= 0:
            return tokens[n - 3][1]
        # fall through if no 3rd-from-right exists

    # Exception 1: 2nd-from-right is `i` → mark rightmost token.
    if tokens[k][0] == 'i':
        return tokens[-1][1]

    return tokens[k][1]

def _reposition_syllable(fhl_syl, fhl_num_syl=None):
    """Move the tone diacritic to the POJ-correct vowel; restore ṳ if the
    numeric form spells it `ii`. Preserves case and non-tone combining marks."""
    if not fhl_syl:
        return fhl_syl
    nfd = unicodedata.normalize('NFD', fhl_syl)
    base = []
    combining = []  # list of [base_index, combining_char]
    for ch in nfd:
        if unicodedata.combining(ch):
            combining.append([len(base) - 1, ch])
        else:
            base.append(ch)
    base_lc = ''.join(c.lower() for c in base)

    # Restore U+0324 where numeric `ii` corresponds to bare `u` in unicode.
    if fhl_num_syl:
        num_base = re.sub(r'[0-9]$', '', fhl_num_syl).lower()
        i_n = i_u = 0
        to_add = []
        while i_n < len(num_base) and i_u < len(base_lc):
            cn, cu = num_base[i_n], base_lc[i_u]
            if cn == 'i' and i_n + 1 < len(num_base) and num_base[i_n+1] == 'i' and cu == 'u':
                if not any(p == i_u and c == _TREMA_BELOW for p, c in combining):
                    to_add.append(i_u)
                i_n += 2; i_u += 1
            elif cn == cu:
                i_n += 1; i_u += 1
            else:
                to_add = []  # parallel walk broke — leave alone
                break
        for pos in to_add:
            combining.append([pos, _TREMA_BELOW])

    # Reposition the tone diacritic if present.
    tone_slot = next((k for k, (_, c) in enumerate(combining) if c in _PFS_TONE_COMBINING), None)
    if tone_slot is not None:
        target = _pfs_tone_position(base_lc)
        if target is not None:
            combining[tone_slot][0] = target

    by_pos = {}
    for pos, ch in combining:
        by_pos.setdefault(pos, []).append(ch)
    out = []
    for i, b in enumerate(base):
        out.append(b)
        out.extend(by_pos.get(i, []))
    return unicodedata.normalize('NFC', ''.join(out))

def fhl_unicode_to_pfs_unicode(fhl_unicode, fhl_numeric=None):
    """Convert an FHL graph=2 string to POJ-corrected PFS Unicode placement."""
    if not fhl_unicode:
        return fhl_unicode
    uni_tokens = re.split(r'([-\s]+)', fhl_unicode)
    num_tokens = re.split(r'([-\s]+)', fhl_numeric) if fhl_numeric else []
    # Align numeric syllables to unicode syllables when both syllable counts match.
    uni_syls = [i for i, t in enumerate(uni_tokens) if t and not re.fullmatch(r'[-\s]+', t)]
    num_syls = [t for t in num_tokens if t and not re.fullmatch(r'[-\s]+', t)]
    pair_numerics = len(uni_syls) == len(num_syls)
    for k, idx in enumerate(uni_syls):
        ns = num_syls[k] if pair_numerics else None
        uni_tokens[idx] = _reposition_syllable(uni_tokens[idx], ns)
    return ''.join(uni_tokens)

# --- KPPY u-onglide → canonical PFS o-onglide -----------------------------------
# A handful of FHL rows spell the rising u-onglide medial KPPY-style (`ua`, `uai`,
# `uan`, `uet`, etc.) instead of canonical PFS `oa`, `oai`, `oan`, `oet`. Per
# §10.2 of the Hak-fa Roman Orthography reference, PFS uniformly writes this
# medial as `oV`; KPPY uses `uV`. The nine rimes that shift are:
#   PFS  oa  oai  oan  oang  oat  oak  oe  oen  oet
#   KPPY ua  uai  uan  uang  uat  uak  ue  uen  ued
# Genuine PFS u-rimes (`ui`, `un`, `ung`, `uk`, `ut`) are phonemically distinct
# and do NOT shift — never touch them.
_KPPY_UV_RIMES = ('uang', 'uan', 'uai', 'uak', 'uat', 'uet', 'uen', 'ue', 'ua')

# Longest-first PFS initials (matches the list in HakfaSyllable.kt).
_PFS_INITIALS = ('tsh', 'chh', 'ts', 'ch', 'ph', 'th', 'kh', 'ng',
                 'p', 't', 'k', 'm', 'n', 'l', 'f', 'v', 's', 'h')

def _strip_pfs_onset(base_lc):
    for o in _PFS_INITIALS:
        if base_lc.startswith(o):
            return len(o)
    return 0

def _normalize_uv_syllable(syl):
    """If `syl`'s rime matches one of the nine KPPY u-onglide rimes, rewrite the
    leading rime `u` → `o`. Preserves case, tone digit, and combining marks
    (a tone diacritic on a non-`u` letter stays on its original letter)."""
    if not syl:
        return syl
    nfd = unicodedata.normalize('NFD', syl)
    tone_digit = ''
    if nfd and nfd[-1] in '0123456789':
        tone_digit = nfd[-1]
        nfd = nfd[:-1]
    base, combos = [], []
    for ch in nfd:
        if unicodedata.combining(ch):
            combos.append((len(base) - 1, ch))
        else:
            base.append(ch)
    base_lc = ''.join(b.lower() for b in base)
    onset_len = _strip_pfs_onset(base_lc)
    rime_lc = base_lc[onset_len:]
    if rime_lc not in _KPPY_UV_RIMES:
        return syl
    if onset_len >= len(base) or base[onset_len].lower() != 'u':
        return syl
    # ṳ ≠ u: after NFD, ṳ decomposes to `u` + U+0324 (combining diaeresis below).
    # If the `u` we're about to rewrite carries U+0324, it's actually ṳ and the
    # rime is iV-class (e.g. `iian` if it existed), not uV-class — leave it alone.
    if any(pos == onset_len and c == _TREMA_BELOW for pos, c in combos):
        return syl
    base[onset_len] = 'O' if base[onset_len].isupper() else 'o'
    by_pos = {}
    for pos, c in combos:
        by_pos.setdefault(pos, []).append(c)
    out = []
    for i, b in enumerate(base):
        out.append(b)
        out.extend(by_pos.get(i, []))
    return unicodedata.normalize('NFC', ''.join(out)) + tone_digit

def normalize_kppy_uv_onglide_to_pfs(text):
    """Walk syllables in `text`, normalizing any KPPY uV rime to canonical PFS
    oV. Whitespace/hyphen separators are preserved verbatim. Safe on numeric
    and Unicode forms; idempotent on already-canonical input."""
    if not text:
        return text
    tokens = re.split(r'([-\s]+)', text)
    for i, t in enumerate(tokens):
        if t and not re.fullmatch(r'[-\s]+', t):
            tokens[i] = _normalize_uv_syllable(t)
    return ''.join(tokens)

# IPA Conversion Logic for Si-yen Hak-fa (expects PFS 1~6 numerics)
def pfs_to_ipa(pfs_text):
    if not pfs_text: return ""

    def convert_syllable(s):
        s = s.lower().strip()
        # PFS tone number → IPA tone (Chao) for Si-yen 四縣腔
        tone_map = {
            '1': '˨˦',  # sî
            '2': '˩˩',  # sì
            '3': '˧˩',  # sí
            '4': '˥˥',  # si
            '5': '˥',   # se̍k
            '6': '˨',   # sek
        }

        m = re.search(r'([1-6])$', s)
        tone = ""
        if m:
            tone = tone_map.get(m.group(1), "")
            s = s[:-1]

        # ṳ (PFS_INPUT: ii, PFS_UNICODE: ṳ) → ɨ before palatalization
        s = s.replace('ii', 'ɨ')
        s = s.replace('ṳ', 'ɨ')

        # Palatalization before /i/ (alveolar → alveolo-palatal)
        s = re.sub(r'^tshi', 'tɕʰi', s)
        s = re.sub(r'^chhi', 'tɕʰi', s)
        s = re.sub(r'^tsi', 'tɕi', s)
        s = re.sub(r'^chi', 'tɕi', s)
        s = re.sub(r'^ngi', 'ɲi', s)
        s = re.sub(r'^si', 'ɕi', s)

        # Initials (ch/chh = ts/tsh spelling variants)
        s = re.sub(r'^tsh', 'tsʰ', s)
        s = re.sub(r'^chh', 'tsʰ', s)
        s = re.sub(r'^ch', 'ts', s)
        s = re.sub(r'^ph', 'pʰ', s)
        s = re.sub(r'^th', 'tʰ', s)
        s = re.sub(r'^kh', 'kʰ', s)

        s = re.sub(r'^v', 'ʋ', s)

        # Final and initial ng
        s = s.replace('ng', 'ŋ')

        # Vowels
        s = s.replace('er', 'ɤ')

        # Unreleased stop codas
        s = re.sub(r'p$', 'p̚', s)
        s = re.sub(r't$', 't̚', s)
        s = re.sub(r'k$', 'k̚', s)

        return f"[{s}{tone}]"

    # Split by hyphen or space
    syllables = re.split(r'[-\s]+', pfs_text)
    ipa_syllables = [convert_syllable(s) for s in syllables if s]
    return " ".join(ipa_syllables)

# Each worker thread keeps one persistent HTTPS connection to the origin. Avoids
# the per-request TLS handshake cost (~50–100 ms) that single-shot urllib.request
# pays. Connect by hostname so SNI/cert verification target hakka.fhl.net; the
# socket.getaddrinfo override above routes the TCP connect to the origin IP.
_thread_local = threading.local()

def _get_conn():
    conn = getattr(_thread_local, 'conn', None)
    if conn is None:
        conn = http.client.HTTPSConnection('hakka.fhl.net', 443, timeout=HTTP_TIMEOUT)
        _thread_local.conn = conn
    return conn

def _drop_conn():
    conn = getattr(_thread_local, 'conn', None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _thread_local.conn = None

def fetch_id_data(id_val, graph=0):
    path = f"/dict/search_hakka.php?DETAIL=1&LIMIT=id={id_val}&dbname=hakka&graph={graph}"
    headers = {'User-Agent': 'Mozilla/5.0', 'Connection': 'keep-alive'}
    for attempt in range(HTTP_MAX_ATTEMPTS):
        try:
            conn = _get_conn()
            conn.request('GET', path, headers=headers)
            response = conn.getresponse()
            html_bytes = response.read()  # always drain so the connection stays reusable
            if response.status != 200:
                return None  # 404 = no such ID; do not retry
            html = html_bytes.decode('utf-8', errors='ignore')

            parser = FHLParser()
            parser.feed(html)
            if not parser.all_data:
                return None

            data_dict = {}
            for j in range(0, len(parser.all_data) - 1, 2):
                data_dict[parser.all_data[j]] = parser.all_data[j+1]

            if '編號' in data_dict and data_dict['編號'] == str(id_val):
                return data_dict
            return None
        except Exception as e:
            _drop_conn()  # tear down stale connection; next attempt opens fresh
            if attempt == HTTP_MAX_ATTEMPTS - 1:
                print(f"Error fetching ID {id_val} (graph={graph}): {e}", file=sys.stderr)
                return None
            # Retry immediately — origin slowness doesn't get better with sleeps
    return None

def update_manifest(version_id):
    manifest_path = 'public/manifest.json'
    now = datetime.now().strftime('%Y-%m-%d')
    
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    else:
        manifest = {"latest_version": "", "last_updated": "", "versions": []}
    
    manifest["latest_version"] = version_id
    manifest["last_updated"] = now
    if version_id not in manifest["versions"]:
        manifest["versions"].append(version_id)
    
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

def csv_to_json(csv_path, json_path):
    data = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def scrape():
    version_id = datetime.now().strftime('%Y%m%d-%H%M')
    bunji_dir = f'public/{version_id}/bunji'
    tangloo_dir = f'public/{version_id}/tangloo'
    os.makedirs(bunji_dir, exist_ok=True)
    os.makedirs(tangloo_dir, exist_ok=True)

    bunji_csv = os.path.join(bunji_dir, 'HakfaFHLDict.csv')
    bunji_json = os.path.join(bunji_dir, 'HakfaFHLDict.json')
    tangloo_csv = os.path.join(tangloo_dir, 'HakfaFHLDict.csv')
    tangloo_json = os.path.join(tangloo_dir, 'HakfaFHLDict.json')

    max_id = 23500

    # tangloo/ mirrors the FHL site verbatim: original POJ-style numeric (graph=0)
    # and Unicode-diacritic (graph=2) forms, with no conversion. This is the
    # archival snapshot — analogous to HakkaDictMoeDataMirror's raw dump.
    tangloo_columns = [
        'ID',
        'FHL_DICT_Numeric',    # graph=0 raw (POJ-style 1~8)
        'FHL_DICT_Unicode',    # graph=2 raw (Unicode diacritics)
        'Hanzi', 'Tai-gi', 'Hakfa-exp', 'Hua-gi', 'Eng-gi'
    ]
    # bunji/ is the derived output: FHL → PFS (numeric + Unicode) + IPA.
    # KPPY columns intentionally omitted (use lib/KonvertToPFS if needed).
    bunji_columns = [
        'ID',
        'FHL_DICT_Numeric',    # original FHL (POJ-style 1~8) — kept for traceability
        'PFS_Numeric',         # PFS 1~6 numeric
        'PFS_Unicode',         # PFS Unicode diacritics
        'Hakfa_IPA',           # IPA with Chao tone letters
        'Hanzi', 'Tai-gi', 'Hakfa-exp', 'Hua-gi', 'Eng-gi'
    ]

    print(f"Scraping Hak-fa FHL Dictionary (workers={SCRAPE_WORKERS})...")
    print(f"  tangloo: {tangloo_csv} (raw FHL)")
    print(f"  bunji:   {bunji_csv} (FHL → PFS + IPA)")

    def fetch_one(i):
        """Fetch both graphs for one ID; returns (i, tangloo_row, bunji_row) or
        (i, None, None) if FHL returned no data for that ID."""
        data_numeric = fetch_id_data(i, graph=0)
        if not data_numeric:
            return i, None, None
        data_unicode = fetch_id_data(i, graph=2)

        fhl_numeric = data_numeric.get('四縣客語', '')
        fhl_unicode = data_unicode.get('四縣客語', '') if data_unicode else ''
        hanzi = data_numeric.get('漢字', '')
        taigi = data_numeric.get('台語解說', '')
        hakfa_exp = data_numeric.get('客語解說', '')
        huagi = data_numeric.get('華語解說', '')
        enggi = data_numeric.get('英語解說', '')

        tangloo_row = [i, fhl_numeric, fhl_unicode, hanzi, taigi, hakfa_exp, huagi, enggi]

        pfs_numeric = fhl_numeric_to_pfs_numeric(fhl_numeric)
        pfs_numeric = normalize_kppy_uv_onglide_to_pfs(pfs_numeric)
        pfs_unicode = fhl_unicode_to_pfs_unicode(fhl_unicode, fhl_numeric)
        pfs_unicode = normalize_kppy_uv_onglide_to_pfs(pfs_unicode)
        ipa_val = pfs_to_ipa(pfs_numeric)
        bunji_row = [i, fhl_numeric, pfs_numeric, pfs_unicode, ipa_val,
                     hanzi, taigi, hakfa_exp, huagi, enggi]

        return i, tangloo_row, bunji_row

    with open(tangloo_csv, 'w', newline='', encoding='utf-8') as tf, \
         open(bunji_csv, 'w', newline='', encoding='utf-8') as bf, \
         ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as pool:
        tangloo_writer = csv.writer(tf)
        bunji_writer = csv.writer(bf)
        tangloo_writer.writerow(tangloo_columns)
        bunji_writer.writerow(bunji_columns)

        # executor.map preserves input order, so rows land in the CSV sorted by ID
        # even though the HTTP fetches run concurrently. Workers do I/O in parallel;
        # only the CSV writes are serial (cheap).
        for i, tangloo_row, bunji_row in pool.map(fetch_one, range(1, max_id + 1)):
            if tangloo_row is None:
                if i % 500 == 0:
                    print(f"Processed up to ID {i} (skipped)...")
                continue
            tangloo_writer.writerow(tangloo_row)
            bunji_writer.writerow(bunji_row)

            if i % 500 == 0:
                tf.flush(); bf.flush()
                print(f"Processed up to ID {i}...")
                sys.stdout.flush()

    print("Generating JSON...")
    csv_to_json(tangloo_csv, tangloo_json)
    csv_to_json(bunji_csv, bunji_json)

    print("Updating manifest...")
    update_manifest(version_id)
    print(f"Done! Version: {version_id}")

if __name__ == '__main__':
    scrape()
