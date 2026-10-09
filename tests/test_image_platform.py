"""Host-architecture selection of pinned image files (amd64 unchanged, arm64 siblings)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from polycodebench_core.image_platform import (
    architecture_variant,
    architecture_variant_name,
    docker_platform,
    host_image_architecture,
    normalize_architecture,
)
from polycodebench_lang_python import identities as python_identities
from polycodebench_lang_rust import identities as rust_identities
from polycodebench_orchestration import worker_cli
from polycodebench_orchestration.grading.worker_runtime import load_grading_image_allowlist

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{name}_for_platform_tests", ROOT / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PCB_IMAGE_ARCH", raising=False)


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "amd64"),
        ("AMD64", "amd64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("linux/arm64", "arm64"),
        ("linux/amd64", "amd64"),
    ],
)
def test_machine_names_map_to_pin_families(machine: str, expected: str) -> None:
    assert normalize_architecture(machine) == expected
    assert host_image_architecture(machine) == expected


def test_unknown_machine_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="unsupported image architecture"):
        host_image_architecture("riscv64")


def test_override_wins_over_the_detected_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCB_IMAGE_ARCH", "arm64")
    assert host_image_architecture("x86_64") == "arm64"
    monkeypatch.setenv("PCB_IMAGE_ARCH", "sparc")
    with pytest.raises(ValueError):
        host_image_architecture("x86_64")


def test_amd64_paths_are_unchanged_and_arm64_reads_the_suffixed_sibling() -> None:
    path = Path("config/images/python-v1.json")
    assert architecture_variant(path, "amd64") == path
    assert architecture_variant(path, "arm64") == Path("config/images/python-v1-arm64.json")
    assert (
        architecture_variant_name("config/plugins/allowlist-v1.yaml", "arm64")
        == "config/plugins/allowlist-v1-arm64.yaml"
    )
    assert docker_platform("arm64") == "linux/arm64"


@pytest.mark.parametrize(
    ("module", "stem"), [(python_identities, "python-v1"), (rust_identities, "rust-v1")]
)
def test_plugin_identity_file_follows_the_host_architecture(
    module: ModuleType, stem: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images = tmp_path / "config" / "images"
    images.mkdir(parents=True)
    (images / f"{stem}.json").write_text("{}", encoding="utf-8")
    (images / f"{stem}-arm64.json").write_text("{}", encoding="utf-8")
    nested = tmp_path / "plugins" / "x"
    nested.mkdir(parents=True)
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    assert module._find(nested) == images / f"{stem}.json"
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    assert module._find(nested) == images / f"{stem}-arm64.json"
    (images / f"{stem}-arm64.json").unlink()
    # An arm64 host never falls back to amd64 digests it cannot run.
    with pytest.raises(FileNotFoundError):
        module._find(nested)


def _identity(path: Path, repository: str, digest_char: str) -> None:
    digest = "sha256:" + digest_char * 64
    path.write_text(
        json.dumps(
            {"images": {"runtime": {"reference": f"{repository}@{digest}", "digest": digest}}}
        ),
        encoding="utf-8",
    )


def test_grading_allowlist_reads_only_this_architectures_identities(tmp_path: Path) -> None:
    _identity(tmp_path / "python-v1.json", "pcb-python-runtime", "a")
    _identity(tmp_path / "python-v1-arm64.json", "pcb-python-runtime", "b")
    _identity(tmp_path / "go-v1.json", "pcb-go-runtime", "c")
    assert load_grading_image_allowlist(tmp_path, architecture="amd64") == {
        "pcb-python-runtime@sha256:" + "a" * 64: "sha256:" + "a" * 64,
        "pcb-go-runtime@sha256:" + "c" * 64: "sha256:" + "c" * 64,
    }
    assert load_grading_image_allowlist(tmp_path, architecture="arm64") == {
        "pcb-python-runtime@sha256:" + "b" * 64: "sha256:" + "b" * 64,
    }
    (tmp_path / "python-v1-arm64.json").unlink()
    with pytest.raises(ValueError, match="no versioned language image identities"):
        load_grading_image_allowlist(tmp_path, architecture="arm64")


def test_repository_grading_allowlist_is_unchanged_on_amd64() -> None:
    images = ROOT / "config" / "images"
    amd64 = load_grading_image_allowlist(images, architecture="amd64")
    python = json.loads((images / "python-v1.json").read_text(encoding="utf-8"))["images"]
    assert {record["reference"] for record in python.values()} <= set(amd64)


def test_worker_defaults_on_this_host_match_its_architecture() -> None:
    arch = host_image_architecture()
    assert worker_cli.DEFAULT_IMAGE_ALLOWLIST == architecture_variant(
        worker_cli.ROOT / "config/worker/local-image-allowlist.json", arch
    )
    assert worker_cli.DEFAULT_PLUGIN_ALLOWLIST == architecture_variant(
        worker_cli.ROOT / "config/plugins/allowlist-v1.yaml", arch
    )
    assert worker_cli.DEFAULT_RESOURCE_SPEC == architecture_variant(
        worker_cli.ROOT / "config/worker/local-small-resource.json", arch
    )


@pytest.mark.parametrize(
    "name", ["build_python_images", "build_rust_images", "fetch_rust_components"]
)
def test_build_scripts_default_to_amd64_and_write_only_suffixed_files_for_arm64(name: str) -> None:
    script = _script(name)
    script.select_target([])
    assert script.TARGET.native and script.TARGET.platform == "linux/amd64"
    assert script.TARGET.sibling(script.OUTPUT) == script.OUTPUT
    script.select_target(["--platform", "linux/arm64"])
    assert not script.TARGET.native
    assert script.TARGET.sibling(script.OUTPUT).name.endswith("-arm64.json")
    assert script.TARGET.sibling(script.OUTPUT) != script.OUTPUT
    with pytest.raises(SystemExit):
        script.select_target(["--platform", "linux/s390x"])


def test_python_build_command_is_unchanged_for_amd64_and_buildx_for_arm64() -> None:
    script = _script("build_python_images")
    lock = script.IMAGES / "runtime.lock"
    script.select_target([])
    amd64 = script.build_command("pcb-python-runtime:v1", lock, Path("ctx"))
    assert amd64[:2] == ["docker", "build"] and "--platform" not in amd64
    assert f"BASE_IMAGE={script.BASE_IMAGE}" in amd64
    script.select_target(["--platform", "linux/arm64"])
    arm64 = script.build_command("pcb-python-runtime:v1-arm64", lock, Path("ctx"))
    assert arm64[:5] == ["docker", "buildx", "build", "--platform", "linux/arm64"]
    assert {"--load", "--provenance=false", "--sbom=false"} <= set(arm64)
    assert ["--network", "none"] == arm64[arm64.index("--network") : arm64.index("--network") + 2]
    assert f"BASE_IMAGE={script.BASE_IMAGE}" not in arm64


def test_rust_dockerfile_rewrite_targets_the_aarch64_interpreter_only() -> None:
    script = _script("build_rust_images")
    original = (script.IMAGES / "Dockerfile").read_text(encoding="utf-8")
    script.select_target([])
    assert script.platform_dockerfile(original) == original
    script.select_target(["--platform", "linux/arm64"])
    rewritten = script.platform_dockerfile(original)
    assert "x86_64-linux-gnu" not in rewritten and "ld-linux-x86-64" not in rewritten
    assert "/usr/lib/aarch64-linux-gnu" in rewritten
    assert "/opt/pcb/python/lib/ld-linux-aarch64.so.1 --library-path" in rewritten
    # Nothing else in the recipe changes.
    assert rewritten.count("\n") == original.count("\n")


def test_recorded_arm64_pins_are_digest_pinned_and_disjoint_from_amd64() -> None:
    from polycodebench_orchestration.solve.worker_runtime import (
        SolveWorkerResourceSpec,
        load_image_allowlist,
    )
    from polycodebench_plugins_api import load_allowlist

    images = ROOT / "config" / "images"
    amd64 = load_grading_image_allowlist(images, architecture="amd64")
    arm64 = load_grading_image_allowlist(images, architecture="arm64")
    assert arm64 and not set(arm64.values()) & set(amd64.values())
    for stem, loader in (
        ("python-v1-arm64", python_identities.load_identities),
        ("rust-v1-arm64", rust_identities.load_identities),
    ):
        identities = loader(str(images / f"{stem}.json"))
        assert identities.build["platform"] == "linux/arm64"
    plugins = load_allowlist(ROOT / "config/plugins/allowlist-v1-arm64.yaml")
    allowed = {digest for plugin in plugins.plugins for digest in plugin.image_digests}
    assert allowed == set(arm64.values())
    worker = ROOT / "config" / "worker"
    resource = SolveWorkerResourceSpec.model_validate_json(
        (worker / "local-small-resource-arm64.json").read_text(encoding="utf-8"), strict=True
    )
    pinned = load_image_allowlist(worker / "local-image-allowlist-arm64.json")
    assert pinned == {resource.image: resource.image_digest}
    amd64_resource = json.loads((worker / "local-small-resource.json").read_text(encoding="utf-8"))
    assert resource.image_digest != amd64_resource["image_digest"]
    assert {k: v for k, v in resource.model_dump().items() if not k.startswith("image")} == {
        k: v for k, v in amd64_resource.items() if not k.startswith("image")
    }
