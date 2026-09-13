from knowledge_platform.core.collection.normalize import canonicalize_url, content_hash, normalize_html

HTML = b"""<html><head><title>Sample page</title></head><body>
<nav><a href="/nav">nav</a></nav>
<main><h1>Filter context</h1>
<p>The filter context is the set of filters applied to the model when a DAX expression is evaluated.
It is one of the two evaluation contexts and it matters for every measure you write in a report.</p>
<p>CALCULATE modifies the filter context.
<a href="../dax/calculate-function-dax#syntax?utm_source=x">CALCULATE</a></p></main>
</body></html>"""


def test_canonicalize_url():
    assert (
        canonicalize_url("HTTPS://Learn.Microsoft.com/en-us/dax/?utm_source=a&b=1#frag")
        == "https://learn.microsoft.com/en-us/dax/?b=1"
    )
    assert canonicalize_url("mailto:x@y.z") == ""
    assert canonicalize_url("../dax/calc", "https://h.com/en-us/power-bi/x") == "https://h.com/en-us/dax/calc"


def test_normalize_html_extracts_main_content_and_links():
    nd = normalize_html(HTML, "https://learn.microsoft.com/en-us/dax/filter-context")
    assert "filter context" in nd.text.lower()
    assert nd.title
    assert any(link.endswith("/en-us/dax/calculate-function-dax") for link in nd.links)
    assert nd.content_hash == content_hash(nd.text)


def test_content_hash_ignores_layout_whitespace():
    assert content_hash("a  b\n\n\n\nc") == content_hash("a b\n\nc")
