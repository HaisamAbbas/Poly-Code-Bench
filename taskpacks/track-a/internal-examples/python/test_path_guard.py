"""Reproducer for the authored sibling-prefix path containment defect."""

from path_guard import is_within


def test_sibling_prefix_is_outside_root() -> None:
    assert not is_within("/srv/data", "/srv/data-secret/key")
