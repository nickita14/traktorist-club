"""Name search across alphabets: Cyrillic, Romanian Latin with diacritics, plain Latin.

Organizers type on a phone keyboard that is often in the wrong layout, so "ио" finds "Ion",
"stefan" finds "Ștefan" and "ivan" finds "Иван". Every text gets two folds: `fold_plain` keeps
Latin as written, `fold_ro` reads it the Romanian way ("Ciobanu" sounds "chobanu", "Ștefan"
"shtefan"). A query matches when either of its folds is a substring of either fold of a name or
nickname.
"""

import re
import unicodedata

# Close to Romanian spelling; ъ and ь are dropped. Й and ё lose their marks before this table.
CYRILLIC = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ж": "j",
        "з": "z",
        "и": "i",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sh",
        "ъ": "",
        "ы": "i",
        "ь": "",
        "э": "e",
        "ю": "iu",
        "я": "ia",
    }
)

# Romanian reading of Latin c and g, one left-to-right pass: hard "chi"/"ghe", a silent i in
# "cio"/"giu", soft "ce"/"gi", any other c is k.
ROMANIAN = re.compile(
    r"(?P<hard>[cg])h(?=[ie])"
    r"|(?P<silent>[cg])i(?=[aou])"
    r"|(?P<soft>[cg])(?=[ie])"
    r"|c"
)

DOUBLED = re.compile(r"([^\W\d_])\1+")


def _romanian(match: re.Match) -> str:
    if hard := match["hard"]:
        return "k" if hard == "c" else "g"
    if soft := match["silent"] or match["soft"]:
        return "ch" if soft == "c" else "dj"
    return "k"


def _fold(text: str, *, romanian: bool) -> str:
    text = text.casefold().replace("ț", "ts").replace("ţ", "ts")
    if romanian:
        # "Ștefan" sounds "shtefan", as Russian speakers write it ("Штефан").
        text = text.replace("ș", "sh").replace("ş", "sh")
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    if romanian:
        text = ROMANIAN.sub(_romanian, text)
    text = text.replace("дж", "dj").translate(CYRILLIC)
    return DOUBLED.sub(r"\1", text)


def fold_plain(text: str) -> str:
    """Lowercase, no diacritics, Cyrillic in Latin letters, no doubled letters."""
    return _fold(text, romanian=False)


def fold_ro(text: str) -> str:
    """`fold_plain` reading ș, c and g the Romanian way: "Ciobanu" -> "chobanu"."""
    return _fold(text, romanian=True)


def folds(*texts: str) -> frozenset[str]:
    """Both folds of every non-empty text: compute them once per candidate."""
    return frozenset(fold(text) for text in texts if text for fold in (fold_plain, fold_ro))


def found(needles: frozenset[str], haystack: frozenset[str]) -> bool:
    """Whether a query's folds (`folds(query)`) occur in a candidate's folds."""
    return not needles or any(needle in hay for needle in needles for hay in haystack)


def matches(query: str, *texts: str) -> bool:
    """Whether the query occurs in any of the texts in any alphabet. An empty query matches."""
    return found(folds(query.strip()), folds(*texts))
