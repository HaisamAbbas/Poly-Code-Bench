"""Java guest helper tests over internal synthetic code, never benchmark outcome data."""

from __future__ import annotations

import sys
from pathlib import Path

from polycodebench_lang_java.guestmods import load_guest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "languages" / "java" / "src"
sys.path.insert(0, str(PLUGIN))
scan = load_guest("pcb_java_scan")
dependency_audit = load_guest("pcb_dependency_audit")
run_guest = load_guest("pcb_java_run")


def test_context_scan_separates_resource_null_concurrency_and_security_defects() -> None:
    source = b'''package demo;
import java.io.FileInputStream;
import java.util.Optional;
import java.util.Map;
import java.util.HashMap;
class Bad {
  static Map<String, Integer> shared = new HashMap<>();
  int read(String path) throws Exception {
    FileInputStream input = new FileInputStream(path);
    return input.read();
  }
  String maybe(String value) { return Optional.ofNullable(value).get(); }
  Process launch(String value) throws Exception {
    return Runtime.getRuntime().exec("lookup " + value);
  }
}
'''
    findings, parsed = scan.scan_text("src/main/java/demo/Bad.java", source)
    assert parsed is True
    rules = {item["rule"] for item in findings if item["verdict"] == "violation"}
    assert {
        "resource-unclosed",
        "implicit-optional-get",
        "published-mutable-state",
        "command-injection",
    } <= rules


def test_context_scan_treats_correct_resource_and_optional_use_as_benign() -> None:
    source = b'''package demo;
import java.io.FileInputStream;
import java.util.Optional;
class Good {
  int read(String path) throws Exception {
    try (var input = new FileInputStream(path)) { return input.read(); }
  }
  String maybe(String value) { return Optional.ofNullable(value).orElse(""); }
}
'''
    findings, parsed = scan.scan_text("src/main/java/demo/Good.java", source)
    assert parsed is True
    assert not [item for item in findings if item["verdict"] == "violation"]


def test_dependency_comparison_reports_findings_and_drift_separately() -> None:
    resolved = [
        {
            "coordinate": "org.example:unsafe:1.2.0",
            "group_id": "org.example",
            "artifact_id": "unsafe",
            "version": "1.2.0",
            "scope": "runtime",
        }
    ]
    findings, drift = dependency_audit.audit(
        resolved,
        ["org.example:unsafe:1.1.0"],
        [{"id": "PCB-JAVA-TEST-1", "coordinate_prefix": "org.example:unsafe:", "severity": "high"}],
    )
    assert [item["advisory"] for item in findings] == ["PCB-JAVA-TEST-1"]
    assert drift == ["org.example:unsafe:1.1.0", "org.example:unsafe:1.2.0"]


def test_guest_wrapper_selects_the_pinned_temurin_home() -> None:
    environment = run_guest._pinned_env()
    assert environment["JAVA_HOME"] == "/opt/java/openjdk"
    assert environment["MAVEN_CONFIG"] == "/opt/pcb/maven"
    assert environment["MAVEN_OPTS"].endswith("/opt/pcb/m2/repository")
