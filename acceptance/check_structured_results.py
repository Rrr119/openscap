#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Downstream style acceptance checks for oscap-structured.

This script deliberately acts as the "downstream parser": it invokes the
``utils/oscap-structured`` CLI, parses every produced artifact as JSON/JSONL
and asserts the 等保 acceptance criteria:

1. structured per-rule records (rule_id/result/evidence/remediation_commands)
   are parseable for all three shipped profiles;
2. multiple profile results can be merged into one parseable document;
3. two scans of the same host produce a diff with the expected changed rules.

It only needs the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_TOOL = os.path.join(REPO_ROOT, "utils", "oscap-structured")

PROFILES = ("basic", "level2", "level3")
EXPECTED_SELECTED_RULES = {"basic": 3, "level2": 5, "level3": 6}

# Rule short suffix -> expected result in the BEFORE fixtures/state.
BEFORE_EXPECTED = {
    "pass_max_days": "fail",
    "root_login": "fail",
    "pass_min_len": "pass",
    "banner": "pass",
    "pass_warn_age": "pass",
    "ip_forward": "pass",
}

# Changes expected between the BEFORE and AFTER host states.
EXPECTED_CHANGES = {
    "banner": ("pass", "fail"),
    "pass_max_days": ("fail", "pass"),
    "root_login": ("fail", "pass"),
}

REQUIRED_RULE_KEYS = ("rule_id", "result", "evidence", "remediation_commands")


class AcceptanceError(AssertionError):
    pass


def rule_suffix(rule_id):
    # xccdf_com.example.mlps_rule_pass_max_days -> pass_max_days
    marker = "mlps_"
    if marker in rule_id:
        suffix = rule_id.split(marker, 1)[1]
        if suffix.startswith("rule_"):
            suffix = suffix[len("rule_"):]
        return suffix
    return rule_id


