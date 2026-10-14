from boulangerie import organize


def _dist(i, url, **extra):
    return {"@id": f"f{i}", "@type": "cr:FileObject", "contentUrl": url, "contentSize": "1000 B", **extra}


def test_location_uses_url_folders_and_archive_contents():
    dists = [_dist(0, "https://x.org/a/b/data.zip"), _dist(1, "data.zip!/inner/t.csv", containedIn={"@id": "f0"})]
    by_id = {d["@id"]: d for d in dists}
    assert organize.location(dists[0], by_id) == ["x.org", "a", "b", "data.zip"]
    assert organize.location(dists[1], by_id) == ["x.org", "a", "b", "data.zip", "inner", "t.csv"]


def test_tree_collapses_chains_and_counts():
    dists = [_dist(0, "https://x.org/pub/db/one.csv"), _dist(1, "https://x.org/pub/db/sub/two.csv")]
    tree = organize.build_file_tree(dists, [True, False])
    assert tree["name"] == "x.org › pub › db"
    assert (tree["files"], tree["size"], tree["look"]) == (2, 2000, 1)
    assert [c["name"] for c in tree["children"]] == ["sub", "one.csv"]  # folders first


def test_numbered_folders_and_month_folders_are_grouped():
    dists = [_dist(i, f"https://x.org/scores/PGS{i:06d}/PGS{i:06d}.txt.gz") for i in range(6)]
    dists += [_dist(10 + i, f"https://x.org/rel/01-{m}-2026/s.tsv") for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May"])]
    tree = organize.build_file_tree(dists, [False] * len(dists))
    groups = {c["name"]: c for c in tree["children"]}
    assert groups["scores"]["children"][0]["type"] == "group"
    assert groups["scores"]["children"][0]["count"] == 6
    # "*-*-*" says nothing, so a group of dated folders is labelled with its range.
    assert groups["rel"]["children"][0]["name"] == "01-Apr-2026 … 01-May-2026"
    assert groups["rel"]["children"][0]["count"] == 5


def test_flat_folders_group_by_family_and_merge_formats():
    names = [f"CTD_{w}.{ext}.gz" for w in ("anatomy", "chem_gene_ixns", "chem_go", "genes", "genes_diseases",
                                             "genes_pathways", "pathways", "chemicals") for ext in ("csv", "tsv", "xml")]
    dists = [_dist(i, f"https://ctdbase.org/reports/{n}") for i, n in enumerate(names)]
    tree = organize.build_file_tree(dists, [False] * len(dists))
    families = {c["name"]: c for c in tree["children"]}
    assert set(families) >= {"CTD_anatomy.*", "CTD_chem_*", "CTD_genes*"}
    anatomy = families["CTD_anatomy.*"]["children"][0]
    assert anatomy["type"] == "formats" and anatomy["formats"] == ["CSV", "TSV", "XML"]


def test_family_names_stop_at_word_boundaries():
    names = [f"mouse_protein_{i}_x.csv" for i in range(11)] + [f"mouse_proteoform_{i}.csv" for i in range(11)]
    names += [f"rat_protein_{i}_x.csv" for i in range(11)]
    dists = [_dist(i, f"https://g.org/d/{n}") for i, n in enumerate(names)]
    tree = organize.build_file_tree(dists, [False] * len(dists))
    assert "mouse_*" in [c["name"] for c in tree["children"]]


def test_dataset_stem():
    assert organize.dataset_stem("CTD_anatomy.csv.gz") == ("CTD_anatomy", "CSV")
    assert organize.dataset_stem("hgnc.owl") == ("hgnc", "OWL")
    assert organize.dataset_stem("README") == ("README", "")


def _rs(i, cols, file_id):
    return {"@id": f"rs{i}", "field": [{"name": c, "dataType": "sc:Text",
                                        "source": {"fileObject": {"@id": file_id}, "extract": {"column": c}}}
                                       for c in cols]}


def test_table_families_group_by_path_pattern_and_layout():
    record_sets = [_rs(0, ["a", "b"], "s1"), _rs(1, ["a", "b", "c"], "s2"), _rs(2, ["a", "b"], "s3"),
                   _rs(3, ["x"], "m")]
    paths = {"s1": "scores › PGS000001.txt", "s2": "scores › PGS000002.txt", "s3": "scores › PGS000003.txt",
             "m": "metadata.csv"}
    families = organize.build_table_families(record_sets, paths, [False, True, False, False])
    scores, meta = families  # the one needing a look comes first
    assert scores["name"] == "scores › PGS*.txt"
    assert scores["look"] == 1 and scores["files"] == 3
    assert scores["common"] == [["a", "Text"], ["b", "Text"]]
    assert [l["extra"] for l in scores["layouts"]] == [[["c", "Text"]], []]
    assert meta["name"] == "metadata.csv" and len(meta["layouts"]) == 1


def test_tables_with_the_same_columns_share_a_family_even_with_unrelated_names():
    record_sets = [_rs(0, ["id", "name"], "a"), _rs(1, ["id", "name"], "b")]
    families = organize.build_table_families(record_sets, {"a": "human_x.csv", "b": "mouse_y.csv"}, [False, False])
    assert len(families) == 1 and families[0]["files"] == 2


def test_family_names_only_mention_files_the_pattern_misses():
    record_sets = [_rs(i, ["a"], f"f{i}") for i in range(4)]
    paths = {"f0": "INTERACTION-GEN_FB.tsv.gz", "f1": "INTERACTION-MOL_HUMAN.tsv.gz",
             "f2": "INTERACTION-GEN_WB.tsv.gz", "f3": "INTERACTION-MOL_SARS-CoV-2.tsv.gz"}
    [family] = organize.build_table_families(record_sets, paths, [False] * 4)
    assert family["name"] == "INTERACTION-*.tsv.gz"  # the glob matches the odd one out too

    paths["f3"] = "summary.txt"
    [family] = organize.build_table_families(record_sets, paths, [False] * 4)
    assert family["name"].endswith("(and 1 file named differently)")


def test_trim_partial_word():
    assert organize.trim_partial_word("arabidopsis_prote", ["arabidopsis_protein", "arabidopsis_proteoform"]) == "arabidopsis_"
    assert organize.trim_partial_word("x-AcceptedA", ["x-AcceptedAndSubmitted", "x-AcceptedAssertions"]) == "x-Accepted"
    assert organize.trim_partial_word("CTD_chemicals", ["CTD_chemicals.csv", "CTD_chemicals_diseases.csv"]) == "CTD_chemicals"


def test_layouts_ignore_recorded_types_but_report_disagreements():
    def rs(i, ic50_type, file_id):
        return {"@id": f"rs{i}", "field": [
            {"name": "Compound", "dataType": "sc:Text", "source": {"fileObject": {"@id": file_id}, "extract": {"column": "Compound"}}},
            {"name": "IC50", "dataType": ic50_type, "source": {"fileObject": {"@id": file_id}, "extract": {"column": "IC50"}}},
        ]}
    record_sets = [rs(0, "sc:Integer", "a"), rs(1, "sc:Text", "b"), rs(2, "sc:Integer", "c")]
    paths = {"a": "x › 1A29.csv", "b": "x › 1AO8.csv", "c": "x › 1BCD.csv"}
    [family] = organize.build_table_families(record_sets, paths, [False] * 3)
    [layout] = family["layouts"]
    assert layout["columns"] == [["Compound", "Text"], ["IC50", "Integer or Text"]]
    assert layout["types_vary"] == [{"column": "IC50", "types": [["Integer", 2], ["Text", 1]]}]


def test_files_in_two_places_are_named_by_file_and_places():
    paths = [f"rwd › surflex.tar › {i:04d}_X › {i:04d}.csv" for i in range(6)] + \
            [f"data › surflex › {i:04d}_X › {i:04d}.csv" for i in range(4)]
    record_sets = [_rs(i, ["a"], f"f{i}") for i in range(10)]
    family = organize.build_table_families(record_sets, {f"f{i}": p for i, p in enumerate(paths)}, [False] * 10)[0]
    assert family["name"] == "*.csv in rwd › surflex.tar › * and data › surflex › *"
