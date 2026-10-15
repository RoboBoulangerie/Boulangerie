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
    assert groups["rel"]["children"][0]["name"] == "01-Jan-2026 … 01-May-2026"  # in date order
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


def test_kinds_of_tables_are_sets_of_columns_not_file_patterns():
    record_sets = [_rs(0, ["a", "b"], "s1"), _rs(1, ["a", "b", "c"], "s2"), _rs(2, ["a", "b"], "s3"),
                   _rs(3, ["x"], "m")]
    paths = {"s1": "scores › PGS000001.txt", "s2": "scores › PGS000002.txt", "s3": "scores › PGS000003.txt",
             "m": "metadata.csv"}
    kinds = organize.build_table_kinds(record_sets, paths, [False, True, False, False])
    # Same file pattern, different columns: two kinds. The one needing a look comes first.
    assert [(k["files"], [c[0] for c in k["columns"]]) for k in kinds] == [
        (["scores › PGS000002.txt"], ["a", "b", "c"]),
        (["metadata.csv"], ["x"]),
        (["scores › PGS000001.txt", "scores › PGS000003.txt"], ["a", "b"]),
    ]
    assert kinds[0]["look"] == 1
    assert kinds[2]["name"] == "scores › PGS*.txt"


def test_tables_with_the_same_columns_are_one_kind_even_with_unrelated_names():
    record_sets = [_rs(0, ["id", "name"], "a"), _rs(1, ["id", "name"], "b")]
    kinds = organize.build_table_kinds(record_sets, {"a": "human_x.csv", "b": "mouse_y.csv"}, [False, False])
    assert len(kinds) == 1 and len(kinds[0]["files"]) == 2


def test_family_names_only_mention_files_the_pattern_misses():
    record_sets = [_rs(i, ["a"], f"f{i}") for i in range(4)]
    paths = {"f0": "INTERACTION-GEN_FB.tsv.gz", "f1": "INTERACTION-MOL_HUMAN.tsv.gz",
             "f2": "INTERACTION-GEN_WB.tsv.gz", "f3": "INTERACTION-MOL_SARS-CoV-2.tsv.gz"}
    [kind] = organize.build_table_kinds(record_sets, paths, [False] * 4)
    assert kind["name"] == "INTERACTION-*.tsv.gz"  # the glob matches the odd one out too

    paths["f3"] = "summary.txt"
    [kind] = organize.build_table_kinds(record_sets, paths, [False] * 4)
    assert kind["name"].endswith("(and 1 file named differently)")


def test_trim_partial_word():
    assert organize.trim_partial_word("arabidopsis_prote", ["arabidopsis_protein", "arabidopsis_proteoform"]) == "arabidopsis_"
    assert organize.trim_partial_word("x-AcceptedA", ["x-AcceptedAndSubmitted", "x-AcceptedAssertions"]) == "x-Accepted"
    assert organize.trim_partial_word("CTD_chemicals", ["CTD_chemicals.csv", "CTD_chemicals_diseases.csv"]) == "CTD_chemicals"


def test_kinds_ignore_recorded_types_but_report_disagreements():
    def rs(i, ic50_type, file_id):
        return {"@id": f"rs{i}", "field": [
            {"name": "Compound", "dataType": "sc:Text", "source": {"fileObject": {"@id": file_id}, "extract": {"column": "Compound"}}},
            {"name": "IC50", "dataType": ic50_type, "source": {"fileObject": {"@id": file_id}, "extract": {"column": "IC50"}}},
        ]}
    record_sets = [rs(0, "sc:Integer", "a"), rs(1, "sc:Text", "b"), rs(2, "sc:Integer", "c")]
    paths = {"a": "x › 1A29.csv", "b": "x › 1AO8.csv", "c": "x › 1BCD.csv"}
    [kind] = organize.build_table_kinds(record_sets, paths, [False] * 3)
    assert kind["columns"] == [["Compound", "Text"], ["IC50", "Integer or Text"]]
    assert kind["types_vary"] == [{"column": "IC50", "types": [["Integer", 2], ["Text", 1]]}]


