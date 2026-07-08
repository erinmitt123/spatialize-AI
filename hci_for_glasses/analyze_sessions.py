"""
Analyze VR/XR interaction telemetry against the Android XR Spatial HCI rubric.

Reads:  xr_hci_rubric.json  and  data/session_*.json
Writes: data/analysis_report.json  (findings per session, in the rubric's
        output_format_for_agent shape)
Prints: a human-readable report, and if data/summary.json (ground truth)
        exists, a precision/recall check so you can confirm the tool finds
        what was actually injected.

Findings are derived only from telemetry signals the rubric names, using the
click/grab interaction data and the 6DoF hand/target positions in each session.

Standard library only.
"""

import glob
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUBRIC_PATH = os.path.join(BASE_DIR, "xr_hci_rubric.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORT_PATH = os.path.join(DATA_DIR, "analysis_report.json")
SUMMARY_PATH = os.path.join(DATA_DIR, "summary.json")

# --------------------------------------------------------------------------- #
# Detection thresholds (tune these; they map directly to the rubric signals)   #
# --------------------------------------------------------------------------- #

MIN_ATTEMPTS = 2            # need repeated attempts on a target to judge it
FAIL_RATE_THRESHOLD = 0.50  # >= this share of failed grabs => "hard target"
LARGE_FAIL_DISTANCE = 0.20  # m; failed grabs beyond this => misjudged position
DISTANCE_VARIANCE_MIN = 0.02  # m stdev across attempts => weak pre-contact cue
HESITATION_GAP = 1.50       # s of pose-only quiet before first grab => hesitation

# Map a ui_interaction issue_flag string to the rubric rule it evidences.
ISSUE_FLAG_TO_RULE = {
    "content_scroll_in_elevated_panel": "content_type_in_elevated_context",
}


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #

def load_rubric(path):
    with open(path, encoding="utf-8") as f:
        rubric = json.load(f)
    return {r["id"]: r for r in rubric["rules"]}


def find_rubric_path():
    """
    Prefer the canonical rubric filename, but tolerate Windows download suffixes
    like "xr_hci_rubric (1).json" when that is the only copy on disk.
    """
    if os.path.exists(RUBRIC_PATH):
        return RUBRIC_PATH

    matches = sorted(glob.glob(os.path.join(BASE_DIR, "xr_hci_rubric*.json")))
    if matches:
        return matches[0]

    raise FileNotFoundError(
        "No rubric JSON found beside analyze_sessions.py. "
        "Expected xr_hci_rubric.json or a matching copy like xr_hci_rubric (1).json."
    )


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def make_finding(rules, rule_id, target, evidence, fix, related=None):
    """Build one finding in the rubric's output_format_for_agent shape."""
    rule = rules[rule_id]
    finding = {
        "rule_id": rule_id,
        "rule_text": rule["rule"],
        "target": target,
        "evidence_from_telemetry": evidence,
        "suggested_fix": fix,
        "severity": rule["severity_if_violated"],
    }
    if related:
        finding["related_rule_ids"] = related
    return finding


# --------------------------------------------------------------------------- #
# Detectors                                                                    #
# --------------------------------------------------------------------------- #

def detect_grab_targets(session, rules):
    """
    touch_target_size (primary): repeated failed grabs / large distance_to_target
    on a specific target. Folds in hesitation (icon_button_contrast) and
    distance variance (hover_state_availability) as supporting evidence.
    """
    findings = []
    events = session["events"]

    # Group grab attempts by target, preserving order.
    by_target = {}
    for e in events:
        if e["type"] == "grab_attempt":
            by_target.setdefault(e["target"], []).append(e)

    action_types = {"grab_attempt", "controller_input", "ui_interaction"}

    for target, attempts in by_target.items():
        n = len(attempts)
        fails = [a for a in attempts if not a["success"]]
        succ = [a for a in attempts if a["success"]]
        fail_rate = len(fails) / n
        fail_dists = [a["distance_to_target"] for a in fails]
        all_dists = [a["distance_to_target"] for a in attempts]
        max_fail_dist = max(fail_dists) if fail_dists else 0.0

        hard = n >= MIN_ATTEMPTS and fails and (
            fail_rate >= FAIL_RATE_THRESHOLD or max_fail_dist >= LARGE_FAIL_DISTANCE
        )
        if not hard:
            continue

        # Hesitation before the first attempt on this target: quiet span in which
        # only hand_pose events occur (user hovering/hunting, not acting).
        first_t = attempts[0]["t"]
        prior_action_t = 0.0
        for e in events:
            if e["t"] >= first_t:
                break
            if e["type"] in action_types:
                prior_action_t = e["t"]
        hesitation = first_t - prior_action_t

        dist_sd = stdev(all_dists)

        parts = [
            "{} grab attempts on '{}', {} failed (fail rate {:.0%})".format(
                n, target, len(fails), fail_rate),
        ]
        if fail_dists:
            parts.append(
                "failed distance_to_target {:.2f}-{:.2f}m (mean {:.2f}m)".format(
                    min(fail_dists), max(fail_dists), mean(fail_dists)))
        if succ:
            parts.append("eventual success at {:.2f}m".format(
                min(a["distance_to_target"] for a in succ)))

        related = []
        if hesitation >= HESITATION_GAP:
            parts.append(
                "{:.1f}s of hand-pose-only hesitation before the first attempt".format(
                    hesitation))
            related.append("icon_button_contrast")
        if dist_sd >= DISTANCE_VARIANCE_MIN:
            parts.append("distance_to_target stdev {:.2f}m across attempts".format(dist_sd))
            related.append("hover_state_availability")
        related.append("icon_button_visual_clarity")

        evidence = "; ".join(parts) + "."
        fix = (
            "Enlarge the interactive/grab collider of '{t}' to >=56dp (>=48dp min) and "
            "confirm the collider matches the visual bounds so users aren't reaching {d:.2f}m "
            "off-target; add a hover/focus highlight and press confirmation so the target is "
            "acquirable on the first try (compare against clean targets that succeed <0.10m)."
        ).format(t=target, d=max_fail_dist)

        findings.append(make_finding(
            rules, "touch_target_size", target, evidence, fix, related=related))

    return findings


def detect_ui_issue_flags(session, rules):
    """content_type_in_elevated_context (and any other flagged ui_interaction)."""
    findings = []
    for e in session["events"]:
        if e["type"] != "ui_interaction":
            continue
        flag = e.get("issue_flag")
        if not flag:
            continue
        component = e.get("component", "unknown")
        rule_id = ISSUE_FLAG_TO_RULE.get(flag)
        if rule_id is None:
            # Unknown flag: still surface it rather than swallow it.
            findings.append({
                "rule_id": "unmapped_issue_flag",
                "rule_text": "Telemetry raised an issue_flag with no rubric mapping.",
                "target": component,
                "evidence_from_telemetry":
                    "ui_interaction on '{}' at t={:.2f}s raised issue_flag='{}'.".format(
                        component, e["t"], flag),
                "suggested_fix": "Add a rubric rule / mapping for this flag, then re-run.",
                "severity": "medium",
            })
            continue
        evidence = (
            "ui_interaction on '{}' at t={:.2f}s raised issue_flag='{}'.".format(
                component, e["t"], flag))
        fix = (
            "Move scrollable primary content out of the elevated/orbiter '{}' into a "
            "standard anchored panel; keep only controls in elevated elements per platform "
            "convention.".format(component))
        findings.append(make_finding(
            rules, rule_id, component, evidence, fix))
    return findings


DETECTORS = [detect_grab_targets, detect_ui_issue_flags]


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #

def analyze_session(session, rules):
    findings = []
    for detector in DETECTORS:
        findings.extend(detector(session, rules))
    # Sort most-severe first.
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return findings


def main():
    try:
        rubric_path = find_rubric_path()
    except FileNotFoundError as exc:
        print(str(exc))
        return

    rules = load_rubric(rubric_path)
    session_paths = sorted(glob.glob(os.path.join(DATA_DIR, "session_*.json")))
    if not session_paths:
        print("No session files found in {} - run generate_sessions.py first.".format(DATA_DIR))
        return

    report = {"rubric": os.path.basename(rubric_path), "sessions": []}
    print("=" * 72)
    print("Android XR Spatial HCI review  -  {} sessions".format(len(session_paths)))
    print("=" * 72)

    for path in session_paths:
        with open(path, encoding="utf-8") as f:
            session = json.load(f)
        findings = analyze_session(session, rules)
        report["sessions"].append({
            "file": os.path.basename(path),
            "session_id": session.get("session_id"),
            "app": session.get("app"),
            "scene": session.get("scene"),
            "findings": findings,
        })

        print("\n{}  [{} / {}]".format(
            os.path.basename(path), session.get("app"), session.get("scene")))
        if not findings:
            print("  [OK] no rubric violations detected (clean)")
        for fnd in findings:
            print("  [{}] {}".format(fnd["severity"].upper(), fnd["rule_id"]))
            print("      evidence: {}".format(fnd["evidence_from_telemetry"]))
            print("      fix:      {}".format(fnd["suggested_fix"]))
            if fnd.get("related_rule_ids"):
                print("      related:  {}".format(", ".join(fnd["related_rule_ids"])))

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nwrote {}".format(REPORT_PATH))

    _ground_truth_check(report)


def _ground_truth_check(report):
    """If summary.json exists, cross-check detected rules vs injected issues."""
    if not os.path.exists(SUMMARY_PATH):
        return
    with open(SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)

    # Prefer structured rule ids from summary.json; fall back to legacy prose parsing.
    def expected_rules(summary_session):
        expected = summary_session.get("expected_rule_ids")
        if expected is not None:
            return set(expected)

        desc = summary_session.get("injected_issue", "")
        d = desc.lower()
        exp = set()
        if "hard to grab" in d or "fail" in d:
            exp.add("touch_target_size")
        if "content_scroll_in_elevated_panel" in d or "ui issue" in d:
            exp.add("content_type_in_elevated_context")
        return exp

    by_file = {s["file"]: s for s in report["sessions"]}
    print("\n" + "=" * 72)
    print("Ground-truth check (detected vs. injected in summary.json)")
    print("=" * 72)
    for s in summary["sessions"]:
        exp = expected_rules(s)
        got = {f["rule_id"] for f in by_file.get(s["file"], {}).get("findings", [])}
        missed = exp - got
        # "clean" baseline sessions expect nothing; any finding there is a false positive.
        extra = got - exp
        status = "OK"
        if missed:
            status = "MISSED " + ",".join(sorted(missed))
        elif extra and not exp:
            status = "FALSE-POSITIVE " + ",".join(sorted(extra))
        print("  {:<18} expected={:<45} detected={:<30} -> {}".format(
            s["file"],
            "{" + ",".join(sorted(exp)) + "}" if exp else "{clean}",
            "{" + ",".join(sorted(got)) + "}" if got else "{none}",
            status))


if __name__ == "__main__":
    main()
