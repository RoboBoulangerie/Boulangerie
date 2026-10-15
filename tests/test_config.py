from boulangerie import config


def test_pages_url_from_repo():
    assert config._pages_url("RoboBoulangerie/Boulangerie") == "https://roboboulangerie.github.io/Boulangerie/"
    assert config._pages_url("cbizon/boulangerie-sandbox") == "https://cbizon.github.io/boulangerie-sandbox/"


def test_only_copies_of_the_site_are_previews():
    assert config.MAIN_REPO == "RoboBoulangerie/Boulangerie"
    assert config.PREVIEW == (config.REPO != config.MAIN_REPO)
