"""A temporary fixture for a default-token screen observation; not a real test."""

from pathlib import Path


def test_checkout_is_pinned():
    text = Path(".github/workflows/tests.yml").read_text()
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text


def test_setup_python_is_pinned():
    text = Path(".github/workflows/actionlint.yml").read_text()
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text