class Runner:
    def __init__(self, tool, work):
        self.tool = tool
        self.work = work

    def run(self, *args):
        proc = subprocess.run(
            [sys.executable, self.tool, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise AcceptanceError(
                "command failed (%d): %s\n%s" % (
                    proc.returncode, " ".join(args), proc.stderr.strip()))
        return proc.stdout

    def run_json(self, *args):
        out = self.run(*args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise AcceptanceError("output is not valid JSON: %s\n%s" % (exc, out[:400]))

    def to_file(self, name):
        return os.path.join(self.work, name)


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def check_rule_records(doc, profile_name):
    require(isinstance(doc, dict), "%s: document is not an object" % profile_name)
    rules = doc.get("rules")
    require(isinstance(rules, list) and rules,
            "%s: missing non-empty 'rules' array" % profile_name)

    selected = 0
    for rule in rules:
        for key in REQUIRED_RULE_KEYS:
            require(key in rule, "%s: rule %r missing key %r"
                    % (profile_name, rule.get("rule_id"), key))
        require(isinstance(rule["rule_id"], str) and rule["rule_id"],
                "%s: rule_id must be a non-empty string" % profile_name)
        require(isinstance(rule["result"], str) and rule["result"],
                "%s: result must be a non-empty string" % profile_name)
        require(isinstance(rule["evidence"], list),
                "%s: evidence must be a list" % rule["rule_id"])
        require(isinstance(rule["remediation_commands"], list),
                "%s: remediation_commands must be a list" % rule["rule_id"])
        if rule["result"] != "notselected":
            selected += 1
            # Every evaluated demo rule ships an OVAL evidence line and a fix.
            require(rule["evidence"], "%s: expected evidence for %s"
                    % (profile_name, rule["rule_id"]))
            require(rule["remediation_commands"],
                    "%s: expected remediation command for %s"
                    % (profile_name, rule["rule_id"]))

    expected_selected = EXPECTED_SELECTED_RULES[profile_name]
    require(selected == expected_selected,
            "%s: expected %d evaluated rules, got %d"
            % (profile_name, expected_selected, selected))

    by_suffix = {rule_suffix(r["rule_id"]): r for r in rules}
    for suffix, result in BEFORE_EXPECTED.items():
        if suffix in by_suffix and by_suffix[suffix]["result"] != "notselected":
            actual = by_suffix[suffix]["result"]
            require(actual == result,
                    "%s: %s expected %s got %s"
                    % (profile_name, suffix, result, actual))
    return rules


def check_profiles(runner, fixtures):
    docs = {}
    for profile in PROFILES:
        arf = os.path.join(fixtures, "profile-%s.before.arf.xml" % profile)
        require(os.path.exists(arf), "missing fixture: %s" % arf)
        out_json = runner.to_file("%s.json" % profile)
        runner.run("export", arf, "-o", out_json)
        with open(out_json, encoding="utf-8") as handle:
            doc = json.load(handle)
        check_rule_records(doc, profile)
        docs[profile] = out_json
        print("  [ok] export/parse profile=%-7s rules=%d"
              % (profile, EXPECTED_SELECTED_RULES[profile]))

        # JSONL must also be line parseable.
        jsonl = runner.run("export", arf, "--format", "jsonl")
        records = [json.loads(line) for line in jsonl.splitlines() if line.strip()]
        require(len(records) == len(doc["rules"]),
                "%s: jsonl record count mismatch" % profile)

        # Bare JSON array variant must parse as a list.
        listed = runner.run_json("export", arf, "--format", "list")
        require(isinstance(listed, list) and len(listed) == len(doc["rules"]),
                "%s: list format mismatch" % profile)

    # Demonstrate a trivial downstream sink: CSV of rule id/result/fix count.
    csv_path = runner.to_file("downstream-rules.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["profile", "rule_id", "result", "evidence_count",
                        "remediation_count"])
        for profile, json_path in docs.items():
            with open(json_path, encoding="utf-8") as jh:
                doc = json.load(jh)
            for rule in doc["rules"]:
                writer.writerow([profile, rule["rule_id"], rule["result"],
                                 len(rule["evidence"]),
                                 len(rule["remediation_commands"])])
    require(os.path.getsize(csv_path) > 100, "downstream CSV was not written")
    print("  [ok] downstream CSV sink: %s" % os.path.basename(csv_path))
    return docs


def check_merge(runner, docs, fixtures):
    merged_path = runner.to_file("merged.json")
    runner.run("merge", docs["basic"], docs["level2"], docs["level3"],
               "-o", merged_path)
    with open(merged_path, encoding="utf-8") as handle:
        merged = json.load(handle)
    require(merged.get("kind") == "merged_results", "merge: wrong kind")
    require(len(merged["profiles"]) == 3, "merge: expected three profiles")
    require(len(merged["scans"]) == 3, "merge: expected three scans")
    # Lossless rule stream: 3 + 5 + 6 selected plus notselected occurrences.
    require(len(merged["rules"]) == 18,
            "merge: expected 18 rule occurrences, got %d" % len(merged["rules"]))
    require(len(merged["unique_rules"]) == 6,
            "merge: expected 6 unique rules, got %d"
            % len(merged["unique_rules"]))
    print("  [ok] merge JSON: scans=3 occurrences=18 unique=6")

    # Merging raw XML/ARF inputs directly must work as well.
    direct = runner.run_json(
        "merge",
        os.path.join(fixtures, "profile-basic.before.arf.xml"),
        os.path.join(fixtures, "profile-level2.before.arf.xml"))
    require(len(direct["scans"]) == 2, "direct xml merge failed")
    print("  [ok] merge directly from ARF inputs")

    # JSONL of merged rules is fully parseable.
    jsonl = runner.run("merge", docs["level3"], "--format", "jsonl")
    rows = [json.loads(line) for line in jsonl.splitlines() if line.strip()]
    require(len(rows) == 6, "merge jsonl expected 6 rows")
    print("  [ok] merge JSONL parseable")


def check_diff(runner, fixtures):
    old = os.path.join(fixtures, "profile-level3.before.arf.xml")
    new = os.path.join(fixtures, "profile-level3.after.arf.xml")
    diff_path = runner.to_file("diff.json")
    runner.run("diff", old, new, "-o", diff_path)
    with open(diff_path, encoding="utf-8") as handle:
        diff = json.load(handle)

    require(diff["has_changes"] is True, "diff: expected changes")
    require(diff["same_host"] is True, "diff: fixtures must represent one host")
    changes = {rule_suffix(c["rule_id"]): c for c in diff["changes"]
               if c["change_type"] == "result_changed"}
    require(set(changes) == set(EXPECTED_CHANGES),
            "diff: unexpected changed set: %s" % sorted(changes))
    for suffix, (old_result, new_result) in EXPECTED_CHANGES.items():
        change = changes[suffix]
        require(change["old_result"] == old_result,
                "diff: %s old result %s != %s"
                % (suffix, change["old_result"], old_result))
        require(change["new_result"] == new_result,
                "diff: %s new result %s != %s"
                % (suffix, change["new_result"], new_result))
        require(change["new_evidence"], "diff: new evidence missing for %s" % suffix)
    require(diff["summary"]["unchanged"] == 3,
            "diff: expected 3 unchanged rules, got %s"
            % diff["summary"].get("unchanged"))
    print("  [ok] diff JSON: %d result changes, 3 unchanged"
          % len(EXPECTED_CHANGES))

    # Human renderers must succeed and contain the changed ids.
    text = runner.run("diff", old, new, "--format", "text")
    require("result_changed" in text and "Changes: 3" in text,
            "text diff rendering missing change summary")
    markdown = runner.run("diff", old, new, "--format", "markdown")
    require("| Rule | Change | Result |" in markdown,
            "markdown diff table missing")
    print("  [ok] diff text/markdown renderers")

    # Comparing a scan to itself reports no changes.
    same = runner.run_json("diff", old, old)
    require(same["has_changes"] is False and same["changes"] == [],
            "self diff must be empty")
    print("  [ok] self comparison reports no changes")


def check_plain_xccdf_results(runner, fixtures):
    results_xml = os.path.join(fixtures, "profile-level3.before.results.xml")
    if not os.path.exists(results_xml):
        print("  [skip] plain XCCDF results fixture absent")
        return
    doc = runner.run_json("export", results_xml)
    require(len(doc["rules"]) == 6, "plain results.xml expected 6 rule results")
    require(all(r["remediation_commands"] for r in doc["rules"]),
            "fixes must be extracted from embedded Benchmark")
    print("  [ok] plain XCCDF --results XML extracts fixes")


def check_existing_content_compatibility(runner):
    """Export every TestResult artifact shipped in the upstream test suite.

    This is the backward compatibility net: the tool must never crash on
    existing XCCDF 1.1/1.2, ARF or datastream result documents.
    """
    tests_dir = Path(REPO_ROOT) / "tests"
    if not tests_dir.is_dir():
        print("  [skip] upstream tests/ directory unavailable")
        return
    checked = 0
    rule_count = 0
    for xml_file in tests_dir.rglob("*.xml"):
        try:
            text = xml_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "TestResult" not in text:
            continue
        doc = runner.run_json("export", str(xml_file), "--compact")
        require(isinstance(doc.get("rules"), list),
                "compat: no rules array exported from %s" % xml_file)
        for rule in doc["rules"]:
            require(rule.get("rule_id") and rule.get("result"),
                    "compat: rule without id/result in %s" % xml_file)
        checked += 1
        rule_count += len(doc["rules"])
    require(checked >= 20,
            "compat: expected to sweep >=20 existing artifacts, got %d" % checked)
    print("  [ok] backward compatibility sweep: %d existing artifacts, %d rules"
          % (checked, rule_count))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", default=DEFAULT_TOOL)
    parser.add_argument("--fixtures", default=os.path.join(HERE, "fixtures"))
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args()

    if not os.path.exists(args.tool):
        print("tool not found: %s" % args.tool, file=sys.stderr)
        return 2
    work = args.work_dir or tempfile.mkdtemp(prefix="oscap-sr-check-")
    os.makedirs(work, exist_ok=True)
    runner = Runner(os.path.abspath(args.tool), work)

    print("== structured per-rule output for 3 profiles ==")
    docs = check_profiles(runner, args.fixtures)
    print("== result merging ==")
    check_merge(runner, docs, args.fixtures)
    print("== same-host scan comparison ==")
    check_diff(runner, args.fixtures)
    print("== plain XCCDF results ==")
    check_plain_xccdf_results(runner, args.fixtures)
    print("== compatibility with existing content/profiles ==")
    check_existing_content_compatibility(runner)

    print("\nACCEPTANCE PASSED. Downstream outputs in: %s" % work)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AcceptanceError as exc:
        print("\nACCEPTANCE FAILED: %s" % exc, file=sys.stderr)
        sys.exit(1)
