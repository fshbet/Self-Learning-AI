from knowledge_platform.core.extraction.chunker import Chunk
from knowledge_platform.core.extraction.extractor import locate_quote, looks_like_boilerplate, statement_hash

TEXT = (
    "Evaluates an expression in a modified filter context.\n\n"
    "The expression you use  as the first parameter works like a measure."
)


def test_exact_quote():
    assert locate_quote("modified filter context", TEXT) == (29, 52)


def test_whitespace_and_case_tolerant_quote():
    loc = locate_quote("the expression you use as the first parameter", TEXT)
    assert loc is not None
    assert TEXT[loc[0] : loc[1]].lower().startswith("the expression you use")


def test_missing_quote_is_rejected():
    assert locate_quote("this sentence does not exist anywhere", TEXT) is None


def test_statement_hash_is_normalized():
    assert statement_hash("CALCULATE modifies filter context.") == statement_hash(
        "  calculate   modifies filter context "
    )


def test_boilerplate_filter():
    nav_lines = ["Home", "Docs", "Blog", "Sign in", "Search", "Menu", "Help", "About", "Terms"]
    nav = Chunk(0, [], "\n".join(nav_lines), 0, 0)
    prose = Chunk(
        0, [], "This paragraph explains how CALCULATE modifies the filter context in detail and at length.", 0, 0
    )
    assert looks_like_boilerplate(nav)
    assert not looks_like_boilerplate(prose)
