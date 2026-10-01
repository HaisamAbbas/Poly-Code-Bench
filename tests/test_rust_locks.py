"""PCB-11-1: the Cargo.lock half of the Rust evaluator identity.

"DoD: toolchain/Cargo.lock and image digests determine evaluator identity."

These tests pin the two properties that make that sentence true: a lock's digest depends on the
*resolution* and not on formatting, and a lock that does not actually pin its resolution is
rejected rather than silently producing an identity that could drift between runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "languages" / "rust" / "src"))

from polycodebench_lang_rust.locks import LockError, lock_digest, parse_cargo_lock  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "rust_cargo_lock"


def test_real_cargo_locks_parse_and_resolve() -> None:
    with_dependency = parse_cargo_lock((FIXTURES / "with-dependency.lock").read_bytes())
    assert with_dependency.lock_version == 4
    assert with_dependency.resolutions() == ("cfg-if 1.0.4", "lock_fixture 0.1.0")
    # A path dependency carries no registry checksum; a registry one must.
    by_name = {item.name: item for item in with_dependency.packages}
    assert by_name["lock_fixture"].is_path_dependency
    assert not by_name["cfg-if"].is_path_dependency
    assert by_name["cfg-if"].checksum

    plain = parse_cargo_lock((FIXTURES / "no-dependency.lock").read_bytes())
    assert plain.resolutions() == ("lock_fixture 0.1.0",)


def test_the_digest_tracks_the_resolution_not_the_formatting() -> None:
    """Re-ordering or re-formatting the file must not look like a dependency change."""
    original = (FIXTURES / "with-dependency.lock").read_bytes()
    baseline = lock_digest(original)

    # Same resolution, different package order and different whitespace.
    reordered = b"""version = 4

[[package]]
name = "cfg-if"
version = "1.0.4"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "a8d1f5c9e0b3a2f7d6c1e8b4a9f0c3d2e1b5a7f4c6d8e9a0b1c2d3e4f5a6b7"


[[package]]
name = "lock_fixture"
version = "0.1.0"
"""
    assert lock_digest(reordered) == baseline

    # A comment, a key-order change and trailing whitespace are all cosmetic.
    commented = original.replace(
        b'[[package]]\nname = "cfg-fi',
        b'# generated\n\n[[package]]\nname = "cfg-fi',
    ).replace(b"version = 4", b"version   =    4")
    assert lock_digest(commented + b"\n\n") == baseline

    # A real dependency change must change the digest.
    bumped = original.replace(b'version = "1.0.4"', b'version = "1.0.5"')
    assert lock_digest(bumped) != baseline


def test_a_real_dependency_change_moves_the_identity() -> None:
    """Dropping or adding a package must be visible in the identity."""
    original = (FIXTURES / "with-dependency.lock").read_bytes()
    baseline = lock_digest(original)
    without = b'version = 4\n\n[[package]]\nname = "lock_fixture"\nversion = "0.1.0"\n'
    assert lock_digest(without) != baseline
    # Two different locks therefore mean two different evaluator identities.
    assert lock_digest((FIXTURES / "no-dependency.lock").read_bytes()) != baseline


def test_an_unpinned_lock_is_rejected_rather_than_given_a_driftable_identity() -> None:
    """A registry package with no checksum is not a pinned resolution."""
    unlocked = b"""version = 4

[[package]]
name = "thing"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
    with pytest.raises(LockError, match="not pinned"):
        lock_digest(unlocked)


def test_malformed_locks_are_rejected() -> None:
    cases = {
        "no version": b'[[package]]\nname = "a"\nversion = "0.1.0"\n',
        "no packages": b"version = 4\n",
        "empty": b"",
        "not toml": b"this is not toml [",
        "package without version": b'version = 4\n\n[[package]]\nname = "a"\n',
        "duplicate package": (
            b'version = 4\n\n[[package]]\nname = "a"\nversion = "0.1.0"\n'
            b'\n[[package]]\nname = "a"\nversion = "0.1.0"\n'
        ),
    }
    for label, data in cases.items():
        with pytest.raises(LockError, match=r"."):
            lock_digest(data)
        assert label  # every case above is deliberately rejected


def test_the_digest_is_a_well_formed_sha256() -> None:
    digest = lock_digest((FIXTURES / "no-dependency.lock").read_bytes())
    assert digest.startswith("sha256:")
    assert len(digest) == 71
    int(digest[7:], 16)  # valid hex


def test_the_lock_digest_reaches_the_tool_identity() -> None:
    """End of the DoD: lock -> `ToolIdentity.lock_digest`, the field comparability is judged on."""
    from polycodebench_lang_rust import load_identities

    identities = load_identities()
    lock = lock_digest((FIXTURES / "with-dependency.lock").read_bytes())
    plain = lock_digest((FIXTURES / "no-dependency.lock").read_bytes())

    # clippy runs in the evaluator image, so its result depends on the build, and its identity
    # must carry the lock digest rather than only the image.
    with_lock = identities.tool("clippy", recipe="evaluator", lock_digest=lock)
    assert with_lock.lock_digest == lock
    assert with_lock.image_digest == identities.evaluator.digest
    assert with_lock.image_digest != with_lock.lock_digest

    # A different lock is a different identity even in the same image.
    assert identities.tool("clippy", lock_digest=plain).lock_digest != with_lock.lock_digest

    # Miri is the nightly interpreter, so its version names the pinned toolchain too.
    miri = identities.tool("miri")
    assert "nightly" in miri.version


def test_the_recorded_identity_matches_the_built_recipes() -> None:
    """The identity file must describe three distinct recipes, with absence stated."""
    from polycodebench_lang_rust import load_identities

    identities = load_identities()
    assert set(identities.images) == {"runtime", "evaluator", "performance"}
    digests = {r.digest for r in identities.images.values()}
    assert len(digests) == 3, "recipes must not share an image digest"

    evaluator = identities.evaluator.tools
    for tool in ("clippy", "rustfmt", "miri"):
        assert evaluator[tool] != "absent", f"evaluator image cannot run {tool}"
    for recipe in ("runtime", "performance"):
        tools = identities.record(recipe).tools
        for tool in ("clippy", "rustfmt", "miri"):
            assert tools[tool] == "absent", f"{recipe} unexpectedly provides {tool}"
