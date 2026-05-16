from namr.generator import MAX_LEN, _norm, validate_title


def test_valid_title_passes():
    assert validate_title("Sunday spin through Collserola", []) is None


def test_rejects_too_long():
    title = "a" * (MAX_LEN + 1)
    assert validate_title(title, []) == f"too_long_{MAX_LEN + 1}"


def test_rejects_emoji():
    assert validate_title("Quick spin 🚴", []) == "contains_emoji"


def test_rejects_multiline():
    assert validate_title("first line\nsecond", []) == "multiline"


def test_rejects_blocklist_phrases():
    res = validate_title("Crushed it on Montjuïc", [])
    assert res is not None and res.startswith("blocklist:")
    res2 = validate_title("Another one in the books", [])
    assert res2 is not None and res2.startswith("blocklist:")


def test_rejects_duplicate_recent_case_insensitive():
    recent = ["Morning shuffle near the port"]
    assert validate_title("morning shuffle near the port", recent) == "duplicate_recent"


def test_empty_rejected():
    assert validate_title("", []) == "empty"


def test_norm_strips_quotes():
    assert _norm('"Foo bar"') == "Foo bar"
    assert _norm("“Foo bar”") == "Foo bar"
    assert _norm("«Foo bar»") == "Foo bar"
    assert _norm("  Foo bar  ") == "Foo bar"
