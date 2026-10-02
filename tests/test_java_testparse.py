"""Java test evidence must come from the declared Surefire report artifacts."""

from __future__ import annotations

from polycodebench_lang_java.testparse import surefire_reports
from polycodebench_plugins_api import DictArtifactReader


def test_surefire_parser_reads_exact_declared_xml_paths_and_ignores_unlisted_reports() -> None:
    chosen = "work/target/surefire-reports/TEST-demo.TopWordsTest.xml"
    unrelated = "work/target/surefire-reports/TEST-demo.SecretTest.xml"
    xml = (
        b'<testsuite name="demo.TopWordsTest"><testcase classname="demo.TopWordsTest" '
        b'name="ordersByCountAndToken" time="0.012" /></testsuite>'
    )
    reader = DictArtifactReader({chosen: xml, unrelated: xml})

    assert surefire_reports(reader, (chosen,)) == [
        {
            "classname": "demo.TopWordsTest",
            "name": "ordersByCountAndToken",
            "outcome": "pass",
            "reason": "",
            "time": "0.012",
        }
    ]
