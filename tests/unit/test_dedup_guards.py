from knowledge_platform.core.quality.dedup import is_near_duplicate, lexical_overlap
from knowledge_platform.models import KnowledgeItem


def _item(subject: str, statement: str, obj: str = "") -> KnowledgeItem:
    return KnowledgeItem(subject=subject, statement=statement, object=obj)


def test_different_subjects_are_never_duplicates():
    a = _item("CLOSINGBALANCEWEEK", "The CLOSINGBALANCEWEEK function returns the closing balance for the week.")
    b = _item("OPENINGBALANCEWEEK", "The OPENINGBALANCEWEEK function returns the opening balance for the week.")
    assert not is_near_duplicate(a, b, 0.01, max_distance=0.06)


def test_same_subject_needs_lexical_overlap():
    a = _item("CALCULATE", "CALCULATE evaluates an expression in a modified filter context.", "modified filter context")
    b = _item(
        "CALCULATE", "CALCULATE evaluates an expression in a filter context that has been modified.", "filter context"
    )
    c = _item("CALCULATE", "CALCULATE performs context transition when a row context exists.", "context transition")
    assert is_near_duplicate(a, b, 0.02, max_distance=0.06)
    assert not is_near_duplicate(a, c, 0.02, max_distance=0.06)
    assert not is_near_duplicate(a, b, 0.2, max_distance=0.06)


def test_lexical_overlap_bounds():
    assert lexical_overlap("a b c", "a b c") == 1.0
    assert lexical_overlap("a b c", "x y z") == 0.0


def test_same_template_different_object_is_not_duplicate():
    a = _item("DAX function reference", "The DAX function reference covers financial functions.", "financial functions")
    b = _item("DAX function reference", "The DAX function reference covers other functions.", "other functions")
    assert not is_near_duplicate(a, b, 0.01, max_distance=0.06)
