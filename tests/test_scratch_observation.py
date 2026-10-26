"""A temporary fixture for a two-head behavioural observation; not a real test."""

from pathlib import Path


def test_checkout_is_pinned():
    text = Path(".github/workflows/tests.yml").read_text()
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text


def test_setup_python_is_pinned():
    text = Path(".github/workflows/actionlint.yml").read_text()
    assert "actions/setup-python@e797f83b8e4e4a7f8e5d76a1b2c3d4e5f6071829" in text

# A trailing comment line moves the head for the third observation run.
