"""The plugin's metadata rules, exercised on throw-away test files with pytester."""
import json

pytest_plugins = ["pytester"]

HEADER = "import pytest\n"


def run(pytester, body, *args):
    pytester.makepyfile(**{"tests/api/test_sample": HEADER + body})
    return pytester.runpytest("-p", "apitest.plugin", "--reports-dir", "out", *args)


def test_missing_case_marker_is_a_usage_error(pytester):
    r = run(pytester, "@pytest.mark.positive\ndef test_a():\n    pass\n")
    assert r.ret == 4
    r.stderr.fnmatch_lines(["*missing @pytest.mark.case*"])


def test_duplicate_case_ids_are_rejected(pytester):
    body = ("@pytest.mark.case('API-TC-001')\n@pytest.mark.positive\ndef test_a():\n    pass\n\n"
            "@pytest.mark.case('API-TC-001')\n@pytest.mark.negative\ndef test_b():\n    pass\n")
    r = run(pytester, body)
    assert r.ret == 4
    r.stderr.fnmatch_lines(["*API-TC-001 is used by both*"])


def test_tier_marker_required_and_case_format(pytester):
    r = run(pytester, "@pytest.mark.case('TC1')\ndef test_a():\n    pass\n")
    assert r.ret == 4
    r.stderr.fnmatch_lines(["*must look like API-TC-001*", "*no tier marker*"])


def test_known_defect_becomes_strict_xfail_and_reaches_the_report(pytester):
    body = ("@pytest.mark.case('API-TC-001')\n@pytest.mark.edge\n"
            "@pytest.mark.known_defect('DEMO-9', 'returns 500')\ndef test_a():\n    assert False\n\n"
            "@pytest.mark.case('API-TC-002')\n@pytest.mark.positive\n"
            "@pytest.mark.parametrize('x', [1, 2])\ndef test_b(x):\n    '''two iterations'''\n    assert x\n")
    r = run(pytester, body)
    r.assert_outcomes(passed=2, xfailed=1)
    results = json.loads((pytester.path / "out" / "results.json").read_text())["results"]
    by_case = {}
    for i in results:
        by_case.setdefault(i["case"], []).append(i)
    assert by_case["API-TC-001"][0]["outcome"] == "xfailed" and by_case["API-TC-001"][0]["defects"] == ["DEMO-9"]
    assert [i["param"] for i in by_case["API-TC-002"]] == ["1", "2"]
    assert by_case["API-TC-002"][0]["title"] == "two iterations"
    xray = json.loads((pytester.path / "out" / "xray-results.json").read_text())
    assert xray["tests"] == []          # no mapping file in this sandbox → nothing invented


def test_fixed_defect_fails_the_run(pytester):
    body = ("@pytest.mark.case('API-TC-001')\n@pytest.mark.edge\n"
            "@pytest.mark.known_defect('DEMO-9', 'returns 500')\ndef test_a():\n    assert True\n")
    r = run(pytester, body)
    r.assert_outcomes(failed=1)          # XPASS(strict): remove the marker and close the defect


def test_xray_strict_fails_on_unmapped_cases(pytester):
    r = run(pytester, "@pytest.mark.case('API-TC-001')\n@pytest.mark.positive\ndef test_a():\n    pass\n",
            "--xray-strict")
    assert r.ret == 1
    r.stdout.fnmatch_lines(["*unmapped cases: API-TC-001*"])
