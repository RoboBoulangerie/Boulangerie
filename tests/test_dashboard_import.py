from boulangerie import dashboard_import

# Trimmed from a real dashboard /update page.
PAGE = """
<div>Page 1 of 3 &nbsp;·&nbsp; 1200 total fields</div>
<tr data-path="conformsTo"
    data-orig-value="x"
    data-current-value="http:&#x2F;&#x2F;mlcommons.org&#x2F;croissant&#x2F;1.1"
    data-current-source="http:&#x2F;&#x2F;mlcommons.org&#x2F;croissant&#x2F;1.1">
    <td><span class="conf-badge"><span class="badge badge-auto">⚙ Auto</span></span></td>
</tr>
<tr data-path="description"
    data-current-value="The Children&#x27;s Repository"
    data-current-source="https:&#x2F;&#x2F;cadre.med.wisc.edu&#x2F;">
    <td><span class="conf-badge">
        <span class="badge badge-high">100%</span>
    </span></td>
</tr>
<tr data-path="distribution[0].name" data-current-value="a.csv" data-current-source="https://x/a.csv">
    <td><span class="badge badge-medium">80%</span></td>
</tr>
"""


def test_parse_page():
    rows, pages = dashboard_import.parse_page(PAGE)
    assert pages == 3
    assert [r["path"] for r in rows] == ["conformsTo", "description", "distribution[0].name"]
    assert rows[0]["url"] == "http://mlcommons.org/croissant/1.1"
    assert rows[0]["confidence"] is None
    assert rows[1]["value"] == "The Children's Repository"
    assert rows[1]["confidence"] == 1.0
    assert rows[2]["confidence"] == 0.8
