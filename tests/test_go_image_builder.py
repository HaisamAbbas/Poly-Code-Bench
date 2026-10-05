from __future__ import annotations

from subprocess import CompletedProcess

from scripts import build_go_images, fetch_go_components


def test_builder_reads_the_bundled_go_vet_version(monkeypatch) -> None:
    def fake_run(args: list[str], *, check: bool = True) -> CompletedProcess[str]:
        assert args[-4:] == ["go", "tool", "vet", "-V=full"]
        return CompletedProcess(args, 0, stdout="vet version go1.26.8\n", stderr="")

    monkeypatch.setattr(build_go_images, "run", fake_run)
    assert build_go_images.tool_version("evaluator", "sha256:image", "go-vet") == "1.26.8"


def test_builder_reads_gosec_version_from_embedded_module_build_info(monkeypatch) -> None:
    def fake_run(args: list[str], *, check: bool = True) -> CompletedProcess[str]:
        assert args[-4:] == ["go", "version", "-m", "/opt/pcb/bin/gosec"]
        output = (
            "/opt/pcb/bin/gosec: go1.26.8\n"
            "\tpath\tgithub.com/securego/gosec/v2/cmd/gosec\n"
            "\tmod\tgithub.com/securego/gosec/v2\tv2.29.0\th1:sum\n"
        )
        return CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(build_go_images, "run", fake_run)
    assert build_go_images.tool_version("evaluator", "sha256:image", "gosec") == "v2.29.0"


def test_components_builder_reads_gosec_module_version(monkeypatch) -> None:
    def fake_run(args: list[str], *, check: bool = True) -> CompletedProcess[str]:
        assert args[-4:] == ["go", "version", "-m", "/opt/pcb/tools/gosec"]
        output = "\tmod\tgithub.com/securego/gosec/v2\tv2.29.0\th1:sum\n"
        return CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(fetch_go_components, "run", fake_run)
    assert (
        fetch_go_components.tool_version("components", "sha256:image", ["gosec", "--version"])
        == "v2.29.0"
    )


def test_components_builder_probes_staticcheck_at_its_install_path(monkeypatch) -> None:
    def fake_run(args: list[str], *, check: bool = True) -> CompletedProcess[str]:
        assert args[-2:] == ["/opt/pcb/tools/staticcheck", "-version"]
        return CompletedProcess(args, 0, stdout="staticcheck 2026.2.1 (0.8.1)\n", stderr="")

    monkeypatch.setattr(fetch_go_components, "run", fake_run)
    assert (
        fetch_go_components.tool_version("components", "sha256:image", ["staticcheck", "-version"])
        == "2026.2.1"
    )
