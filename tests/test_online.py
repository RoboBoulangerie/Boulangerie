"""Text matching for the grounding check. Network calls are exercised by the CI workflow, not here."""

from boulangerie import online


def test_page_text_drops_scripts_and_unescapes():
    body = "<html><head><script>var name = 'Fake';</script><style>p{}</style></head>" \
           "<body><h1>Tiny&nbsp;KB</h1><p>Genes &amp; diseases</p></body></html>"
    text = online.page_text(body)
    assert "Fake" not in text
    assert "Genes & diseases" in text


def test_exact_match():
    assert online.match_value("Tiny KB", "Welcome to Tiny KB!") == online.MATCH_EXACT


def test_normalized_match_ignores_spacing_case_and_curly_quotes():
    page = "The Children’s   Allergy and\nAsthma Data Repository"
    assert online.match_value("The children's allergy and asthma data repository", page) == online.MATCH_NORMALIZED


def test_partial_match_when_most_sentences_appear():
    value = ("Tiny KB collects facts about genes and diseases. It is updated every month by curators. "
             "It was built with love and care by a team.")
    page = "Tiny KB collects facts about genes and diseases. It is updated every month by curators."
    assert online.match_value(value, page) == online.MATCH_PARTIAL


def test_no_match_for_paraphrase():
    assert online.match_value("A database of gene-disease facts", "Tiny KB collects facts about genes.") == online.MATCH_NONE


def test_javascript_shell_detection():
    shell = online.page_text("<html><body><noscript>You need to enable JavaScript to run this app.</noscript>"
                             "<div id='root'></div></body></html>")
    assert "JavaScript" in online.unreadable_reason(shell)
    assert online.unreadable_reason("Real content about genes. " * 40) is None


def test_bot_wall_detection():
    wall = "Comparative Toxicogenomic Database To continue, we need to verify you are a human. " * 10
    assert online.unreadable_reason(wall) == "blocks automated visitors"


def test_grounding_skips_maintainer_confirmed_values():
    croissant = {"name": "Tiny KB"}
    provenance = {"name": {"path": "name", "url": "https://github.com/x/y/issues/1", "source": "maintainer"}}
    findings, levels = online.check_grounding(croissant, provenance)
    assert findings == [] and levels == {}


def test_grounding_flags_missing_source_without_network():
    croissant = {"name": "Tiny KB"}
    findings, levels = online.check_grounding(croissant, {})
    assert levels == {"name": "no_source"}
    assert [f.check for f in findings] == ["grounding_no_source"]


def _dist(url, fmt):
    return {"@type": "cr:FileObject", "contentUrl": url, "encodingFormat": fmt}


def test_link_sample_covers_every_kind_of_file():
    # 500 surflex CSVs and 3 monthly zips in another folder: the zips must be sampled.
    dists = [_dist(f"https://b.org/data/surflex/{i}/{i}.csv", "text/csv") for i in range(500)]
    dists += [_dist(f"https://b.org/rwd/bind/downloads/All_2026{m:02d}.zip", "application/zip") for m in (7, 8, 9)]
    dists += [_dist("https://b.org/rwd/bind/report.pdf", "application/pdf")]
    sample, total, kinds = online.sample_links({"name": "x", "distribution": dists}, sample_size=10)
    urls = [u for _, u in sample]
    assert total == 504 and kinds == 3 and len(sample) == 10
    assert any(u.endswith(".zip") for u in urls) and any(u.endswith(".pdf") for u in urls)
    assert online.sample_links({"name": "x", "distribution": dists}, sample_size=10)[0] == sample  # deterministic


def test_link_sample_skips_archive_members_and_relative_links():
    dists = [_dist("https://b.org/a.zip", "application/zip"),
             {**_dist("inner.csv", "text/csv"), "containedIn": {"@id": "a"}}]
    sample, total, _ = online.sample_links({"name": "x", "distribution": dists})
    assert total == 1 and [u for _, u in sample] == ["https://b.org/a.zip"]
