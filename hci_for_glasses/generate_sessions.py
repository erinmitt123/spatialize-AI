"""
Generate synthetic VR/XR interaction telemetry for testing a usability-analysis tool.

Produces data/session_01.json .. data/session_06.json plus data/summary.json.

Injected patterns (see SUMMARY_DESCRIPTIONS):
  - Sessions 1-3: "problematic" settings_toggle  -> hesitation + repeated failed grabs
  - Sessions 4-5: "clean"       door_handle       -> first-try grabs, tiny distance
  - Session  6 : ui_interaction issue on notes_panel

Only the Python standard library is used.
"""

import json
import os
import random

# --------------------------------------------------------------------------- #
# Tunable injection parameters (adjust these to change the injected patterns)  #
# --------------------------------------------------------------------------- #

SEED = 42                     # set to None for non-reproducible output
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "data")

# --- Problematic object (sessions 1-3) ---
PROBLEM_OBJECT = "settings_toggle"
PROBLEM_SESSIONS = (1, 2, 3)
PROBLEM_GRAB_ATTEMPTS = (2, 4)          # min/max grab_attempts targeting the object
PROBLEM_FAIL_RATE = (0.60, 0.80)        # fraction of attempts that fail before success
PROBLEM_FAIL_DISTANCE = (0.30, 0.50)    # meters, distance_to_target on a failed grab
PROBLEM_SUCCESS_DISTANCE = (0.02, 0.08) # meters, distance on the eventual success
PROBLEM_HESITATION = (1.5, 3.0)         # seconds, hand_pose gap before first attempt

# --- Clean object (sessions 4-5) ---
CLEAN_OBJECT = "door_handle"
CLEAN_SESSIONS = (4, 5)
CLEAN_HESITATION_MAX = 0.5              # seconds, max hesitation before grab
CLEAN_DISTANCE_MAX = 0.10              # meters, max distance_to_target on success

# --- UI issue (session 6) ---
UI_ISSUE_SESSION = 6
UI_ISSUE_COMPONENT = "notes_panel"
UI_ISSUE_FLAG = "content_scroll_in_elevated_panel"

# --- General noise ---
JITTER = 0.01                          # +/- positional jitter (meters) on joints
TIME_JITTER = 0.05                     # +/- jitter (seconds) applied to timestamps

APPS = ["MindfulSpace VR", "AssemblyTrainer", "GalleryWalk", "FocusRoom"]
SCENES = ["main_menu", "workshop", "lounge", "settings_hub", "gallery_a"]

# Key hand joints and their nominal resting positions (meters, headset-relative).
JOINT_BASE = {
    "wrist":      [0.10, -0.20, -0.30],
    "palm":       [0.10, -0.18, -0.34],
    "thumb_tip":  [0.07, -0.15, -0.37],
    "index_tip":  [0.11, -0.14, -0.39],
    "middle_tip": [0.12, -0.15, -0.38],
}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def jit(value, amount):
    """Add small symmetric random noise to a numeric value."""
    return value + random.uniform(-amount, amount)


def rounded(value, digits=4):
    return round(value, digits)


def make_joints():
    """Return a dict of key joints, each a jittered [x, y, z]."""
    return {
        name: [rounded(jit(c, JITTER)) for c in base]
        for name, base in JOINT_BASE.items()
    }


def hand_pose_event(t, hand):
    return {
        "t": rounded(t, 3),
        "type": "hand_pose",
        "hand": hand,
        "joints": make_joints(),
    }


def grab_attempt_event(t, target, hand, success, distance):
    return {
        "t": rounded(t, 3),
        "type": "grab_attempt",
        "target": target,
        "hand": hand,
        "success": success,
        "distance_to_target": rounded(distance),
    }


def controller_input_event(t, hand, button, action):
    return {
        "t": rounded(t, 3),
        "type": "controller_input",
        "hand": hand,
        "button": button,
        "action": action,
    }


def ui_interaction_event(t, component, issue_flag):
    return {
        "t": rounded(t, 3),
        "type": "ui_interaction",
        "component": component,
        "issue_flag": issue_flag,
    }


def ambient_hand_poses(start_t, end_t, hand, step=0.25):
    """Fill an interval with a stream of hand_pose events (models a hesitation gap)."""
    events = []
    t = start_t
    while t < end_t:
        events.append(hand_pose_event(jit(t, TIME_JITTER), hand))
        t += step * random.uniform(0.8, 1.2)
    return events


