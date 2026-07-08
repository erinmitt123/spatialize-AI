"""
Enable or disable rubric rules that the analyzer should consider.

Typical usage:

    python hci_for_glasses/manage_rubric_rules.py --disable-rule touch_target_size
    python hci_for_glasses/manage_rubric_rules.py --enable-rule touch_target_size
    python hci_for_glasses/manage_rubric_rules.py --list-disabled
"""

import argparse
import json

from analyze_sessions import find_rubric_path
from analyze_sessions import get_disabled_rule_ids
from analyze_sessions import load_rubric_document


def parse_args():
    parser = argparse.ArgumentParser(
        description="Enable or disable analyzer rules inside the XR HCI rubric JSON."
    )
    parser.add_argument(
        "--rubric-path",
        default=None,
        help="Optional path to the rubric JSON. Defaults to the rubric beside analyze_sessions.py.",
    )
    parser.add_argument(
        "--disable-rule",
        default=None,
        help="Rule ID to disable for future analyses.",
    )
    parser.add_argument(
        "--enable-rule",
        default=None,
        help="Rule ID to re-enable for future analyses.",
    )
    parser.add_argument(
        "--list-disabled",
        action="store_true",
        help="Print the disabled rule IDs and exit.",
    )
    return parser.parse_args()


def find_rule(rubric, rule_id):
    for rule in rubric.get("rules", []):
        if rule.get("id") == rule_id:
            return rule
    return None


def write_rubric(path, rubric):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rubric, f, indent=2)
        f.write("\n")


def list_disabled_rules(path, rubric):
    disabled = get_disabled_rule_ids(rubric)
    print("Rubric: {}".format(path))
    if not disabled:
        print("No rules are currently disabled.")
        return 0
    print("Disabled rules:")
    for rule_id in disabled:
        print("  - {}".format(rule_id))
    return 0


def set_rule_disabled(path, rubric, rule_id, disabled):
    rule = find_rule(rubric, rule_id)
    if rule is None:
        print("Rule ID not found in rubric: {}".format(rule_id))
        return 1

    if disabled:
        if rule.get("disabled"):
            print("Rule already disabled: {}".format(rule_id))
            return 0
        rule["disabled"] = True
        write_rubric(path, rubric)
        print("Disabled rule '{}' in {}".format(rule_id, path))
        return 0

    if not rule.get("disabled"):
        print("Rule already enabled: {}".format(rule_id))
        return 0
    rule.pop("disabled", None)
    write_rubric(path, rubric)
    print("Enabled rule '{}' in {}".format(rule_id, path))
    return 0


def main():
    args = parse_args()
    action_count = sum(
        1 for value in (args.disable_rule, args.enable_rule) if value is not None
    ) + (1 if args.list_disabled else 0)
    if action_count != 1:
        print("Choose exactly one action: --disable-rule, --enable-rule, or --list-disabled.")
        return 1

    try:
        rubric_path = args.rubric_path or find_rubric_path()
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    rubric = load_rubric_document(rubric_path)

    if args.list_disabled:
        return list_disabled_rules(rubric_path, rubric)
    if args.disable_rule:
        return set_rule_disabled(rubric_path, rubric, args.disable_rule, True)
    return set_rule_disabled(rubric_path, rubric, args.enable_rule, False)


if __name__ == "__main__":
    raise SystemExit(main())