def test_files_in_two_places_are_named_by_file_and_places():
    paths = [f"rwd › surflex.tar › {i:04d}_X › {i:04d}.csv" for i in range(6)] + \
            [f"data › surflex › {i:04d}_X › {i:04d}.csv" for i in range(4)]
    record_sets = [_rs(i, ["a"], f"f{i}") for i in range(10)]
    kind = organize.build_table_kinds(record_sets, {f"f{i}": p for i, p in enumerate(paths)}, [False] * 10)[0]
    assert kind["name"] == "*.csv in rwd › surflex.tar › * and data › surflex › *"


def test_dated_names_sort_by_date():
    names = ["01-Apr-2016", "nightly", "01-May-2015", "01-Jul-2026"]
    dists = [_dist(i, f"https://x.org/{n}/s.tsv") for i, n in enumerate(names)]
    tree = organize.build_file_tree(dists, [False] * 4)
    assert [c["name"] for c in tree["children"]] == ["01-May-2015", "01-Apr-2016", "01-Jul-2026", "nightly"]
    assert organize.name_date("release-07-27-20").isoformat() == "2020-07-27"


def test_friendly_names_for_kinds_of_tables():
    assert organize.friendly_name("* › *-FeatureSummaries.tsv") == "Feature summaries (TSV)"
    assert organize.friendly_name("annotations › gaf › *.gaf.gz (and 1 file named differently)") == "Annotations (GAF)"
    assert organize.friendly_name("ontologies › ICF-d* › download?download_format=csv") == "Ontologies ICF"
    assert organize.friendly_name("INTERACTION-*.tsv.gz") == "Interaction (TSV)"
    assert organize.friendly_name("CTD_chem_gene_ixns.*.gz") == "CTD chem gene ixns"
    # When the pattern says nothing, the example file does.
    assert organize.friendly_name("release-* › output › *", "release-1 › output › added_data.tsv") == "Added data (TSV)"


def test_example_file_is_the_newest_when_files_are_dated():
    record_sets = [_rs(i, ["a"], f"f{i}") for i in range(3)]
    paths = {"f0": "01-Apr-2016 › 01-Apr-2016-X.tsv", "f1": "01-Jul-2026 › 01-Jul-2026-X.tsv",
             "f2": "01-May-2015 › 01-May-2015-X.tsv"}
    [kind] = organize.build_table_kinds(record_sets, paths, [False] * 3)
    assert kind["example"] == "01-Jul-2026 › 01-Jul-2026-X.tsv"


def test_friendly_names_handle_codes_repeats_and_places():
    assert organize.friendly_name("* › *-VariantGroupSummaries.tsv") == "Variant group summaries (TSV)"
    assert organize.friendly_name("scores › PGS* › ScoringFiles › Harmonized › PGS*_hmPOS_GRCh*.txt.gz") == \
        "Harmonized PGS hmPOS GRCh (TXT)"
    assert organize.friendly_name("*.csv in rwd › data › surflex › surflex.tar › * and data › surflex › *") == "Surflex (CSV)"
    assert organize.friendly_name("2026 › 01 › CADRE_Core_Data_Elements_2025_10_07.csv") == "CADRE core data elements (CSV)"


def test_files_of_a_kind_are_in_date_order():
    record_sets = [_rs(i, ["a"], f"f{i}") for i in range(3)]
    paths = {"f0": "01-Apr-2022 › x.tsv", "f1": "01-Aug-2021 › x.tsv", "f2": "01-Jan-2023 › x.tsv"}
    [kind] = organize.build_table_kinds(record_sets, paths, [False] * 3)
    assert kind["files"] == ["01-Aug-2021 › x.tsv", "01-Apr-2022 › x.tsv", "01-Jan-2023 › x.tsv"]
