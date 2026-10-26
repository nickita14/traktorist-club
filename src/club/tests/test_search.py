import pytest

from club.search import fold_plain, fold_ro, folds, found, matches

# (stored name, what the organizer types): the query finds the name.
PAIRS = [
    ("Ion", "ио"),
    ("Ștefan", "stefan"),
    ("Иван", "ivan"),
    ("Ţurcanu", "цуркану"),
    ("Țurcanu", "цуркану"),
    ("Наталья", "natalia"),
    ("Cristina", "кристина"),
    ("Kristina", "кристина"),
    ("Ciobanu", "чобану"),
    ("Cebotari", "чеботарь"),
    ("Chiril", "кирилл"),
    ("Gheorghe", "георге"),
    ("Giurgiu", "джурджу"),
    ("Nicolae", "николае"),
    ("Ștefan", "штефан"),
    ("Ștefan", "shtefan"),
    ("Şerban", "шербан"),
    ("Șerban", "шербан"),
    ("Mîndru", "мындру"),
]


# A query that is a prefix of the name ("ио", and "чеботарь" without its final i) finds it one way.
PREFIXES = {"Ion", "Cebotari"}


@pytest.mark.parametrize(("name", "query"), PAIRS)
def test_query_finds_the_name(name, query):
    assert matches(query, name)
    if name not in PREFIXES:
        assert matches(name, query)


@pytest.mark.parametrize(
    ("text", "plain", "ro"),
    [
        ("Ștefan", "stefan", "shtefan"),
        ("Şerban", "serban", "sherban"),
        ("ĂȘȚÎ", "astsi", "ashtsi"),
        ("Mîndru", "mindru", "mindru"),
        ("Мындру", "mindru", "mindru"),
        ("Ţurcanu", "tsurcanu", "tsurkanu"),
        ("Йордан", "iordan", "iordan"),
        ("Алёна", "alena", "alena"),
        ("Подъячев", "podiachev", "podiachev"),
        ("Джурджу", "djurdju", "djurdju"),
        ("Кирилл", "kiril", "kiril"),
        ("Ciobanu", "ciobanu", "chobanu"),
        ("Cebotari", "cebotari", "chebotari"),
        ("Gheorghe", "gheorghe", "george"),
        ("Giurgiu", "giurgiu", "djurdju"),
        ("Gelu", "gelu", "djelu"),
        ("Chiril", "chiril", "kiril"),
        ("Cristina", "cristina", "kristina"),
        ("Victor 11", "victor 11", "viktor 11"),
        ("", "", ""),
    ],
)
def test_folds(text, plain, ro):
    assert fold_plain(text) == plain
    assert fold_ro(text) == ro


def test_cyrillic_is_not_read_the_romanian_way():
    assert fold_ro("Чичи") == fold_plain("Чичи") == "chichi"


def test_s_comma_reads_both_ways():
    # Romanian ș is "sh" for the Cyrillic spelling, plain "s" for a Latin keyboard.
    for query in ("штефан", "shtefan", "stefan", "ШТЕФ", "Stef"):
        assert matches(query, "Ștefan")
    assert not matches("stefan", "Штефан")  # Cyrillic ш is only ever "sh"


def test_mixed_case():
    assert matches("иОн", "ION")
    assert matches("ȘTEFAN", "stEfan")


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("c", "Ciobanu"),
        ("ci", "Ciobanu"),
        ("cio", "Ciobanu"),
        ("ch", "Ciobanu"),
        ("ch", "Chiril"),
        ("gh", "Gheorghe"),
        ("чоб", "Ciobanu"),
        ("крис", "Cristina"),
        ("chobanu", "Ciobanu"),
        ("ана", "Ana"),
        ("anna", "Ана"),
    ],
)
def test_partial_and_english_style_queries(query, name):
    assert matches(query, name)


@pytest.mark.parametrize(
    ("query", "name"),
    [("ivan", "Ion"), ("крис", "Ciobanu"), ("чоб", "Cristina"), ("stefania", "Ștefan")],
)
def test_no_match(query, name):
    assert not matches(query, name)


def test_nickname_or_name():
    assert matches("ваня", "Ion Ceban", "Vanea") is False
    assert matches("ванеа", "Ion Ceban", "Vanea")
    assert matches("чебан", "Ion Ceban", "")


def test_empty_query_matches_everything():
    assert matches("", "Ion")
    assert matches("   ", "Ion")
    assert found(folds(""), folds("Ion"))


def test_empty_texts_match_nothing():
    assert not matches("ion")
    assert not matches("ion", "", "")
