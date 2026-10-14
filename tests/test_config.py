from boulangerie import config


def test_pages_url_from_repo():
    assert config._pages_url("RoboBoulangerie/Boulangerie") == "https://roboboulangerie.github.io/Boulangerie/"
    assert config._pages_url("cbizon/boulangerie-sandbox") == "https://cbizon.github.io/boulangerie-sandbox/"
