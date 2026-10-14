"""Boulangerie: serve and check Croissant metadata for biomedical knowledge bases."""


def main() -> int:
    from boulangerie.cli import main as cli_main

    return cli_main()
