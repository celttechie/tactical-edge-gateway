"""Foundational smoke tests for Tactical Edge Gateway."""

import tactical_edge_gateway


def test_package_metadata():
    """Verify package version metadata is initialized."""
    assert tactical_edge_gateway.__version__ == "0.1.0"
