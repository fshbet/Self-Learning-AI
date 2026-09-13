from knowledge_platform.core.extraction.chunker import chunk_text

DOC = (
    "Intro paragraph that is long enough to be kept as a chunk on its own for testing purposes here.\n\n"
    "# Syntax\n\nCALCULATE(<expression>[, <filter1>])\n\n"
    "## Parameters\n\nThe expression you use as the first parameter works like a measure. "
    "Filters can be Boolean expressions.\n\n"
    "# Remarks\n\n" + ("Remark sentence number one that is repeated to build a long section. " * 120)
)


def test_chunks_follow_headings_and_track_offsets():
    chunks = chunk_text(DOC, max_chars=2000, min_chars=20)
    assert chunks[0].heading_path == []
    paths = [c.heading_path for c in chunks]
    assert ["Syntax"] in paths or ["Syntax", "Parameters"] in paths
    for c in chunks:
        assert DOC[c.start : c.end].strip().startswith(c.text.strip()[:20])


def test_oversized_sections_are_split():
    chunks = chunk_text(DOC, max_chars=2000, min_chars=20)
    remarks = [c for c in chunks if c.heading_path == ["Remarks"]]
    assert len(remarks) >= 2
    assert all(len(c.text) <= 2100 for c in remarks)
