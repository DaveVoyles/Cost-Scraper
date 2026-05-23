"""Title normalization, tokenization, fuzzy-match coverage gate, edition classification.

All matching logic is centralized here so each source module can share consistent
behaviour when comparing search results back to the user's Playnite title.
"""
import re

ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7",
         "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12", "xiii": "13",
         "xiv": "14", "xv": "15"}

STOPWORDS = {"the", "of", "a", "an", "and", "-", "\u2013", "\u2014", ":", "|", "&"}

EDITION_TOKENS = {"edition", "goty", "deluxe", "ultimate", "complete", "definitive",
                  "gold", "premium", "collector", "collectors", "anniversary",
                  "enhanced", "remastered", "directors", "director", "cut",
                  "standard", "pc", "xbox", "steam", "ps4", "ps5", "switch", "key"}

EDITION_PATTERNS = [
    ("GOTY",        r"\b(game[ -]of[ -]the[ -]year|GOTY)\b"),
    ("Complete",    r"\b(complete edition|all[- ]in[- ]one)\b"),
    ("Definitive",  r"\bdefinitive\b"),
    ("Ultimate",    r"\bultimate\b"),
    ("Deluxe",      r"\bdeluxe\b"),
    ("Gold",        r"\bgold\s+edition\b"),
    ("Premium",     r"\bpremium\b"),
    ("Collector",   r"\bcollect(or|or's|ors)\b"),
    ("Anniversary", r"\banniversary\b"),
    ("Enhanced",    r"\benhanced\b"),
    ("Remastered",  r"\bremaster(ed)?\b"),
    ("Standard",    r"\bstandard\s+edition\b"),
]

DLC_PATTERNS = [
    ("DLC",        r"(?:- ?DLC\b|\bDLC\b|\bexpansion(?: pass)?\b|\bseason pass\b|\bcontent pack\b|\bcharacter pack\b|\bpre[- ]order bonus\b)"),
    ("Bundle",     r"(\b\d+[- ]?pack\b|\bbundle\b|\+\s)"),
    ("Soundtrack", r"\b(soundtrack|OST|original sound ?track)\b"),
    ("Upgrade",    r"\bupgrade\b"),
]

FULL_GAME_EDITIONS = {"Standard", "Deluxe", "GOTY", "Complete", "Definitive",
                      "Ultimate", "Gold", "Premium", "Collector", "Anniversary",
                      "Enhanced", "Remastered", "Officer"}

EDITION_ORDER = {e: i for i, e in enumerate(
    ["Standard", "Deluxe", "GOTY", "Complete", "Definitive", "Ultimate", "Gold",
     "Premium", "Collector", "Anniversary", "Enhanced", "Remastered", "Officer",
     "Other", "DLC", "Bundle", "Upgrade", "Soundtrack"]
)}

SUFFIX_STRIP = re.compile(
    r"\s*[-\u2013\u2014]?\s*(PC|Xbox(?: One| Series)?|PS[45]|PS4/PS5|Nintendo Switch|Switch|Steam)\b"
    r"|\s*\((?:US|UK|EU|EMEA|NA|WW|Worldwide|North America|Europe(?: & UK)?|Europe Middle East and Africa|Asia|Latin America|LATAM|Mexico|US/Mexico|Australia|Austria|Global|\d{4})\)"
    r"|\s*-\s*Steam Key$",
    re.IGNORECASE,
)

NOISE = re.compile(
    r"\s*[-\u2013\u2014:]?\s*\b(director'?s cut|definitive edition|game of the year edition|"
    r"GOTY edition|deluxe edition|ultimate edition|complete edition|enhanced edition|remastered)\b.*$",
    re.IGNORECASE,
)

_punct = re.compile(r"[\u2013\u2014\-:\|/\\]+")
_ws = re.compile(r"\s+")


def normalize_for_search(t: str) -> str:
    """Strip edition phrases so the search query has broadest recall."""
    return NOISE.sub("", t).strip()


def tokenize(s: str) -> list[str]:
    s = _punct.sub(" ", s.lower())
    s = re.sub(r"['\u2019]", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _ws.sub(" ", s).strip()
    out = []
    for w in s.split():
        if not w or w in STOPWORDS:
            continue
        if w in ROMAN:
            w = ROMAN[w]
        out.append(w)
    return out


def core_tokens(title: str) -> list[str]:
    """Tokens stripped of edition/platform boilerplate."""
    return [t for t in tokenize(title) if t not in EDITION_TOKENS]


def base_token_coverage(base_title: str, hit_name: str) -> float:
    base = core_tokens(base_title)
    if not base:
        return 0.0
    hit_toks = set(tokenize(hit_name))
    matched = sum(1 for t in base if t in hit_toks)
    return matched / len(base)


def _strip_suffixes(name: str) -> str:
    prev = None
    s = name
    while prev != s:
        prev = s
        s = SUFFIX_STRIP.sub("", s).strip(" -\u2013\u2014:")
    return s


def _norm_compare(s: str) -> str:
    s = s.lower()
    s = re.sub(r"['\u2019]", "", s)
    s = re.sub(r"[\u2013\u2014\-:\|/]+", " ", s)
    s = re.sub(r"[(),]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = " ".join(ROMAN.get(tok, tok) for tok in s.split())
    return s


def classify_edition(hit_name: str, base_title: str) -> str:
    """Return one of: DLC, Bundle, Soundtrack, Upgrade, Standard, Deluxe, …, Other."""
    for label, pat in DLC_PATTERNS:
        if re.search(pat, hit_name, re.IGNORECASE):
            return label
    stripped = _strip_suffixes(hit_name)
    norm_hit = _norm_compare(stripped)
    norm_base = _norm_compare(base_title)
    if norm_hit == norm_base:
        return "Standard"
    if norm_hit.startswith(norm_base + " "):
        trailing = stripped[len(stripped) - (len(norm_hit) - len(norm_base)):].strip(" -\u2013\u2014:")
        m = re.match(r"([A-Za-z]+)\s+edition\b", trailing, re.IGNORECASE)
        if m:
            return m.group(1).title()
    for label, pat in EDITION_PATTERNS:
        if re.search(pat, hit_name, re.IGNORECASE):
            return label
    return "Other"


def is_full_game(edition: str) -> bool:
    return edition in FULL_GAME_EDITIONS