def some_ambient_controller_noise(t, hand):
    """Occasional benign controller input so sessions aren't only grabs/poses."""
    events = []
    if random.random() < 0.6:
        button = random.choice(["trigger", "grip", "menu"])
        events.append(controller_input_event(t, hand, button, "press"))
        events.append(controller_input_event(t + random.uniform(0.1, 0.4), hand, button, "release"))
    return events


def choose_problem_attempt_profile():
    """
    Pick an (attempt_count, fail_count) pair that exactly satisfies the configured
    fail-rate band after integer rounding.
    """
    valid_profiles = []
    for attempts in range(PROBLEM_GRAB_ATTEMPTS[0], PROBLEM_GRAB_ATTEMPTS[1] + 1):
        for fails in range(1, attempts):
            fail_rate = fails / attempts
            if PROBLEM_FAIL_RATE[0] <= fail_rate <= PROBLEM_FAIL_RATE[1]:
                valid_profiles.append((attempts, fails))
    if not valid_profiles:
        raise ValueError(
            "No valid problem grab profile fits PROBLEM_GRAB_ATTEMPTS={} and "
            "PROBLEM_FAIL_RATE={}.".format(PROBLEM_GRAB_ATTEMPTS, PROBLEM_FAIL_RATE)
        )
    return random.choice(valid_profiles)


# --------------------------------------------------------------------------- #
# Injection: problematic object (sessions 1-3)                                 #
# --------------------------------------------------------------------------- #

def inject_problem_grabs(events, hand, start_t):
    """
    Model a user struggling with PROBLEM_OBJECT:
    hesitation gap of jittery hand poses, then several failed grabs
    (misjudging distance) before an eventual success.
    """
    n_attempts, n_fail = choose_problem_attempt_profile()

    # Hesitation: fill a gap with ambient hand poses before the first attempt.
    hesitation = random.uniform(*PROBLEM_HESITATION)
    events.extend(ambient_hand_poses(start_t, start_t + hesitation, hand))

    t = start_t + hesitation
    fail_distances = []
    for _ in range(n_fail):
        events.append(hand_pose_event(t - random.uniform(0.1, 0.3), hand))
        dist = random.uniform(*PROBLEM_FAIL_DISTANCE)
        fail_distances.append(rounded(dist))
        events.append(grab_attempt_event(t, PROBLEM_OBJECT, hand, False, dist))
        t += random.uniform(0.6, 1.2)   # re-approach time between failed attempts

    # Eventual success.
    success_distance = rounded(random.uniform(*PROBLEM_SUCCESS_DISTANCE))
    events.append(hand_pose_event(t - random.uniform(0.1, 0.3), hand))
    events.append(grab_attempt_event(t, PROBLEM_OBJECT, hand, True, success_distance))
    return t, {
        "kind": "problematic_grab",
        "target": PROBLEM_OBJECT,
        "expected_rule_ids": ["touch_target_size"],
        "attempt_count": n_attempts,
        "fail_count": n_fail,
        "fail_rate": rounded(n_fail / n_attempts, 3),
        "fail_distance_min": min(fail_distances),
        "fail_distance_max": max(fail_distances),
        "success_distance": success_distance,
        "hesitation_seconds": rounded(hesitation, 3),
    }


# --------------------------------------------------------------------------- #
# Injection: clean object (sessions 4-5)                                       #
# --------------------------------------------------------------------------- #

def inject_clean_grab(events, hand, start_t):
    """Model a well-designed object: minimal hesitation, first-try success, close."""
    hesitation = random.uniform(0.05, CLEAN_HESITATION_MAX)
    events.extend(ambient_hand_poses(start_t, start_t + hesitation, hand, step=0.2))

    t = start_t + hesitation
    success_distance = rounded(random.uniform(0.01, CLEAN_DISTANCE_MAX))
    events.append(hand_pose_event(t - random.uniform(0.05, 0.15), hand))
    events.append(grab_attempt_event(t, CLEAN_OBJECT, hand, True, success_distance))
    return t, {
        "kind": "clean_baseline",
        "target": CLEAN_OBJECT,
        "expected_rule_ids": [],
        "attempt_count": 1,
        "fail_count": 0,
        "fail_rate": 0.0,
        "success_distance": success_distance,
        "hesitation_seconds": rounded(hesitation, 3),
    }


# --------------------------------------------------------------------------- #
# Session builder                                                              #
# --------------------------------------------------------------------------- #

