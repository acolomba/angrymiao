"""unit tests for angrymiao."""

from angrymiao import __version__
from angrymiao.__main__ import main


def test_version() -> None:
    """verifies the package version is set."""
    assert __version__ == "0.1.0"


def test_main() -> None:
    """verifies the main function runs successfully."""
    result = main()
    assert result == 0
