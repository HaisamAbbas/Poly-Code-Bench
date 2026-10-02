"""PCB-21-1: the pinned-toolchain half of the C++ evaluator identity.

C++ has no lockfile, so ``config/languages/cpp-toolchain-v1.json`` *is* the lock. Its job is to
refuse, before anything is built or scored, the combinations the pinned toolchain cannot honour:
AddressSanitizer and ThreadSanitizer in one translation unit, a sanitizer the images do not carry,
a standard or compiler that is not pinned, and — the one that would quietly corrupt a score — a
sanitized build profile used as a release measurement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "languages" / "cpp" / "src"))

from polycodebench_lang_cpp.locks import (  # noqa: E402
    LOCK_FILE,
    TOOLCHAIN_VERSION,
    LockError,
    ToolchainLock,
    load_lock,
    lock_digest,
)
from polycodebench_lang_cpp.taskspec import parse_recipe  # noqa: E402

lock = load_lock()


# ------------------------------------------------------------------ the incompatible pair


@pytest.mark.parametrize("pair", [("address", "thread"), ("undefined", "thread")])
def test_the_two_runtime_incompatible_pairs_are_refused_either_order(pair: tuple[str, str]) -> None:
    """ASan and TSan cannot share a process, and neither can UBSan and TSan."""
    with pytest.raises(LockError, match="cannot be combined"):
        lock.check_sanitizers(pair)
    with pytest.raises(LockError, match="cannot be combined"):
        lock.check_sanitizers(pair[::-1])


def test_the_pair_that_can_share_a_translation_unit_is_accepted() -> None:
    """The control: refusing everything would make the sanitizer lane useless."""
    lock.check_sanitizers(("address", "undefined"))
    lock.check_sanitizers(())
    lock.check_sanitizers(("thread",))


def test_a_repeated_sanitizer_is_refused_rather_than_silently_deduplicated() -> None:
    with pytest.raises(LockError, match="repeats an entry"):
        lock.check_sanitizers(("address", "address"))
    with pytest.raises(LockError, match="repeats an entry"):
        lock.check_sanitizers(("address", "undefined", "address"))


@pytest.mark.parametrize(
    ("name", "error"),
    [("msan", "unknown sanitizer"), ("", "unknown sanitizer"), ("ADDRESS", "unknown sanitizer")],
)
def test_a_sanitizer_the_images_do_not_carry_is_refused(name: str, error: str) -> None:
    with pytest.raises(LockError, match=error):
        lock.check_sanitizers((name,))


def test_the_unknown_sanitizer_is_named_with_the_available_ones() -> None:
    with pytest.raises(LockError) as caught:
        lock.check_sanitizers(("leak",))
    message = str(caught.value)
    assert "'leak'" in message
    for available in lock.available_instrumentation:
        assert available in message


# ------------------------------------------------------------------------ unknown lookups


@pytest.mark.parametrize(
    ("lookup", "error", "argument"),
    [
        (lock.compiler, "is not pinned", "gcc"),
        (lock.compiler, "is not pinned", "clang-15"),
        (lock.standard_flag, r"language standard 'c\+\+23' is not pinned", "c++23"),
        (lock.standard_flag, "language standard 'c11' is not pinned", "c11"),
        (lock.build_profile, "build profile 'relwithdebinfo' is not pinned", "relwithdebinfo"),
        (lock.sanitizer, "sanitizer 'msan' is not pinned", "msan"),
        (lock.analyzer, "analyzer invocation 'iwyu' is not pinned", "iwyu"),
    ],
)
def test_every_unpinned_name_is_refused_rather_than_approximated(
    lookup: object, error: str, argument: str
) -> None:
    with pytest.raises(LockError, match=error):
        lookup(argument)  # type: ignore[operator]


def test_a_compiler_is_pinned_but_gcc_is_not_available_in_these_images() -> None:
    """`compilers` documents gcc; `available_compilers` says the images ship only clang."""
    assert lock.compilers["gcc"].binary == "g++"
    assert "gcc" not in lock.available_compilers
    with pytest.raises(LockError, match="compiler 'gcc' is not pinned"):
        lock.compiler("gcc")


def test_the_refusal_lists_what_is_available() -> None:
    with pytest.raises(LockError) as caught:
        lock.build_profile("fast")
    message = str(caught.value)
    assert "'fast'" in message
    for profile in sorted(lock.build_profiles):
        assert f"'{profile}'" in message


# ------------------------------------------------------------------- profile resolution


def test_profile_for_returns_the_pinned_profile_carrying_exactly_its_sanitizer_flag() -> None:
    address = lock.profile_for(("address",))
    both = lock.profile_for(("address", "undefined"))
    thread = lock.profile_for(("thread",))
    assert address is both
    assert address.cxxflags == lock.build_profiles["sanitize_address_undefined"].cxxflags
    assert address.ldflags == ("-fsanitize=address,undefined",)
    assert address.sanitized and not address.release
    assert thread.cxxflags == lock.build_profiles["sanitize_thread"].cxxflags
    assert thread.ldflags == ("-fsanitize=thread",)


def test_an_unsanitized_request_is_the_debug_profile_not_a_sanitized_one() -> None:
    profile = lock.profile_for(())
    assert profile is lock.build_profiles["debug"]
    assert not profile.sanitized and not profile.release
    assert not any("sanitize" in flag for flag in profile.cxxflags)


def test_profile_for_refuses_a_pair_before_it_looks_any_profile_up() -> None:
    """The compatibility check is not bypassed by asking for a profile instead of a check."""
    with pytest.raises(LockError, match="cannot be combined"):
        lock.profile_for(("thread", "address"))


def test_the_release_profile_is_the_measurement_profile_and_nothing_else() -> None:
    release = lock.release_profile()
    assert release is lock.build_profiles["release"]
    assert release.release and not release.sanitized
    assert not any("sanitize" in flag for flag in (*release.cxxflags, *release.ldflags))
    # -O3 with NDEBUG, which is what the performance image bakes as well.
    assert "-O3" in release.cxxflags and "-DNDEBUG" in release.cxxflags


def test_the_sanitizer_environment_is_the_union_of_the_chosen_instrumentation() -> None:
    assert lock.sanitizer_environment(()) == {}
    both = lock.sanitizer_environment(("address", "undefined"))
    assert set(both) == {"ASAN_OPTIONS", "UBSAN_OPTIONS"}
    assert "detect_leaks=1" in both["ASAN_OPTIONS"]
    assert "halt_on_error=1" in both["UBSAN_OPTIONS"]
    thread = lock.sanitizer_environment(("thread",))
    assert set(thread) == {"TSAN_OPTIONS"}
    assert lock.sanitizer_environment(("address", "thread")) == {**both, **thread}


def test_the_detector_is_what_distinguishes_the_two_shared_runtimes() -> None:
    """ASan and UBSan share one build profile; only the detector separates them."""
    assert lock.detector("address") == "asan"
    assert lock.detector("undefined") == "ubsan"
    assert lock.detector("thread") == "tsan"
    assert lock.sanitizer("address").build_profile == lock.sanitizer("undefined").build_profile
    assert lock.sanitizer("thread").build_profile != lock.sanitizer("address").build_profile


# ------------------------------------------------------------------------- the lock digest


def test_the_digest_is_a_well_formed_sha256_over_the_whole_document() -> None:
    digest = lock_digest()
    assert digest.startswith("sha256:") and len(digest) == 71
    int(digest[7:], 16)


def test_the_digest_is_stable_across_loads_and_reordering_moves_it() -> None:
    """Same document -> same identity; different document -> different identity."""
    again = load_lock()
    assert again is lock or again.digest == lock.digest
    assert lock_digest(again) == lock_digest()

    document = json.loads((ROOT / LOCK_FILE).read_text("utf-8"))
    reordered = json.dumps(dict(sorted(document.items())), indent=1).encode()
    # Formatting is not a change of identity.
    assert lock_digest(ToolchainLock.model_validate(json.loads(reordered))) == lock_digest()

    moved = json.loads(reordered)
    moved["default_standard"] = "c++17"
    assert lock_digest(ToolchainLock.model_validate(moved)) != lock_digest()


def test_a_change_to_a_sanitizer_flag_moves_the_identity() -> None:
    document = json.loads((ROOT / LOCK_FILE).read_text("utf-8"))
    flags = document["build_profiles"]["sanitize_address_undefined"]["cxxflags"]
    flags[-1] = "-fno-sanitize-recover=address"
    assert lock_digest(ToolchainLock.model_validate(document)) != lock_digest()


def test_the_digest_reaches_the_tool_identity_of_a_real_plan() -> None:
    """End of the DoD: lock -> `ToolIdentity.lock_digest`, the field comparability is judged on."""
    from polycodebench_lang_cpp.identities import load_identities

    identities = load_identities()
    tool = identities.tool("clang-tidy", lock=lock)
    assert tool.lock_digest == lock_digest()
    # Without an explicit lock the identity falls back to the image's own recipe digest.
    assert identities.tool("clang-tidy").lock_digest == identities.evaluator.recipe_digest


def test_a_lock_that_declares_a_version_this_code_does_not_know_is_refused(tmp_path: Path) -> None:
    """A newer lock is refused: its rules may not be the ones implemented here."""
    document = json.loads((ROOT / LOCK_FILE).read_text("utf-8"))
    document["toolchain_version"] = "cpp-toolchain-v2"
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(LockError, match="expected 'cpp-toolchain-v1'"):
        load_lock(str(path))


def test_an_incompatible_pair_naming_an_unpinned_sanitizer_is_refused(tmp_path: Path) -> None:
    document = json.loads((ROOT / LOCK_FILE).read_text("utf-8"))
    document["incompatible_instrumentation"] = [["address", "leak"]]
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(LockError, match="unpinned sanitizer"):
        load_lock(str(path))


def test_the_pinned_document_is_strict_about_anything_it_does_not_declare() -> None:
    document = json.loads((ROOT / LOCK_FILE).read_text("utf-8"))
    document["surprise"] = 1
    with pytest.raises(Exception, match="Extra inputs"):
        ToolchainLock.model_validate(document)


# ------------------------------------------------------- a recipe may not escape the lock


def test_the_shipped_recipe_passes_the_lock() -> None:
    from cpp_plugin_support import TOP_WORDS

    parse_recipe((TOP_WORDS / "hidden/recipe.json").read_bytes()).check(lock)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("build_profile", "release", "release measurement profile"),
        ("build_profile", "sanitize_address_undefined", "instrumentation"),
        ("build_profile", "sanitize_thread", "instrumentation"),
        ("compiler", "gcc", "is not pinned"),
        ("standard", "c++23", "language standard"),
    ],
)
def test_cpp_recipe_check_refuses_a_recipe_the_lock_does_not_allow(
    field: str, value: str, error: str
) -> None:
    from cpp_plugin_support import TOP_WORDS

    recipe = parse_recipe((TOP_WORDS / "hidden/recipe.json").read_bytes())
    recipe = recipe.model_copy(update={field: value})
    with pytest.raises(LockError, match=error):
        recipe.check(lock)


def test_the_documented_instrumentation_rule_is_the_one_the_refusals_quote() -> None:
    """The prose and the table must not drift: a refusal quotes the rule it enforces."""
    pairs = {tuple(sorted(pair)) for pair in lock.incompatible_instrumentation}
    assert pairs == {("address", "thread"), ("thread", "undefined")}
    # `thread` is the exclusive one, so the prose has to name it alongside AddressSanitizer.
    assert "ThreadSanitizer" in lock.instrumentation_rule
    assert "AddressSanitizer" in lock.instrumentation_rule
    for left, right in sorted(pairs):
        with pytest.raises(LockError) as caught:
            lock.check_sanitizers((left, right))
        # The refusal quotes the pinned rule verbatim, so an author reads why, not just what.
        assert lock.instrumentation_rule in str(caught.value)


def test_the_lock_names_the_compilers_standards_and_detectors_it_claims_to_pin() -> None:
    assert TOOLCHAIN_VERSION == lock.toolchain_version
    assert set(lock.available_compilers) <= set(lock.compilers)
    assert set(lock.standards) >= {"c++17", "c++20"}
    assert lock.default_standard in lock.standards
    assert lock.standard_flag(lock.default_standard) == lock.standards[lock.default_standard].flag
    assert set(lock.available_instrumentation) == set(lock.instrumentation)
    assert set(lock.sanitizer_report.detectors) == {"asan", "ubsan", "tsan"}
    assert "unsupported" in lock.sanitizer_report.verdicts
    assert lock.sanitizer_report.report_schema == "pcb-cpp-sanitizer-v1"
