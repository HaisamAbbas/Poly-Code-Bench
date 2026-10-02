"""Small Java task/image identities for local contract tests; these are never release identities."""

from __future__ import annotations

from pathlib import Path

import yaml
from polycodebench_plugins_api import TaskDraft

ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = ROOT / "plugins" / "languages" / "java" / "fixtures" / "top-words"
DIGESTS = {
    recipe: "sha256:" + str(index) * 64
    for index, recipe in enumerate(("runtime", "evaluator", "performance"), start=1)
}


def draft() -> TaskDraft:
    document = yaml.safe_load((TASK_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    files = {
        path.relative_to(TASK_ROOT).as_posix(): path.read_bytes()
        for path in TASK_ROOT.rglob("*")
        if path.is_file() and path.name != "manifest.yaml"
    }
    return TaskDraft(
        task_id=document["task_id"],
        primary_language=document["primary_language"],
        manifest=document,
        files=files,
    )


def identities():  # type: ignore[no-untyped-def]
    from polycodebench_lang_java.identities import ImageIdentities, ImageRecord, JavaBuild

    flags = {
        "cold": ("-Xint", "-XX:-UsePerfData"),
        "steady_state": (
            "-XX:+UseParallelGC",
            "-XX:-UsePerfData",
            "-XX:+AlwaysPreTouch",
            "-XX:+UseCompressedOops",
            "-XX:ActiveProcessorCount=1",
        ),
    }
    images = {}
    for recipe, digest in DIGESTS.items():
        images[recipe] = ImageRecord(
            recipe=recipe,
            tag=f"pcb-java-{recipe}:test",
            reference=f"pcb-java-{recipe}@{digest}",
            digest=digest,
            java="21-test-only",
            maven="3.9.9-test-only",
            tools={
                "java": "21-test-only",
                "javac": "21-test-only",
                "maven": "3.9.9-test-only",
                "junit": "5.10.2-test-only",
                "compiler": "3.13.0-test-only",
                "surefire": "3.2.5-test-only",
                "dependency": "3.6.1-test-only",
                "spotbugs": "4.8.6.0-test-only" if recipe == "evaluator" else "absent",
                "pmd": "3.21.2-test-only" if recipe == "evaluator" else "absent",
                "checkstyle": "3.3.1-test-only" if recipe == "evaluator" else "absent",
            },
            expected_tools=("java", "javac", "maven", "junit"),
            components=("TEST_ONLY",),
            guest_and_rules_digest="sha256:" + "a" * 64,
            recipe_digest="sha256:" + "b" * 64,
            dockerfile_digest="sha256:" + "c" * 64,
        )
    return ImageIdentities(
        schema_version=1,
        kind="java_images",
        base_image={"reference": "maven:fixture-only", "digest": "sha256:" + "d" * 64},
        build=JavaBuild(
            network="none",
            offline_install=True,
            scored_runs_offline=True,
            java="21-test-only",
            maven="3.9.9-test-only",
            offline_repository="test-only",
            offline_repository_digest="sha256:" + "e" * 64,
            guest_interpreter="python-test-only",
            jvm_measurement=flags,
            note="Test-only synthetic image identity; never use for execution.",
        ),
        rule_bundle_digest="sha256:" + "f" * 64,
        guest_digest="sha256:" + "0" * 64,
        images=images,
    )
