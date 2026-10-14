from boulangerie import checks, review


def test_item_of_maps_paths_to_ids(tiny_croissant):
    assert review.item_of("distribution[1].contentUrl", tiny_croissant) == "file-readme"
    assert review.item_of("recordSet[0].field[1].source", tiny_croissant) == "genes"
    assert review.item_of("license", tiny_croissant) is None
    assert review.item_of("distribution[99].name", tiny_croissant) is None


def test_build_review_ranks_likely_problems_first(tiny_croissant):
    confidence = {
        "file-genes-tsv": {"confidence": 1.0, "values": 3, "reviewed": 3, "unsure": []},
        "file-readme": {"confidence": 0.5, "values": 2, "reviewed": 0,
                        "unsure": [{"path": "name", "value": "README.pdf", "confidence": 0.5}]},
    }
    data = review.build_review(tiny_croissant, confidence, checks.run_offline_checks(tiny_croissant))

    files = data["files"]
    # genes.tsv has a format mismatch (a warning), so it outranks the low-confidence README.
    assert [f["id"] for f in sorted(files, key=lambda f: f["rank"])] == ["file-genes-tsv", "file-readme"]
    genes, readme = sorted(files, key=lambda f: f["order"])
    assert genes["look"] and readme["look"]
    assert genes["issues"][0]["title"] == "A file's type doesn't match its name"
    assert genes["kind"] == "CSV spreadsheet" and genes["size"] == "1.5 MB"
    assert readme["unsure"][0] == {"part": "name", "value": "README.pdf", "confidence": 0.5}

    [table] = data["tables"]
    assert table["issues"][0]["severity"] == checks.PROBLEM
    assert data["counts"] == {"files": 2, "tables": 1, "files_look": 2, "tables_look": 1,
                              "families": 1, "families_look": 1}

    # Tables are shown by family, with their columns and the files they come from.
    [family] = data["families"]
    [layout] = family["layouts"]
    assert layout["columns"] == [["symbol", "Text"], ["where", "Text"]]
    assert layout["files"] == ["file-missing", "genes.tsv"]  # unknown ids are shown as-is
    assert family["look"] == 1

    # Files are shown in their folders: example.org › downloads › genes.tsv
    assert data["tree"]["name"] == "example.org › downloads"
    assert [c["name"] for c in data["tree"]["children"]] == ["genes.tsv", "README.pdf"]
    assert {f["path"] for f in files} == {"genes.tsv", "README.pdf"}


def test_nothing_flagged_when_all_is_well():
    croissant = {"distribution": [{"@id": "a", "@type": "cr:FileObject", "name": "a.csv",
                                   "contentUrl": "https://x/a.csv", "encodingFormat": "text/csv",
                                   "sha256": "0" * 64}]}
    data = review.build_review(croissant, {"a": {"confidence": 0.99, "values": 1, "reviewed": 0, "unsure": []}}, [])
    assert data["files"][0]["look"] is False


def test_describe_part():
    fields = [{"name": "chrom"}, {"name": "pos"}]
    assert review.describe_part("field[1].dataType", fields) == "pos · kind of value"
    assert review.describe_part("field[0]", fields) == "chrom"
    assert review.describe_part("encodingFormat") == "file type"
    assert review.describe_part("somethingElse") == "somethingElse"