def build_session(index, include_ground_truth=False):
    events = []
    issues = []
    hand = random.choice(["left", "right"])
    app = random.choice(APPS)
    scene = random.choice(SCENES)

    # Warm-up ambient activity at the start of every session.
    t = random.uniform(0.5, 2.0)
    events.extend(ambient_hand_poses(0.0, t, hand))
    events.extend(some_ambient_controller_noise(t, hand))

    if index in PROBLEM_SESSIONS:
        t, issue = inject_problem_grabs(events, hand, t + random.uniform(0.3, 0.8))
        issues.append(issue)
        # A little follow-up activity after the struggle.
        events.extend(some_ambient_controller_noise(t + 0.5, hand))

    elif index in CLEAN_SESSIONS:
        t, issue = inject_clean_grab(events, hand, t + random.uniform(0.2, 0.5))
        issues.append(issue)
        events.extend(some_ambient_controller_noise(t + 0.4, hand))

    if index == UI_ISSUE_SESSION:
        # Some benign UI interactions plus the one flagged issue.
        events.append(ui_interaction_event(t + 0.5, "home_button", None))
        flagged_event = ui_interaction_event(t + 1.4, UI_ISSUE_COMPONENT, UI_ISSUE_FLAG)
        events.append(flagged_event)
        events.append(ui_interaction_event(t + 2.2, "close_button", None))
        events.extend(ambient_hand_poses(t + 2.5, t + 3.5, hand))
        issues.append({
            "kind": "ui_issue_flag",
            "component": UI_ISSUE_COMPONENT,
            "issue_flag": UI_ISSUE_FLAG,
            "expected_rule_ids": ["content_type_in_elevated_context"],
            "t": flagged_event["t"],
        })

    # Trailing ambient poses so the session doesn't end abruptly.
    events.extend(ambient_hand_poses(t + 0.2, t + 1.2, hand))

    # Sort strictly by timestamp and clamp any negatives from jitter.
    for e in events:
        if e["t"] < 0:
            e["t"] = 0.0
    events.sort(key=lambda e: e["t"])

    session = {
        "session_id": "sess_{:02d}_{:04d}".format(index, random.randint(1000, 9999)),
        "app": app,
        "scene": scene,
        "events": events,
    }
    if include_ground_truth:
        return session, issues
    return session


# --------------------------------------------------------------------------- #
# Summary descriptions (ground truth for verifying the analysis tool)          #
# --------------------------------------------------------------------------- #

def describe_issues(issues):
    if not issues:
        return "No issue injected."

    parts = []
    for issue in issues:
        if issue["kind"] == "problematic_grab":
            parts.append(
                "Injected usability problem: '{target}' needed {attempt_count} grab "
                "attempts, {fail_count} failed ({fail_rate:.0%}), failed "
                "distance_to_target {fail_distance_min:.2f}-{fail_distance_max:.2f}m, "
                "final success at {success_distance:.2f}m after {hesitation_seconds:.2f}s "
                "of injected hand-pose hesitation.".format(**issue)
            )
        elif issue["kind"] == "clean_baseline":
            parts.append(
                "Clean baseline: '{target}' succeeded on the first try after "
                "{hesitation_seconds:.2f}s of hesitation at {success_distance:.2f}m "
                "(no injected issue).".format(**issue)
            )
        elif issue["kind"] == "ui_issue_flag":
            parts.append(
                "Injected UI issue: ui_interaction on '{component}' at t={t:.2f}s "
                "flagged '{issue_flag}'.".format(**issue)
            )
        else:
            parts.append("Unknown injected issue metadata.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main():
    if SEED is not None:
        random.seed(SEED)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    summary = {"sessions": []}
    for index in range(1, 7):
        session, issues = build_session(index, include_ground_truth=True)
        filename = "session_{:02d}.json".format(index)
        path = os.path.join(OUTPUT_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2)

        expected_rule_ids = sorted({
            rule_id
            for issue in issues
            for rule_id in issue.get("expected_rule_ids", [])
        })
        summary["sessions"].append({
            "file": filename,
            "session_id": session["session_id"],
            "app": session["app"],
            "scene": session["scene"],
            "event_count": len(session["events"]),
            "expected_rule_ids": expected_rule_ids,
            "issues": issues,
            "injected_issue": describe_issues(issues),
        })
        print("wrote {} ({} events)".format(path, len(session["events"])))

    summary_path = os.path.join(OUTPUT_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("wrote {}".format(summary_path))


if __name__ == "__main__":
    main()
