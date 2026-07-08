"""
Analyze VR/XR interaction telemetry against the Android XR Spatial HCI rubric.

Reads:  xr_hci_rubric.json  and session JSON files from a directory or one file
Writes: data/analysis_report.json  (findings per session, in the rubric's
        output_format_for_agent shape)
Prints: a human-readable report, and if data/summary.json (ground truth)
        exists, a precision/recall check so you can confirm the tool finds
        what was actually injected.

Findings are derived only from telemetry signals the rubric names, using the
click/grab interaction data and the 6DoF hand/target positions in each session.

Standard library only.
"""

import argparse
import collections
import datetime
import glob
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKTREE_DIR = os.path.dirname(BASE_DIR)
RUBRIC_PATH = os.path.join(BASE_DIR, "xr_hci_rubric.json")
LENS_DIR = os.path.join(BASE_DIR, "lenses")
LENS_PREFERENCES_PATH = os.path.join(BASE_DIR, "lens_preferences.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORT_PATH = os.path.join(DATA_DIR, "analysis_report.json")
SUMMARY_PATH = os.path.join(DATA_DIR, "summary.json")
RULES_MANAGER_PATH = os.path.join(BASE_DIR, "manage_rubric_rules.py")
SESSION_GLOB = "session_*.json"

# --------------------------------------------------------------------------- #
# Detection thresholds (tune these; they map directly to the rubric signals)   #
# --------------------------------------------------------------------------- #

MIN_ATTEMPTS = 2            # need repeated attempts on a target to judge it
FAIL_RATE_THRESHOLD = 0.50  # >= this share of failed grabs => "hard target"
LARGE_FAIL_DISTANCE = 0.20  # m; failed grabs beyond this => misjudged position
DISTANCE_VARIANCE_MIN = 0.02  # m stdev across attempts => weak pre-contact cue
HESITATION_GAP = 1.50       # s of pose-only quiet before first grab => hesitation
RAPID_CLICK_GAP = 0.75      # s; consecutive clicks closer than this form a cluster
MIN_RAPID_CLICKS = 3        # clicks in one rapid cluster on a control => frustration
MIN_MISSED_TAPS = 3         # taps that hit no control => wrong-spot hunting
DEFAULT_ANALYSIS_MODE = "hybrid"
DEFAULT_AI_MODEL = os.environ.get("XR_AI_MODEL", "gpt-4.1-mini")
DEFAULT_AI_API_KEY_ENV = os.environ.get("XR_AI_API_KEY_ENV", "OPENAI_API_KEY")
DEFAULT_AI_BASE_URL = os.environ.get(
    "XR_AI_BASE_URL", "https://api.openai.com/v1/chat/completions"
)
DEFAULT_AI_TIMEOUT_SECONDS = 45.0
DEFAULT_AI_MAX_EVENTS = 180
DEFAULT_AI_MAX_FINDINGS = 8
DEFAULT_MAX_LENS_RULES = 8
DEFAULT_MAX_LENS_RULES_PER_LENS = 3
DEFAULT_PERSISTED_LENSES = [
    "low_vision_accessibility",
    "manufacturing",
]

# Map a ui_interaction issue_flag string to the rubric rule it evidences.
ISSUE_FLAG_TO_RULE = {
    "content_scroll_in_elevated_panel": "content_type_in_elevated_context",
}


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #

def load_rubric_document(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize_lens_id(value):
    normalized = re.sub(r"[^a-z0-9]+", "_", normalize_whitespace(value).lower()).strip("_")
    return normalized


def parse_csv_list(value):
    if not value:
        return []
    values = []
    for part in str(value).split(","):
        normalized = normalize_whitespace(part)
        if normalized:
            values.append(normalized)
    return values


def annotate_rule(
    rule,
    source_type,
    lens_id=None,
    lens_name=None,
    lens_path=None,
):
    annotated = dict(rule)
    annotated["rule_source_type"] = source_type
    if lens_id:
        annotated["lens_id"] = lens_id
    if lens_name:
        annotated["lens_name"] = lens_name
    if lens_path:
        annotated["lens_path"] = display_path(lens_path)
    return annotated


def get_disabled_rule_ids(rubric):
    disabled = set(rubric.get("disabled_rule_ids", []))
    for rule in rubric.get("rules", []):
        if rule.get("disabled"):
            disabled.add(rule["id"])
    return sorted(disabled)


def load_rubric(path):
    rubric = load_rubric_document(path)
    disabled_rule_ids = set(get_disabled_rule_ids(rubric))
    rules = {
        rule["id"]: annotate_rule(rule, "base")
        for rule in rubric["rules"]
        if rule["id"] not in disabled_rule_ids
    }
    return rules, rubric, sorted(disabled_rule_ids)


def load_lens_registry(lens_dir=LENS_DIR):
    registry = {}
    if not os.path.isdir(lens_dir):
        return registry

    for path in sorted(glob.glob(os.path.join(lens_dir, "*.json"))):
        document = load_rubric_document(path)
        lens_id = normalize_lens_id(os.path.splitext(os.path.basename(path))[0])
        lens_name = document.get("rubric_name") or lens_id
        aliases = {
            lens_id,
            normalize_lens_id(lens_name),
            normalize_lens_id(document.get("persona")),
        }
        if "low_vision" in lens_id:
            aliases.update({"low_vision", "accessibility", "low_vision_accessibility"})
        aliases = {alias for alias in aliases if alias}
        rules = {}
        for rule in document.get("rules", []):
            rules[rule["id"]] = annotate_rule(
                rule,
                "lens",
                lens_id=lens_id,
                lens_name=lens_name,
                lens_path=path,
            )
        registry[lens_id] = {
            "id": lens_id,
            "name": lens_name,
            "path": path,
            "document": document,
            "rules": rules,
            "aliases": sorted(aliases),
            "persona": document.get("persona"),
            "purpose": document.get("purpose"),
            "source": document.get("source"),
        }
    return registry


def lens_alias_map(lens_registry):
    aliases = {}
    for lens_id, lens in lens_registry.items():
        aliases[lens_id] = lens_id
        for alias in lens.get("aliases", []):
            aliases[alias] = lens_id
    return aliases


def normalize_requested_lens_ids(raw_values, lens_registry):
    aliases = lens_alias_map(lens_registry)
    resolved = []
    for raw in raw_values:
        for part in parse_csv_list(raw):
            normalized = normalize_lens_id(part)
            resolved_id = aliases.get(normalized)
            if resolved_id and resolved_id not in resolved:
                resolved.append(resolved_id)
    return resolved


def build_available_lens_summaries(lens_registry):
    return [
        {
            "id": lens["id"],
            "name": lens["name"],
            "path": display_path(lens["path"]),
            "persona": lens.get("persona"),
            "purpose": lens.get("purpose"),
            "rule_count": len(lens.get("rules", {})),
        }
        for lens in lens_registry.values()
    ]


def load_lens_preferences(lens_registry, path=LENS_PREFERENCES_PATH):
    document = {}
    if os.path.exists(path):
        try:
            document = load_rubric_document(path)
        except (OSError, json.JSONDecodeError):
            document = {}

    explicit_defaults = normalize_requested_lens_ids(
        document.get("default_active_lenses", []),
        lens_registry,
    )
    default_active_lenses = explicit_defaults or normalize_requested_lens_ids(
        DEFAULT_PERSISTED_LENSES,
        lens_registry,
    )
    return {
        "path": path,
        "document": document,
        "default_active_lenses": default_active_lenses,
        "default_active_lens_names": lens_names_for_ids(default_active_lenses, lens_registry),
    }


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


def make_finding(
    rules,
    rule_id,
    target,
    evidence,
    fix,
    related=None,
    severity=None,
    judgment=None,
    analysis_method=None,
    confidence=None,
):
    """Build one finding in the rubric's output_format_for_agent shape."""
    rule = rules[rule_id]
    finding = {
        "rule_id": rule_id,
        "rule_text": rule["rule"],
        "target": target,
        "evidence_from_telemetry": evidence,
        "suggested_fix": fix,
        "severity": severity or rule["severity_if_violated"],
    }
    for key in ("rule_source_type", "lens_id", "lens_name", "lens_path", "detection_method"):
        if rule.get(key):
            finding[key] = rule[key]
    if related:
        finding["related_rule_ids"] = related
    if judgment:
        finding["judgment"] = judgment
    if analysis_method:
        finding["analysis_method"] = analysis_method
    if confidence:
        finding["confidence"] = confidence
    return finding


def finding_review_key(finding):
    target = finding.get("target") or "unknown"
    return "{}::{}".format(finding["rule_id"], target)


def decorate_finding(finding):
    decorated = dict(finding)
    decorated["interpretation"] = interpret_finding(finding)
    decorated["review_key"] = finding_review_key(finding)
    return decorated


def json_for_html(value):
    return json.dumps(value).replace("</", "<\\/")


def display_path(path):
    if not path:
        return path
    if not os.path.isabs(path):
        return path
    try:
        return os.path.relpath(path, WORKTREE_DIR)
    except ValueError:
        return path


def resolve_session_paths(input_dir=None, input_file=None):
    if input_file:
        if not os.path.exists(input_file):
            raise FileNotFoundError("Session file not found: {}".format(input_file))
        return [input_file], input_file

    session_dir = input_dir or DATA_DIR
    session_paths = sorted(glob.glob(os.path.join(session_dir, SESSION_GLOB)))
    return session_paths, session_dir


def summarize_telemetry(session):
    events = session.get("events", [])
    counts = collections.Counter(e.get("type", "unknown") for e in events)
    targets = sorted({
        e.get("target")
        for e in events
        if e.get("target")
    })
    components = sorted({
        e.get("component")
        for e in events
        if e.get("component")
    })

    clicks = sum(
        1
        for e in events
        if e.get("type") == "ui_interaction" and e.get("action") == "click"
    )
    runtime_event_types = {
        "rotation_change",
        "transform_change",
        "spatial_transform",
        "spatial_resize",
    }

    return {
        "event_count": len(events),
        "click_count": clicks,
        "rotation_change_count": counts.get("rotation_change", 0),
        "transform_change_count": counts.get("transform_change", 0),
        "spatial_transform_count": counts.get("spatial_transform", 0),
        "spatial_resize_count": counts.get("spatial_resize", 0),
        "hand_pose_count": counts.get("hand_pose", 0),
        "ui_interaction_count": counts.get("ui_interaction", 0),
        "grab_attempt_count": counts.get("grab_attempt", 0),
        "targets": targets,
        "components": components,
        "contains_runtime_telemetry": any(
            counts.get(event_type, 0) > 0 for event_type in runtime_event_types
        ),
    }


def normalize_whitespace(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_analysis_mode(value):
    mode = normalize_whitespace(value).lower() or DEFAULT_ANALYSIS_MODE
    if mode not in {"heuristic", "ai", "hybrid"}:
        return DEFAULT_ANALYSIS_MODE
    return mode


def normalize_level(value, allowed, fallback):
    normalized = normalize_whitespace(value).lower()
    if normalized in allowed:
        return normalized
    return fallback


def severity_rank(value):
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


def top_counter_entries(counter, limit=8):
    items = counter.most_common(limit)
    return {key: count for key, count in items}


def vector_magnitude(value):
    if not isinstance(value, dict):
        return None
    coords = []
    for key in ("x", "y", "z"):
        number = value.get(key)
        if isinstance(number, (int, float)):
            coords.append(float(number))
    if not coords:
        return None
    return round(sum(number ** 2 for number in coords) ** 0.5, 4)


def delta_magnitude(current, previous):
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return None
    deltas = []
    for key in ("x", "y", "z", "w"):
        left = current.get(key)
        right = previous.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            deltas.append(float(left) - float(right))
    if not deltas:
        return None
    return round(sum(delta ** 2 for delta in deltas) ** 0.5, 4)


def event_focus_label(event):
    return (
        event.get("target")
        or event.get("component")
        or event.get("hand")
        or event.get("type")
        or "unknown"
    )


def sample_evenly(items, limit):
    if len(items) <= limit:
        return list(items)
    indexes = {
        round(index * (len(items) - 1) / float(limit - 1))
        for index in range(limit)
    }
    return [item for item_index, item in enumerate(items) if item_index in indexes]


def compact_event(event, event_index):
    compact = {
        "index": event_index,
        "t": event.get("t"),
        "type": event.get("type"),
    }
    for key in (
        "target",
        "component",
        "action",
        "source",
        "phase",
        "group",
        "issue_flag",
        "success",
        "distance_to_target",
        "scale",
        "hand",
    ):
        if key in event:
            compact[key] = event.get(key)
    for key in (
        "translation",
        "rotation",
        "size",
        "pose",
        "previous_pose",
        "previous_scale",
        "joints",
    ):
        if key in event:
            compact[key] = event.get(key)
    return compact


def build_target_profiles(events):
    profiles = {}
    previous_rotation = {}
    previous_translation = {}
    previous_pose = {}
    previous_size = {}

    for event in events:
        target = event.get("target")
        if not target:
            continue

        profile = profiles.setdefault(
            target,
            {
                "target": target,
                "event_count": 0,
                "by_type": collections.Counter(),
                "actions": collections.Counter(),
                "sources": collections.Counter(),
                "phases": collections.Counter(),
                "first_t": event.get("t"),
                "last_t": event.get("t"),
                "grab_successes": 0,
                "grab_failures": 0,
                "grab_distances": [],
                "rotation_step_deltas": [],
                "translation_step_deltas": [],
                "translation_magnitudes": [],
                "scale_values": [],
                "spatial_move_deltas": [],
                "resize_volume_values": [],
            },
        )

        profile["event_count"] += 1
        profile["by_type"][event.get("type", "unknown")] += 1
        profile["sources"][event.get("source", "unknown")] += 1
        if event.get("action"):
            profile["actions"][event["action"]] += 1
        if event.get("phase"):
            profile["phases"][event["phase"]] += 1
        if event.get("t") is not None:
            if profile["first_t"] is None or event["t"] < profile["first_t"]:
                profile["first_t"] = event["t"]
            if profile["last_t"] is None or event["t"] > profile["last_t"]:
                profile["last_t"] = event["t"]

        if event.get("type") == "grab_attempt":
            if event.get("success"):
                profile["grab_successes"] += 1
            else:
                profile["grab_failures"] += 1
            distance = event.get("distance_to_target")
            if isinstance(distance, (int, float)):
                profile["grab_distances"].append(float(distance))

        rotation = event.get("rotation")
        if isinstance(rotation, dict):
            delta = delta_magnitude(rotation, previous_rotation.get(target))
            if delta is not None:
                profile["rotation_step_deltas"].append(delta)
            previous_rotation[target] = rotation

        translation = event.get("translation")
        if isinstance(translation, dict):
            magnitude = vector_magnitude(translation)
            if magnitude is not None:
                profile["translation_magnitudes"].append(magnitude)
            delta = delta_magnitude(translation, previous_translation.get(target))
            if delta is not None:
                profile["translation_step_deltas"].append(delta)
            previous_translation[target] = translation

        scale = event.get("scale")
        if isinstance(scale, (int, float)):
            profile["scale_values"].append(float(scale))

        pose = event.get("pose", {})
        if isinstance(pose, dict):
            pose_translation = pose.get("translation")
            delta = delta_magnitude(pose_translation, previous_pose.get(target))
            if delta is not None:
                profile["spatial_move_deltas"].append(delta)
            if isinstance(pose_translation, dict):
                previous_pose[target] = pose_translation

        size = event.get("size")
        if isinstance(size, dict):
            volume = 1
            saw_number = False
            for key in ("width", "height", "depth"):
                number = size.get(key)
                if isinstance(number, (int, float)):
                    volume *= float(number)
                    saw_number = True
            if saw_number:
                profile["resize_volume_values"].append(round(volume, 4))
            previous_size[target] = size

    summarized = []
    for profile in profiles.values():
        item = {
            "target": profile["target"],
            "event_count": profile["event_count"],
            "time_window_s": round((profile["last_t"] or 0) - (profile["first_t"] or 0), 3),
            "by_type": top_counter_entries(profile["by_type"]),
            "actions": top_counter_entries(profile["actions"]),
            "sources": top_counter_entries(profile["sources"]),
            "phases": top_counter_entries(profile["phases"]),
        }
        if profile["grab_successes"] or profile["grab_failures"]:
            attempts = profile["grab_successes"] + profile["grab_failures"]
            item["grab_stats"] = {
                "attempts": attempts,
                "successes": profile["grab_successes"],
                "failures": profile["grab_failures"],
                "mean_distance": round(mean(profile["grab_distances"]), 4),
                "max_distance": round(max(profile["grab_distances"]), 4)
                if profile["grab_distances"] else None,
            }
        if profile["rotation_step_deltas"]:
            item["rotation_stats"] = {
                "step_count": len(profile["rotation_step_deltas"]),
                "mean_step_delta": round(mean(profile["rotation_step_deltas"]), 4),
                "max_step_delta": round(max(profile["rotation_step_deltas"]), 4),
            }
        if profile["translation_magnitudes"] or profile["translation_step_deltas"] or profile["scale_values"]:
            item["transform_stats"] = {
                "mean_translation_magnitude": round(
                    mean(profile["translation_magnitudes"]), 4
                ) if profile["translation_magnitudes"] else None,
                "max_translation_step": round(
                    max(profile["translation_step_deltas"]), 4
                ) if profile["translation_step_deltas"] else None,
                "min_scale": round(min(profile["scale_values"]), 4)
                if profile["scale_values"] else None,
                "max_scale": round(max(profile["scale_values"]), 4)
                if profile["scale_values"] else None,
            }
        if profile["spatial_move_deltas"] or profile["resize_volume_values"]:
            item["spatial_stats"] = {
                "max_move_delta": round(max(profile["spatial_move_deltas"]), 4)
                if profile["spatial_move_deltas"] else None,
                "min_resize_volume": round(min(profile["resize_volume_values"]), 4)
                if profile["resize_volume_values"] else None,
                "max_resize_volume": round(max(profile["resize_volume_values"]), 4)
                if profile["resize_volume_values"] else None,
            }
        summarized.append(item)

    return sorted(summarized, key=lambda item: (-item["event_count"], item["target"]))


def build_component_profiles(events):
    profiles = {}
    previous_action_time = {}

    for event in events:
        component = event.get("component")
        if not component:
            continue

        profile = profiles.setdefault(
            component,
            {
                "component": component,
                "event_count": 0,
                "actions": collections.Counter(),
                "sources": collections.Counter(),
                "targets": collections.Counter(),
                "issue_flags": collections.Counter(),
                "rapid_repeat_gaps": [],
                "first_t": event.get("t"),
                "last_t": event.get("t"),
            },
        )
        profile["event_count"] += 1
        if event.get("action"):
            profile["actions"][event["action"]] += 1
        if event.get("source"):
            profile["sources"][event["source"]] += 1
        if event.get("target"):
            profile["targets"][event["target"]] += 1
        if event.get("issue_flag"):
            profile["issue_flags"][event["issue_flag"]] += 1
        if event.get("t") is not None:
            if profile["first_t"] is None or event["t"] < profile["first_t"]:
                profile["first_t"] = event["t"]
            if profile["last_t"] is None or event["t"] > profile["last_t"]:
                profile["last_t"] = event["t"]

        action_key = "{}::{}".format(component, event.get("action", event.get("type")))
        previous_t = previous_action_time.get(action_key)
        current_t = event.get("t")
        if isinstance(previous_t, (int, float)) and isinstance(current_t, (int, float)):
            gap = current_t - previous_t
            if gap <= 0.75:
                profile["rapid_repeat_gaps"].append(round(gap, 3))
        if isinstance(current_t, (int, float)):
            previous_action_time[action_key] = current_t

    summarized = []
    for profile in profiles.values():
        item = {
            "component": profile["component"],
            "event_count": profile["event_count"],
            "time_window_s": round((profile["last_t"] or 0) - (profile["first_t"] or 0), 3),
            "actions": top_counter_entries(profile["actions"]),
            "sources": top_counter_entries(profile["sources"]),
            "targets": top_counter_entries(profile["targets"]),
            "issue_flags": top_counter_entries(profile["issue_flags"]),
        }
        if profile["rapid_repeat_gaps"]:
            item["rapid_repeat_stats"] = {
                "count": len(profile["rapid_repeat_gaps"]),
                "fastest_gap_s": round(min(profile["rapid_repeat_gaps"]), 3),
            }
        summarized.append(item)

    return sorted(summarized, key=lambda item: (-item["event_count"], item["component"]))


def build_behavioral_signals(events):
    actionable_types = {"ui_interaction", "grab_attempt", "controller_input"}
    actionable_events = [event for event in events if event.get("type") in actionable_types]
    rapid_repeats = []
    focus_switch_count = 0
    previous_focus = None
    previous_event = None

    for event in actionable_events:
        focus = event_focus_label(event)
        if previous_focus and focus != previous_focus:
            focus_switch_count += 1
        if previous_event:
            previous_focus_label = event_focus_label(previous_event)
            current_t = event.get("t")
            previous_t = previous_event.get("t")
            if (
                focus == previous_focus_label
                and event.get("action", event.get("type"))
                == previous_event.get("action", previous_event.get("type"))
                and isinstance(current_t, (int, float))
                and isinstance(previous_t, (int, float))
            ):
                gap = current_t - previous_t
                if gap <= 0.75:
                    rapid_repeats.append({
                        "focus": focus,
                        "action": event.get("action", event.get("type")),
                        "gap_s": round(gap, 3),
                    })
        previous_focus = focus
        previous_event = event

    issue_flags = []
    for event in events:
        flag = event.get("issue_flag")
        if flag:
            issue_flags.append({
                "t": event.get("t"),
                "component": event.get("component"),
                "target": event.get("target"),
                "issue_flag": flag,
            })

    runtime_types = (
        "rotation_change",
        "transform_change",
        "spatial_transform",
        "spatial_resize",
        "hand_pose",
    )
    runtime_mix = collections.Counter(
        event.get("type") for event in events if event.get("type") in runtime_types
    )

    first_action_t = actionable_events[0].get("t") if actionable_events else None
    return {
        "first_action_t_s": first_action_t,
        "action_count": len(actionable_events),
        "focus_switch_count": focus_switch_count,
        "rapid_repeat_sequences": rapid_repeats[:8],
        "issue_flags": issue_flags[:8],
        "runtime_signal_mix": top_counter_entries(runtime_mix, limit=10),
    }


def is_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def tokenize_for_retrieval(value):
    tokens = set()
    normalized = normalize_whitespace(value).lower().replace("-", "_")
    for token in re.findall(r"[a-z0-9_]+", normalized):
        if len(token) >= 2:
            tokens.add(token)
        for part in token.split("_"):
            if len(part) >= 2:
                tokens.add(part)
    return tokens


def lens_names_for_ids(lens_ids, lens_registry):
    names = []
    for lens_id in lens_ids:
        lens = lens_registry.get(lens_id)
        names.append(lens["name"] if lens else lens_id)
    return names


def extract_session_lens_context(session, forced_lens_ids, lens_registry):
    metadata = session.get("metadata", {}) or {}
    inferred_lens_ids = []
    session_default_lenses = []
    for key in ("analysis_lenses", "analysis_lens_ids", "analysis_focus_lenses"):
        session_default_lenses.extend(
            normalize_requested_lens_ids([metadata.get(key)], lens_registry)
        )
    session_default_lenses = list(dict.fromkeys(session_default_lenses))

    persona_lenses = normalize_requested_lens_ids(
        [metadata.get("user_persona")], lens_registry
    )
    for lens_id in persona_lenses:
        if lens_id not in inferred_lens_ids:
            inferred_lens_ids.append(lens_id)

    accessibility_settings = session.get("accessibility_settings")
    if isinstance(accessibility_settings, dict):
        if any(
            is_truthy(accessibility_settings.get(key))
            for key in (
                "low_vision_mode",
                "high_contrast_mode",
                "relumino_outline",
                "screen_reader_enabled",
            )
        ):
            low_vision_id = normalize_requested_lens_ids(
                ["low_vision_accessibility"], lens_registry
            )
            for lens_id in low_vision_id:
                if lens_id not in inferred_lens_ids:
                    inferred_lens_ids.append(lens_id)

    current_lenses = list(dict.fromkeys(session_default_lenses + inferred_lens_ids))
    lenses_seen = set(current_lenses)
    live_toggle_history = []
    for event in session.get("events", []):
        if event.get("type") != "analysis_lens_change":
            continue
        snapshot_lenses = normalize_requested_lens_ids(
            [event.get("active_lenses"), event.get("analysis_lenses")],
            lens_registry,
        )
        lens_id = normalize_requested_lens_ids(
            [event.get("lens_id"), event.get("target"), event.get("component")],
            lens_registry,
        )
        resolved_lens_id = lens_id[0] if lens_id else None
        enabled = event.get("enabled")
        if snapshot_lenses:
            current_lenses = snapshot_lenses
        elif resolved_lens_id:
            current_set = set(current_lenses)
            if enabled is False or (
                isinstance(enabled, str) and enabled.strip().lower() == "false"
            ):
                current_set.discard(resolved_lens_id)
            else:
                current_set.add(resolved_lens_id)
            current_lenses = sorted(current_set)
        if resolved_lens_id:
            lenses_seen.add(resolved_lens_id)
        lenses_seen.update(current_lenses)
        live_toggle_history.append({
            "t": event.get("t"),
            "lens_id": resolved_lens_id,
            "enabled": enabled,
            "active_lenses": list(current_lenses),
        })

    current_active_lenses = list(dict.fromkeys(forced_lens_ids + current_lenses))
    retrieval_lenses = list(dict.fromkeys(
        forced_lens_ids + sorted(lenses_seen) + inferred_lens_ids
    ))
    return {
        "forced_lenses": forced_lens_ids,
        "session_default_lenses": session_default_lenses,
        "inferred_lenses": inferred_lens_ids,
        "current_active_lenses": current_active_lenses,
        "retrieval_lenses": retrieval_lenses,
        "lenses_seen_during_session": sorted(lenses_seen),
        "live_toggle_history": live_toggle_history,
        "current_active_lens_names": lens_names_for_ids(current_active_lenses, lens_registry),
        "retrieval_lens_names": lens_names_for_ids(retrieval_lenses, lens_registry),
    }


def build_lens_retrieval_query(session, telemetry_summary, heuristic_findings, lens_context):
    event_type_counts = collections.Counter(
        event.get("type", "unknown") for event in session.get("events", [])
    )
    parts = [
        session.get("app"),
        session.get("scene"),
        " ".join(lens_context.get("retrieval_lenses", [])),
        " ".join(telemetry_summary.get("targets", [])),
        " ".join(telemetry_summary.get("components", [])),
        " ".join(
            "{} {}".format(event_type, count)
            for event_type, count in sorted(event_type_counts.items())
        ),
        " ".join(
            "{} {}".format(key, value)
            for key, value in sorted((session.get("metadata", {}) or {}).items())
        ),
    ]
    accessibility_settings = session.get("accessibility_settings")
    if isinstance(accessibility_settings, dict):
        parts.append(" ".join(
            "{} {}".format(key, value)
            for key, value in sorted(accessibility_settings.items())
        ))
    for finding in heuristic_findings[:DEFAULT_AI_MAX_FINDINGS]:
        parts.append(" ".join(filter(None, [
            finding.get("rule_id"),
            finding.get("target"),
            finding.get("evidence_from_telemetry"),
        ])))
    return normalize_whitespace(" ".join(part for part in parts if part))


def lens_rule_retrieval_score(rule, lens, query_tokens, heuristic_findings):
    rule_text = " ".join(filter(None, [
        rule.get("id"),
        rule.get("rule"),
        rule.get("why_it_matters"),
        rule.get("telemetry_signal"),
        rule.get("detection_method"),
        lens.get("purpose"),
        lens.get("persona"),
    ]))
    rule_tokens = tokenize_for_retrieval(rule_text)
    matched_terms = sorted(query_tokens & rule_tokens)
    score = len(matched_terms) * 2
    telemetry_signal = normalize_whitespace(rule.get("telemetry_signal")).lower()
    for keyword in (
        "grab_attempt",
        "controller_input",
        "ui_interaction",
        "hand_pose",
        "rotation",
        "transform",
        "spatial",
        "resize",
        "contrast",
        "error",
        "undo",
        "confirmation",
        "discoverability",
        "alignment",
    ):
        if keyword in telemetry_signal and keyword in query_tokens:
            score += 2
    if rule.get("detection_method") == "static_design_review":
        score -= 1
    heuristic_rule_ids = {finding.get("rule_id") for finding in heuristic_findings}
    for heuristic_rule_id in heuristic_rule_ids:
        if heuristic_rule_id and heuristic_rule_id in telemetry_signal:
            score += 2
    return score, matched_terms


def build_lens_focus_reason(rule, lens, matched_terms):
    if matched_terms:
        return "Matched session signals: {}.".format(", ".join(matched_terms[:6]))
    if rule.get("detection_method") == "static_design_review":
        return (
            "Added because this lens is active, but this heuristic still needs a "
            "static design review rather than telemetry-only judgment."
        )
    return "Added from the active lens as a focused review heuristic for this session."


def retrieve_lens_focus(session, telemetry_summary, heuristic_findings, lens_context, lens_registry):
    retrieval_lenses = lens_context.get("retrieval_lenses", [])
    if not retrieval_lenses:
        return [], {}

    query_text = build_lens_retrieval_query(
        session, telemetry_summary, heuristic_findings, lens_context
    )
    query_tokens = tokenize_for_retrieval(query_text)
    selected_entries = []
    selected_rules = {}

    for lens_id in retrieval_lenses:
        lens = lens_registry.get(lens_id)
        if not lens:
            continue
        scored = []
        for rule in lens.get("rules", {}).values():
            score, matched_terms = lens_rule_retrieval_score(
                rule, lens, query_tokens, heuristic_findings
            )
            scored.append((score, matched_terms, rule))
        scored.sort(
            key=lambda item: (
                -item[0],
                1 if item[2].get("detection_method") == "static_design_review" else 0,
                item[2]["id"],
            )
        )
        desired_count = min(DEFAULT_MAX_LENS_RULES_PER_LENS, len(scored))
        if desired_count == 0:
            continue
        chosen = scored[:desired_count]
        for score, matched_terms, rule in chosen:
            entry = {
                "lens_id": lens_id,
                "lens_name": lens["name"],
                "rule_id": rule["id"],
                "rule_text": rule.get("rule"),
                "why_it_matters": rule.get("why_it_matters"),
                "telemetry_signal": rule.get("telemetry_signal"),
                "detection_method": rule.get("detection_method", "telemetry_inferable"),
                "retrieval_score": score,
                "matched_terms": matched_terms[:8],
                "focus_reason": build_lens_focus_reason(rule, lens, matched_terms),
            }
            selected_entries.append(entry)
            selected_rules[rule["id"]] = rule

    selected_entries.sort(
        key=lambda entry: (
            0 if entry["lens_id"] in lens_context.get("current_active_lenses", []) else 1,
            -entry["retrieval_score"],
            entry["rule_id"],
        )
    )
    selected_entries = selected_entries[:DEFAULT_MAX_LENS_RULES]
    selected_rule_ids = {entry["rule_id"] for entry in selected_entries}
    selected_rules = {
        rule_id: rule for rule_id, rule in selected_rules.items()
        if rule_id in selected_rule_ids
    }
    return selected_entries, selected_rules


def build_ai_prompt_payload(
    session,
    rules,
    telemetry_summary,
    heuristic_findings,
    args,
    lens_context,
    retrieved_lens_focus,
):
    events = session.get("events", [])
    sampled_events = sample_evenly(list(enumerate(events)), args.ai_max_events)
    timeline = [compact_event(event, index) for index, event in sampled_events]
    rules_for_prompt = []
    for rule_id, rule in sorted(rules.items()):
        rules_for_prompt.append({
            "id": rule_id,
            "rule": rule.get("rule"),
            "why_it_matters": rule.get("why_it_matters"),
            "telemetry_signal": rule.get("telemetry_signal"),
            "default_severity": rule.get("severity_if_violated"),
        })
    heuristic_candidates = []
    for finding in heuristic_findings[: args.ai_max_findings]:
        heuristic_candidates.append({
            "rule_id": finding.get("rule_id"),
            "target": finding.get("target"),
            "severity": finding.get("severity"),
            "evidence_from_telemetry": finding.get("evidence_from_telemetry"),
            "suggested_fix": finding.get("suggested_fix"),
            "related_rule_ids": finding.get("related_rule_ids", []),
        })
    return {
        "session": {
            "session_id": session.get("session_id"),
            "app": session.get("app"),
            "scene": session.get("scene"),
            "started_at_epoch_ms": session.get("started_at_epoch_ms"),
            "ended_at_epoch_ms": session.get("ended_at_epoch_ms"),
            "metadata": session.get("metadata", {}),
            "accessibility_settings": session.get("accessibility_settings", {}),
        },
        "lens_context": lens_context,
        "retrieved_lens_focus": retrieved_lens_focus,
        "telemetry_summary": telemetry_summary,
        "behavioral_signals": build_behavioral_signals(events),
        "target_profiles": build_target_profiles(events)[:12],
        "component_profiles": build_component_profiles(events)[:12],
        "timeline_excerpt": {
            "sampled_event_count": len(timeline),
            "total_event_count": len(events),
            "events": timeline,
        },
        "heuristic_candidates": heuristic_candidates,
        "active_rubric_rules": rules_for_prompt,
    }


def build_ai_messages(prompt_payload):
    system_message = (
        "You are a senior XR UX researcher reviewing one Android XR telemetry "
        "session. Your job is to read noisy, multi-signal telemetry and make the "
        "judgment call a strong human researcher would make: which rubric pattern "
        "this most likely reflects, how severe it is in context, and what specific "
        "fix to recommend. Do not blindly flag single metrics. Weigh timing, "
        "repetition, reversals, missing feedback, spatial adjustments, transforms, "
        "empty telemetry, signal sparsity, and any active specialization lenses "
        "together. Base rubric heuristics always apply. Retrieved specialization "
        "lens rules are an additional focus layer, not a replacement. Only emit a "
        "finding when the telemetry or an explicitly needed static review item "
        "supports a likely real issue. If a retrieved lens rule says static design "
        "review is required, say that explicitly in the evidence text. If data is "
        "sparse, say that clearly and return fewer findings."
    )
    user_message = {
        "task": (
            "Return JSON only with keys: session_summary, overall_confidence, "
            "findings. findings must be a list of objects with keys: rule_id, "
            "target, severity, evidence_from_telemetry, suggested_fix, "
            "related_rule_ids, judgment, confidence. rule_id must match one of "
            "the provided active_rubric_rules. evidence_from_telemetry must cite "
            "concrete telemetry details, or say static review required when the "
            "retrieved lens rule is design-review-only. suggested_fix must be "
            "XR-specific and actionable. Use an empty findings list when there is "
            "no credible issue."
        ),
        "payload": prompt_payload,
    }
    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": json.dumps(user_message, indent=2)},
    ]


def extract_chat_completion_text(response_json):
    choices = response_json.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("text"), dict):
                    parts.append(str(item["text"].get("value", "")))
        return "\n".join(part for part in parts if part)
    return ""


def parse_json_object_from_text(value):
    text = normalize_whitespace(value)
    if not text:
        raise ValueError("AI response was empty.")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def normalize_ai_findings(raw_response, rules):
    findings = []
    for finding in raw_response.get("findings", []):
        if not isinstance(finding, dict):
            continue
        rule_id = normalize_whitespace(finding.get("rule_id"))
        if rule_id not in rules:
            continue
        evidence = normalize_whitespace(finding.get("evidence_from_telemetry"))
        fix = normalize_whitespace(finding.get("suggested_fix"))
        if not evidence or not fix:
            continue
        related = [
            related_rule
            for related_rule in finding.get("related_rule_ids", [])
            if related_rule in rules and related_rule != rule_id
        ]
        findings.append(make_finding(
            rules,
            rule_id,
            normalize_whitespace(finding.get("target")) or "unknown",
            evidence,
            fix,
            related=related,
            severity=normalize_level(
                finding.get("severity"),
                {"low", "medium", "high"},
                rules[rule_id]["severity_if_violated"],
            ),
            judgment=normalize_whitespace(finding.get("judgment")) or None,
            analysis_method="ai",
            confidence=normalize_level(
                finding.get("confidence"),
                {"low", "medium", "high"},
                "medium",
            ),
        ))
    findings.sort(key=lambda finding: severity_rank(finding.get("severity")))
    return {
        "session_summary": normalize_whitespace(raw_response.get("session_summary")),
        "overall_confidence": normalize_level(
            raw_response.get("overall_confidence"),
            {"low", "medium", "high"},
            "medium",
        ),
        "findings": findings,
    }


def run_ai_analysis(
    session,
    rules,
    telemetry_summary,
    heuristic_findings,
    args,
    lens_context,
    retrieved_lens_focus,
):
    api_key = os.environ.get(args.ai_api_key_env, "").strip()
    if not api_key:
        return {
            "status": "not_configured",
            "reason": "Set {} to enable AI analysis.".format(args.ai_api_key_env),
            "model": args.ai_model,
            "provider": "openai",
        }

    prompt_payload = build_ai_prompt_payload(
        session,
        rules,
        telemetry_summary,
        heuristic_findings,
        args,
        lens_context,
        retrieved_lens_focus,
    )
    request_payload = {
        "model": args.ai_model,
        "messages": build_ai_messages(prompt_payload),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        args.ai_base_url,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer {}".format(api_key),
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=args.ai_timeout_seconds) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {
            "status": "request_failed",
            "reason": "AI request failed with HTTP {}.".format(exc.code),
            "error": normalize_whitespace(error_body)[:400],
            "model": args.ai_model,
            "provider": "openai",
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "request_failed",
            "reason": "AI request failed.",
            "error": normalize_whitespace(str(exc)),
            "model": args.ai_model,
            "provider": "openai",
        }

    try:
        response_text = extract_chat_completion_text(raw_response)
        parsed_response = parse_json_object_from_text(response_text)
        normalized = normalize_ai_findings(parsed_response, rules)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "response_invalid",
            "reason": "AI response could not be parsed as the expected JSON shape.",
            "error": normalize_whitespace(str(exc)),
            "model": args.ai_model,
            "provider": "openai",
        }

    normalized.update({
        "status": "used",
        "provider": "openai",
        "model": args.ai_model,
        "sampled_event_count": prompt_payload["timeline_excerpt"]["sampled_event_count"],
        "total_event_count": prompt_payload["timeline_excerpt"]["total_event_count"],
    })
    return normalized


def merge_findings(*finding_lists):
    merged = {}
    order = []
    for finding_list in finding_lists:
        for finding in finding_list:
            key = finding_review_key(finding)
            if key not in merged:
                merged[key] = dict(finding)
                order.append(key)
                continue

            existing = merged[key]
            if severity_rank(finding.get("severity")) < severity_rank(existing.get("severity")):
                existing["severity"] = finding.get("severity")
            related = sorted(set(
                existing.get("related_rule_ids", []) + finding.get("related_rule_ids", [])
            ))
            if related:
                existing["related_rule_ids"] = related
            for field in ("judgment", "analysis_method", "confidence"):
                if not existing.get(field) and finding.get(field):
                    existing[field] = finding.get(field)
    result = [merged[key] for key in order]
    result.sort(key=lambda finding: severity_rank(finding.get("severity")))
    return result


def build_fallback_session_summary(telemetry_summary, findings):
    if telemetry_summary["event_count"] == 0:
        return "No telemetry events were captured, so there is not enough evidence to judge UX quality."
    if findings:
        top = findings[0]
        target = top.get("target") or "unknown target"
        evidence = normalize_whitespace(
            top.get("judgment") or top.get("evidence_from_telemetry")
        )
        return (
            "{} finding(s) were flagged. The clearest issue was {} on {}. {}"
        ).format(len(findings), top["rule_id"], target, evidence)
    if not telemetry_summary.get("contains_runtime_telemetry"):
        return (
            "This session mostly captured discrete UI actions and did not show enough "
            "friction to support a rubric finding."
        )
    return (
        "Runtime telemetry was present, but the analyzer did not find enough "
        "evidence to attach a rubric issue in this session."
    )


def derive_user_report_path(report_path):
    base, _ = os.path.splitext(report_path)
    return base + ".html"


def interpret_finding(finding):
    interpretations = {
        "touch_target_size": (
            "People needed repeated reach or grab attempts on this target. "
            "That usually means the target is too small, its collider does not "
            "match the visual shape, or the user is not getting enough hover "
            "feedback before committing."
        ),
        "content_type_in_elevated_context": (
            "Scrollable or primary content is being shown in an elevated/orbiter-"
            "style UI container. In XR that surface pattern is better suited to "
            "small controls, not main reading or scrolling content."
        ),
        "unmapped_issue_flag": (
            "Telemetry captured an issue the rubric does not know how to classify "
            "yet. The event still deserves review, but the rule mapping needs to "
            "be defined first."
        ),
        "interaction_latency_frustration": (
            "The user clicked the same control several times in quick succession. "
            "That usually means the first press gave no visible response, so they "
            "re-issued the input assuming it did not register — a sign of "
            "missing press feedback or a slow/unresponsive control."
        ),
        "target_discoverability": (
            "The user repeatedly tapped where there was no interactive control. "
            "That points to the real control being hard to find or hit — too "
            "small, weak affordance, or placed away from where the user is looking."
        ),
    }
    return interpretations.get(
        finding["rule_id"],
        "Telemetry suggests a usability issue here. Review the evidence and fix "
        "recommendation below to decide whether the interaction needs redesign."
    )


def render_user_report(report):
    report_json = json_for_html(report)
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>XR Session Review Dashboard</title>
  <style>
    :root {
      color-scheme: dark;

      /* Palette — Black Neomorphism */
      --deep-black: #0B0B0E;
      --charcoal: #14161A;
      --slate: #1F2126;
      --electric-cyan: #00F0FF;
      --mint-glow: #7CFFC7;
      --lavender: #C4B5FF;
      --soft-pink: #FF9ED1;
      --peach-glow: #FFC39E;
      --lilac: #E0B3FF;
      --blue-violet: #7B61FF;

      /* Roles */
      --bg: var(--charcoal);
      --surface: #1a1c21;
      --surface-raised: #1f2229;
      --ink: #eef0f5;
      --muted: #8a8f9c;
      --accent: var(--electric-cyan);

      --high: #FF9ED1;    /* soft pink */
      --medium: #FFC39E;  /* peach glow */
      --low: #7CFFC7;     /* mint glow */

      /* Neomorphic shadow pair (raised) */
      --nm-dark: rgba(0, 0, 0, 0.66);
      --nm-light: rgba(255, 255, 255, 0.035);
      --nm-raised: 7px 7px 16px var(--nm-dark), -7px -7px 16px var(--nm-light);
      --nm-raised-sm: 5px 5px 11px var(--nm-dark), -5px -5px 11px var(--nm-light);
      --nm-inset: inset 5px 5px 11px var(--nm-dark), inset -5px -5px 11px var(--nm-light);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background:
        radial-gradient(circle at 85%% -5%%, rgba(0, 240, 255, 0.08), transparent 34rem),
        radial-gradient(circle at 5%% 20%%, rgba(123, 97, 255, 0.08), transparent 34rem),
        var(--bg);
      color: var(--ink);
      line-height: 1.5;
      min-height: 100vh;
    }

    .page {
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 56px;
    }

    .hero {
      background: var(--surface);
      color: var(--ink);
      border-radius: 28px;
      padding: 30px;
      box-shadow: var(--nm-raised);
    }

    .hero h1 {
      margin: 0 0 8px;
      font-size: 34px;
      font-weight: 800;
      letter-spacing: 0.5px;
      background: linear-gradient(100deg, var(--electric-cyan), var(--lavender) 45%%, var(--soft-pink));
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .hero p {
      margin: 6px 0 0;
      max-width: 72ch;
      color: var(--muted);
    }

    .hero p strong {
      color: var(--lilac);
      font-weight: 700;
    }

    .summary-grid,
    .card-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 16px;
    }

    .summary-grid {
      margin-top: 24px;
    }

    .summary-card,
    .surface,
    .finding-card,
    .session-card,
    .mini-card,
    .modal-card {
      background: var(--surface);
      border: 1px solid rgba(255, 255, 255, 0.04);
      border-radius: 20px;
      box-shadow: var(--nm-raised);
    }

    .summary-card {
      padding: 18px;
    }

    .summary-label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .summary-value {
      font-size: 28px;
      font-weight: 800;
      margin-top: 6px;
      color: var(--electric-cyan);
    }

    .tab-bar {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 28px 0 20px;
    }

    .tab-button {
      border: 1px solid rgba(255, 255, 255, 0.04);
      background: var(--surface);
      color: var(--muted);
      border-radius: 999px;
      padding: 11px 18px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: var(--nm-raised-sm);
      transition: color 0.15s ease, box-shadow 0.15s ease;
    }

    .tab-button:hover {
      color: var(--ink);
    }

    .tab-button.active {
      color: var(--electric-cyan);
      box-shadow: var(--nm-inset);
      text-shadow: 0 0 12px rgba(0, 240, 255, 0.5);
    }

    .panel {
      display: none;
      gap: 18px;
    }

    .panel.active {
      display: grid;
    }

    .surface {
      padding: 22px;
    }

    .surface h2,
    .surface h3,
    .finding-card h3,
    .session-card h3 {
      margin: 0;
      color: var(--ink);
    }

    .section-stack {
      display: grid;
      gap: 16px;
    }

    .finding-card,
    .session-card {
      padding: 20px;
    }

    .finding-card {
      border-left: 6px solid var(--blue-violet);
    }

    .severity-high {
      border-left-color: var(--high);
      box-shadow: var(--nm-raised), -1px 0 18px rgba(255, 158, 209, 0.22);
    }

    .severity-medium {
      border-left-color: var(--medium);
      box-shadow: var(--nm-raised), -1px 0 18px rgba(255, 195, 158, 0.2);
    }

    .severity-low {
      border-left-color: var(--low);
      box-shadow: var(--nm-raised), -1px 0 18px rgba(124, 255, 199, 0.18);
    }

    .finding-header,
    .session-header,
    .card-actions,
    .modal-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
    }

    .eyebrow,
    .meta,
    .occurrence-meta {
      color: var(--muted);
      font-size: 13px;
    }

    .finding-card p strong,
    .session-card p strong,
    .mini-card strong,
    .modal-card p strong {
      color: var(--lavender);
      font-weight: 700;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.06em;
      background: var(--surface);
      color: var(--lilac);
      text-transform: uppercase;
      box-shadow: var(--nm-raised-sm);
    }

    .severity-high .pill {
      color: var(--soft-pink);
    }

    .severity-medium .pill {
      color: var(--peach-glow);
    }

    .severity-low .pill {
      color: var(--mint-glow);
    }

    .rule-text {
      color: var(--muted);
      font-style: italic;
    }

    .button-row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 16px;
    }

    .button,
    .button-subtle,
    .button-danger {
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.04);
      padding: 10px 16px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      background: var(--surface);
      box-shadow: var(--nm-raised-sm);
      transition: box-shadow 0.15s ease, color 0.15s ease;
    }

    .button:active,
    .button-subtle:active,
    .button-danger:active {
      box-shadow: var(--nm-inset);
    }

    .button {
      color: var(--electric-cyan);
    }

    .button:hover {
      text-shadow: 0 0 12px rgba(0, 240, 255, 0.5);
    }

    .button-subtle {
      color: var(--lavender);
    }

    .button-danger {
      color: var(--soft-pink);
    }

    .occurrence-list,
    .session-history,
    .info-list {
      display: grid;
      gap: 12px;
      margin-top: 14px;
    }

    .occurrence-item,
    .mini-card {
      padding: 14px 16px;
      background: var(--surface);
      border-radius: 16px;
      box-shadow: var(--nm-inset);
      border: none;
    }

    .ok,
    .empty-state {
      background: var(--surface);
      color: var(--mint-glow);
      padding: 16px 18px;
      border-radius: 18px;
      box-shadow: var(--nm-inset);
      border: 1px solid rgba(124, 255, 199, 0.12);
    }

    .empty-state {
      color: var(--muted);
      border-color: rgba(255, 255, 255, 0.04);
    }

    .two-column {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 18px;
    }

    .code-block {
      background: var(--deep-black);
      color: var(--mint-glow);
      border-radius: 16px;
      padding: 16px;
      overflow-x: auto;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
      box-shadow: var(--nm-inset);
    }

    .modal-shell {
      position: fixed;
      inset: 0;
      background: rgba(11, 11, 14, 0.72);
      backdrop-filter: blur(4px);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 20px;
      z-index: 1000;
    }

    .modal-shell.open {
      display: flex;
    }

    .modal-card {
      width: min(760px, 100%%);
      padding: 24px;
    }

    .status-note {
      color: var(--muted);
      font-size: 14px;
      margin-top: 8px;
    }

    .refresh-note {
      margin-top: 16px;
      color: var(--muted);
      font-size: 13px;
    }

    @media (max-width: 860px) {
      .page {
        padding: 20px 14px 36px;
      }

      .hero h1 {
        font-size: 28px;
      }

      .two-column {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <h1>XR Session Review Dashboard</h1>
      <p id="hero-copy"></p>
      <div class="summary-grid" id="hero-stats"></div>
      <div class="refresh-note" id="refresh-note"></div>
    </section>

    <nav class="tab-bar" aria-label="Review views">
      <button class="tab-button active" data-tab="latest">Latest Session</button>
      <button class="tab-button" data-tab="all">All Sessions</button>
      <button class="tab-button" data-tab="review">Completed</button>
    </nav>

    <section class="panel active" id="panel-latest"></section>
    <section class="panel" id="panel-all"></section>
    <section class="panel" id="panel-review"></section>
  </main>

  <div class="modal-shell" id="ignore-modal" aria-hidden="true">
    <div class="modal-card">
      <div class="finding-header">
        <div>
          <div class="eyebrow">Not Important</div>
          <h3 id="ignore-title"></h3>
        </div>
        <button class="button-subtle" id="ignore-close" type="button">Close</button>
      </div>
      <p class="status-note" id="ignore-copy"></p>
      <div class="code-block" id="ignore-command"></div>
      <div class="modal-actions" style="margin-top: 16px;">
        <button class="button-danger" id="ignore-local" type="button">Hide In Dashboard</button>
        <button class="button" id="ignore-copy-command" type="button">Copy Disable Command</button>
      </div>
    </div>
  </div>

  <script>
    const report = %s;
    const severityRank = { high: 0, medium: 1, low: 2 };
    const AUTO_REFRESH_INTERVAL_MS = 5000;
    let activeTab = "latest";
    let pendingIgnoreItem = null;

    function escapeHtml(value) {
      return String(value === undefined || value === null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function powershellQuote(value) {
      return "'" + String(value || "").replace(/'/g, "''") + "'";
    }

    function storageKey() {
      return "xr-review-state::" + (window.location.pathname || report.source || report.rubric_path || "default");
    }

    function tabStorageKey() {
      return storageKey() + "::active-tab";
    }

    function loadState() {
      try {
        const raw = window.localStorage.getItem(storageKey());
        if (!raw) {
          return { completed: {}, ignored: {} };
        }
        const parsed = JSON.parse(raw);
        return {
          completed: parsed.completed || {},
          ignored: parsed.ignored || {},
        };
      } catch (_error) {
        return { completed: {}, ignored: {} };
      }
    }

    let reviewState = loadState();

    function saveState() {
      window.localStorage.setItem(storageKey(), JSON.stringify(reviewState));
    }

    function loadActiveTab() {
      try {
        const saved = window.sessionStorage.getItem(tabStorageKey());
        if (saved === "latest" || saved === "all" || saved === "review") {
          return saved;
        }
      } catch (_error) {
        return "latest";
      }
      return "latest";
    }

    function saveActiveTab() {
      try {
        window.sessionStorage.setItem(tabStorageKey(), activeTab);
      } catch (_error) {
        // Ignore storage write failures; tab state will just reset on reload.
      }
    }

    activeTab = loadActiveTab();

    function getSessionSortValue(session, index) {
      return session.started_at_epoch_ms || index;
    }

    function getOrderedSessions() {
      return (report.sessions || [])
        .map((session, index) => Object.assign({ _sortIndex: index }, session))
        .sort((a, b) => getSessionSortValue(a, a._sortIndex) - getSessionSortValue(b, b._sortIndex));
    }

    function getLatestSession() {
      const sessions = getOrderedSessions();
      return sessions.length ? sessions[sessions.length - 1] : null;
    }

    function getOccurrences() {
      const sessions = getOrderedSessions();
      const items = [];
      sessions.forEach((session, sessionIndex) => {
        (session.findings || []).forEach((finding, findingIndex) => {
          items.push(Object.assign({}, finding, {
            session_file: session.file,
            session_id: session.session_id,
            app: session.app,
            scene: session.scene,
            telemetry_summary: session.telemetry_summary || {},
            started_at_epoch_ms: session.started_at_epoch_ms,
            ended_at_epoch_ms: session.ended_at_epoch_ms,
            sessionIndex: sessionIndex,
            findingIndex: findingIndex,
          }));
        });
      });
      return items;
    }

    function statusForKey(key) {
      if (reviewState.completed[key]) {
        return "completed";
      }
      if (reviewState.ignored[key]) {
        return "ignored";
      }
      return "open";
    }

    function compareSeverity(left, right) {
      return (severityRank[left] ?? 99) - (severityRank[right] ?? 99);
    }

    function sortFindings(items) {
      return items.slice().sort((left, right) => {
        const severityDelta = compareSeverity(left.severity, right.severity);
        if (severityDelta !== 0) {
          return severityDelta;
        }
        return left.rule_id.localeCompare(right.rule_id);
      });
    }

    function getGroupedItems() {
      const grouped = new Map();
      getOccurrences().forEach((occurrence) => {
        const key = occurrence.review_key;
        if (!grouped.has(key)) {
          grouped.set(key, {
            key: key,
            rule_id: occurrence.rule_id,
            rule_text: occurrence.rule_text,
            interpretation: occurrence.interpretation,
            target: occurrence.target,
            severity: occurrence.severity,
            suggested_fix: occurrence.suggested_fix,
            lens_id: occurrence.lens_id || "",
            lens_name: occurrence.lens_name || "",
            rule_source_type: occurrence.rule_source_type || "base",
            related_rule_ids: occurrence.related_rule_ids || [],
            occurrences: [],
          });
        }
        const entry = grouped.get(key);
        if (compareSeverity(occurrence.severity, entry.severity) < 0) {
          entry.severity = occurrence.severity;
        }
        entry.occurrences.push(occurrence);
      });

      return Array.from(grouped.values())
        .map((entry) => {
          entry.occurrences.sort((left, right) => {
            return (right.started_at_epoch_ms || right.sessionIndex) - (left.started_at_epoch_ms || left.sessionIndex);
          });
          entry.session_count = new Set(entry.occurrences.map((item) => item.session_file)).size;
          entry.latest_occurrence = entry.occurrences[0] || null;
          return entry;
        })
        .sort((left, right) => {
          const severityDelta = compareSeverity(left.severity, right.severity);
          if (severityDelta !== 0) {
            return severityDelta;
          }
          return right.occurrences.length - left.occurrences.length;
        });
    }

    function countStatuses(groupedItems) {
      const counts = { open: 0, completed: 0, ignored: 0 };
      groupedItems.forEach((item) => {
        counts[statusForKey(item.key)] += 1;
      });
      return counts;
    }

    function telemetrySummaryLine(telemetry) {
      if (!telemetry) {
        return "No telemetry summary available.";
      }
      return [
        (telemetry.event_count || 0) + " events",
        (telemetry.click_count || 0) + " clicks",
        (telemetry.rotation_change_count || 0) + " rotations",
        (telemetry.transform_change_count || 0) + " transforms",
        (telemetry.spatial_transform_count || 0) + " spatial moves",
        (telemetry.spatial_resize_count || 0) + " resizes",
        (telemetry.hand_pose_count || 0) + " hand snapshots"
      ].join(", ");
    }

    function formatSessionMeta(session) {
      return escapeHtml((session.app || "unknown app") + " / " + (session.scene || "unknown scene"));
    }

    function engineLabel(engine) {
      const mode = (engine && (engine.mode_used || engine.mode_requested) || "heuristic").toUpperCase();
      const model = engine && engine.ai_model ? " / " + engine.ai_model : "";
      if ((engine && engine.mode_used) === "heuristic") {
        return "HEURISTIC";
      }
      return mode + model;
    }

    function analysisStatusLine(engine) {
      if (!engine) {
        return "Heuristic analysis only.";
      }
      if (engine.mode_used === "heuristic") {
        if (engine.ai_status === "not_configured") {
          return "Heuristic fallback because " + (engine.reason || "AI is not configured") + ".";
        }
        if (engine.ai_status === "request_failed" || engine.ai_status === "response_invalid") {
          return "Heuristic fallback because " + (engine.reason || "the AI pass failed") + ".";
        }
        return "Heuristic analysis only.";
      }
      return "AI-assisted review using " + (engine.ai_model || "configured model") + ".";
    }

    function availableLensMap() {
      const map = new Map();
      (report.available_lenses || []).forEach((lens) => {
        map.set(lens.id, lens);
      });
      return map;
    }

    function lensDisplayNames(lensIds) {
      const lensMap = availableLensMap();
      return (lensIds || []).map((lensId) => {
        const lens = lensMap.get(lensId);
        return lens ? lens.name : lensId;
      });
    }

    function renderLensTags(lensIds) {
      const names = lensDisplayNames(lensIds);
      if (!names.length) {
        return "<span class='meta'>None</span>";
      }
      return names.map((name) => {
        return "<span class='pill'>" + escapeHtml(name) + "</span>";
      }).join(" ");
    }

    function renderLensFocusRules(focusRules) {
      if (!focusRules || !focusRules.length) {
        return "";
      }
      return "<section class='surface section-stack'>" +
        "<div><div class='eyebrow'>Specialization focus</div><h3>Retrieved Lens Heuristics</h3></div>" +
        focusRules.map((item) => {
          const matched = item.matched_terms && item.matched_terms.length
            ? "<div class='meta'>Matched terms: " + escapeHtml(item.matched_terms.join(", ")) + "</div>"
            : "";
          return "<article class='mini-card'>" +
            "<div class='finding-header'>" +
              "<div><strong>" + escapeHtml(item.lens_name + " / " + item.rule_id) + "</strong></div>" +
              "<span class='pill'>" + escapeHtml((item.detection_method || "telemetry_inferable").replace(/_/g, " ").toUpperCase()) + "</span>" +
            "</div>" +
            "<p class='rule-text'>" + escapeHtml(item.rule_text || "") + "</p>" +
            "<p><strong>Why surfaced:</strong> " + escapeHtml(item.focus_reason || "") + "</p>" +
            matched +
          "</article>";
        }).join("") +
      "</section>";
    }

    function renderLensHistory(lensContext) {
      const history = lensContext && lensContext.live_toggle_history || [];
      if (!history.length) {
        return "";
      }
      return "<div class='occurrence-list'>" + history.slice(-6).map((entry) => {
        const active = lensDisplayNames(entry.active_lenses || []).join(", ") || "none";
        return "<div class='occurrence-item'>" +
          "<div class='occurrence-meta'>t=" + escapeHtml(String(entry.t ?? "?")) + "s</div>" +
          "<div>" + escapeHtml(active) + "</div>" +
        "</div>";
      }).join("") + "</div>";
    }

    function summaryCards() {
      const groupedItems = getGroupedItems();
      const statusCounts = countStatuses(groupedItems);
      const sessions = getOrderedSessions();
      const latest = getLatestSession();
      const engine = report.analysis_engine || {};
      const cards = [
        ["Sessions", String(sessions.length)],
        ["Open Items", String(statusCounts.open)],
        ["Completed", String(statusCounts.completed)],
        ["Not Important", String(statusCounts.ignored)],
        ["Disabled Rules", String((report.disabled_rule_ids || []).length)],
        ["Lenses", String((report.available_lenses || []).length)],
        ["Engine", engineLabel(engine)],
      ];
      document.getElementById("hero-copy").innerHTML =
        "Generated " + escapeHtml(report.generated_at || "") +
        ". Latest session: <strong>" + escapeHtml(latest ? latest.file : "none") + "</strong>. " +
        "Rubric: <strong>" + escapeHtml(report.rubric || "unknown") + "</strong>. " +
        "Analysis: <strong>" + escapeHtml(analysisStatusLine(engine)) + "</strong>";
      document.getElementById("hero-stats").innerHTML = cards.map((card) => {
        return "<article class='summary-card'><div class='summary-label'>" +
          escapeHtml(card[0]) + "</div><div class='summary-value'>" +
          escapeHtml(card[1]) + "</div></article>";
      }).join("");
    }

    function shouldAutoRefresh() {
      const path = (window.location.pathname || "").toLowerCase();
      return path.endsWith("/latest_analysis.html") || path.endsWith("\\latest_analysis.html");
    }

    function setupAutoRefresh() {
      const refreshNote = document.getElementById("refresh-note");
      if (!shouldAutoRefresh()) {
        refreshNote.textContent = "Viewing a snapshot report. Re-run the watcher to regenerate this file.";
        return;
      }

      refreshNote.textContent =
        "Auto-refresh is on for latest_analysis.html. This page reloads every 5 seconds so new watcher output shows up without a manual browser refresh.";

      window.setInterval(() => {
        window.location.reload();
      }, AUTO_REFRESH_INTERVAL_MS);
    }

    function updateTabButtons() {
      document.querySelectorAll(".tab-button").forEach((button) => {
        button.classList.toggle("active", button.dataset.tab === activeTab);
      });
      document.querySelectorAll(".panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === "panel-" + activeTab);
      });
      saveActiveTab();
    }

    function renderActionButtons(item, status) {
      if (status === "completed" || status === "ignored") {
        return "<div class='button-row'>" +
          "<button class='button-subtle' type='button' data-action='restore' data-key='" + escapeHtml(item.key) + "'>Return To Open</button>" +
          "</div>";
      }
      return "<div class='button-row'>" +
        "<button class='button' type='button' data-action='complete' data-key='" + escapeHtml(item.key) + "'>Mark Completed</button>" +
        "<button class='button-danger' type='button' data-action='ignore' data-key='" + escapeHtml(item.key) + "'>Not Important</button>" +
        "</div>";
    }

    function renderGroupedCard(item, status) {
      const latestOccurrence = item.latest_occurrence;
      const lensLine = item.lens_name
        ? "<div class='eyebrow'>" + escapeHtml(item.lens_name + " lens") + "</div>"
        : "";
      const judgment = latestOccurrence && latestOccurrence.judgment
        ? "<p><strong>Researcher judgment:</strong> " + escapeHtml(latestOccurrence.judgment) + "</p>"
        : "";
      const analysisMeta = latestOccurrence && (latestOccurrence.analysis_method || latestOccurrence.confidence)
        ? "<div class='eyebrow'>" + escapeHtml([
            latestOccurrence.analysis_method ? latestOccurrence.analysis_method.toUpperCase() : "",
            latestOccurrence.confidence ? latestOccurrence.confidence.toUpperCase() + " confidence" : ""
          ].filter(Boolean).join(" | ")) + "</div>"
        : "";
      const related = item.related_rule_ids && item.related_rule_ids.length
        ? "<p><strong>Related rules:</strong> " + escapeHtml(item.related_rule_ids.join(", ")) + "</p>"
        : "";
      const occurrences = item.occurrences.slice(0, 4).map((occurrence) => {
        return "<div class='occurrence-item'>" +
          "<div class='occurrence-meta'>" + escapeHtml(occurrence.session_file) + " | " +
          escapeHtml((occurrence.app || "unknown app") + " / " + (occurrence.scene || "unknown scene")) + "</div>" +
          "<div>" + escapeHtml(occurrence.evidence_from_telemetry) + "</div>" +
        "</div>";
      }).join("");
      return "<article class='finding-card severity-" + escapeHtml(item.severity) + "'>" +
        "<div class='finding-header'>" +
          "<div>" +
            "<div class='eyebrow'>" + escapeHtml(item.session_count + " sessions | " + item.occurrences.length + " occurrences") + "</div>" +
            lensLine +
            analysisMeta +
            "<h3>" + escapeHtml(item.rule_id + " on " + (item.target || "unknown")) + "</h3>" +
          "</div>" +
          "<span class='pill'>" + escapeHtml(item.severity.toUpperCase()) + "</span>" +
        "</div>" +
        "<p class='rule-text'>" + escapeHtml(item.rule_text) + "</p>" +
        "<p><strong>What this means:</strong> " + escapeHtml(item.interpretation) + "</p>" +
        judgment +
        "<p><strong>Recommended fix:</strong> " + escapeHtml(item.suggested_fix) + "</p>" +
        "<p><strong>Latest evidence:</strong> " + escapeHtml(latestOccurrence ? latestOccurrence.evidence_from_telemetry : "No evidence available.") + "</p>" +
        related +
        renderActionButtons(item, status) +
        "<div class='occurrence-list'>" + occurrences + "</div>" +
      "</article>";
    }

    function renderLatestFindingCard(item, session) {
      const status = statusForKey(item.review_key);
      if (status !== "open") {
        return "";
      }
      const aggregatedItem = {
        key: item.review_key,
        rule_id: item.rule_id,
        rule_text: item.rule_text,
        interpretation: item.interpretation,
        target: item.target,
        severity: item.severity,
        suggested_fix: item.suggested_fix,
        related_rule_ids: item.related_rule_ids || [],
        occurrences: [item],
        session_count: 1,
        latest_occurrence: item,
      };
      return renderGroupedCard(aggregatedItem, "open");
    }

    function renderLatestPanel() {
      const panel = document.getElementById("panel-latest");
      const latest = getLatestSession();
      if (!latest) {
        panel.innerHTML = "<div class='empty-state'>No sessions were available to analyze.</div>";
        return;
      }

      const openLatestFindings = (latest.findings || []).map((finding) => {
        return renderLatestFindingCard(finding, latest);
      }).filter(Boolean);
      const hiddenCount = (latest.findings || []).length - openLatestFindings.length;
      const targets = (latest.telemetry_summary && latest.telemetry_summary.targets || []).join(", ") || "None";
      const components = (latest.telemetry_summary && latest.telemetry_summary.components || []).join(", ") || "None";
      const latestEngine = latest.analysis_engine || report.analysis_engine || {};
      const latestLensContext = latest.lens_context || {};
      const disabledRules = (report.disabled_rule_ids || []).length
        ? "<div class='mini-card'><strong>Disabled in rubric:</strong> " + escapeHtml(report.disabled_rule_ids.join(", ")) + "</div>"
        : "";
      panel.innerHTML =
        "<section class='surface section-stack'>" +
          "<div class='session-header'>" +
            "<div><div class='eyebrow'>Latest session</div><h2>" + escapeHtml(latest.file) + "</h2></div>" +
            "<div class='meta'>Session ID: " + escapeHtml(latest.session_id || "unknown") + "</div>" +
          "</div>" +
          "<div class='card-grid'>" +
            "<div class='mini-card'><strong>Context</strong><div class='meta'>" + formatSessionMeta(latest) + "</div></div>" +
            "<div class='mini-card'><strong>Telemetry</strong><div class='meta'>" + escapeHtml(telemetrySummaryLine(latest.telemetry_summary)) + "</div></div>" +
            "<div class='mini-card'><strong>Targets</strong><div class='meta'>" + escapeHtml(targets) + "</div></div>" +
            "<div class='mini-card'><strong>Components</strong><div class='meta'>" + escapeHtml(components) + "</div></div>" +
            "<div class='mini-card'><strong>Engine</strong><div class='meta'>" + escapeHtml(analysisStatusLine(latestEngine)) + "</div></div>" +
            "<div class='mini-card'><strong>Active Lenses</strong><div class='meta'>" + renderLensTags(latestLensContext.current_active_lenses || []) + "</div></div>" +
          "</div>" +
          (latest.researcher_summary
            ? "<div class='mini-card'><strong>Researcher Readout</strong><div class='meta'>" + escapeHtml(latest.researcher_summary) + "</div></div>"
            : "") +
          ((latestLensContext.live_toggle_history && latestLensContext.live_toggle_history.length)
            ? "<div class='mini-card'><strong>Live Lens Changes</strong>" + renderLensHistory(latestLensContext) + "</div>"
            : "") +
          disabledRules +
          (hiddenCount > 0 ? "<div class='status-note'>" + escapeHtml(String(hiddenCount)) + " latest-session item(s) are already in Completed or Not Important.</div>" : "") +
        "</section>" +
        renderLensFocusRules(latestLensContext.retrieved_focus_rules || []) +
        (openLatestFindings.length
          ? "<section class='section-stack'>" + openLatestFindings.join("") + "</section>"
          : "<div class='ok'>No open findings remain for the latest session.</div>");
    }

    function renderSessionHistory() {
      return getOrderedSessions().slice().reverse().map((session) => {
        const findingCount = (session.findings || []).length;
        const activeLensNames = lensDisplayNames(
          (session.lens_context && session.lens_context.current_active_lenses) || []
        ).join(", ");
        return "<article class='session-card'>" +
          "<div class='session-header'>" +
            "<div><h3>" + escapeHtml(session.file) + "</h3><div class='meta'>" + formatSessionMeta(session) + "</div></div>" +
            "<span class='pill'>" + escapeHtml(String(findingCount)) + " findings</span>" +
          "</div>" +
          "<p><strong>Telemetry:</strong> " + escapeHtml(telemetrySummaryLine(session.telemetry_summary)) + "</p>" +
          (activeLensNames
            ? "<p><strong>Lenses:</strong> " + escapeHtml(activeLensNames) + "</p>"
            : "") +
          (session.researcher_summary
            ? "<p><strong>Researcher readout:</strong> " + escapeHtml(session.researcher_summary) + "</p>"
            : "") +
        "</article>";
      }).join("");
    }

    function renderAllPanel() {
      const panel = document.getElementById("panel-all");
      const groupedItems = getGroupedItems();
      const openItems = groupedItems.filter((item) => statusForKey(item.key) === "open");
      const summaryNote = openItems.length
        ? "<div class='status-note'>" + escapeHtml(String(openItems.length)) + " open cross-session item(s) need review.</div>"
        : "<div class='ok'>No open items remain across the analyzed sessions.</div>";
      panel.innerHTML =
        "<div class='two-column'>" +
          "<section class='surface section-stack'>" +
            "<div><div class='eyebrow'>All sessions backlog</div><h2>Open Cross-Session Findings</h2></div>" +
            summaryNote +
            (openItems.length
              ? "<div class='section-stack'>" + openItems.map((item) => renderGroupedCard(item, "open")).join("") + "</div>"
              : "") +
          "</section>" +
          "<section class='surface section-stack'>" +
            "<div><div class='eyebrow'>Session history</div><h2>Analyzed Sessions</h2></div>" +
            "<div class='session-history'>" + renderSessionHistory() + "</div>" +
          "</section>" +
        "</div>";
    }

    function renderReviewSection(items, emptyText, bucket) {
      if (!items.length) {
        return "<div class='empty-state'>" + escapeHtml(emptyText) + "</div>";
      }
      return items.map((item) => {
        return renderGroupedCard(item, bucket);
      }).join("");
    }

    function renderReviewPanel() {
      const panel = document.getElementById("panel-review");
      const groupedItems = getGroupedItems();
      const completedItems = groupedItems.filter((item) => statusForKey(item.key) === "completed");
      const ignoredItems = groupedItems.filter((item) => statusForKey(item.key) === "ignored");
      panel.innerHTML =
        "<section class='surface section-stack'>" +
          "<div><div class='eyebrow'>Completed queue</div><h2>Completed</h2></div>" +
          renderReviewSection(completedItems, "No findings have been marked completed yet.", "completed") +
        "</section>" +
        "<section class='surface section-stack'>" +
          "<div><div class='eyebrow'>Muted queue</div><h2>Not Important</h2></div>" +
          "<p class='status-note'>Muted items are hidden from the open views. Use the disable command if you want future analyses to stop flagging that rule type.</p>" +
          renderReviewSection(ignoredItems, "No findings have been marked not important yet.", "ignored") +
        "</section>";
    }

    function findGroupedItem(key) {
      return getGroupedItems().find((item) => item.key === key) || null;
    }

    function openIgnoreModal(key) {
      const item = findGroupedItem(key);
      if (!item) {
        return;
      }
      pendingIgnoreItem = item;
      const command =
        "& " + powershellQuote(report.python_executable || "python") +
        " " + powershellQuote(report.rules_manager_path || "hci_for_glasses\\\\manage_rubric_rules.py") +
        " --rubric-path " + powershellQuote(report.rubric_path || report.rubric || "") +
        " --disable-rule " + item.rule_id;
      document.getElementById("ignore-title").textContent = item.rule_id + " on " + (item.target || "unknown");
      document.getElementById("ignore-copy").textContent =
        "Hide this finding in the dashboard now, or run the command below to update the referenced rubric JSON so future analyses stop flagging this rule type.";
      document.getElementById("ignore-command").textContent = command;
      document.getElementById("ignore-modal").classList.add("open");
      document.getElementById("ignore-modal").setAttribute("aria-hidden", "false");
    }

    function closeIgnoreModal() {
      pendingIgnoreItem = null;
      document.getElementById("ignore-modal").classList.remove("open");
      document.getElementById("ignore-modal").setAttribute("aria-hidden", "true");
    }

    function markCompleted(key) {
      delete reviewState.ignored[key];
      reviewState.completed[key] = new Date().toISOString();
      saveState();
      renderDashboard();
    }

    function restoreOpen(key) {
      delete reviewState.completed[key];
      delete reviewState.ignored[key];
      saveState();
      renderDashboard();
    }

    function markIgnoredLocally() {
      if (!pendingIgnoreItem) {
        return;
      }
      delete reviewState.completed[pendingIgnoreItem.key];
      reviewState.ignored[pendingIgnoreItem.key] = new Date().toISOString();
      saveState();
      closeIgnoreModal();
      renderDashboard();
    }

    async function copyDisableCommand() {
      const command = document.getElementById("ignore-command").textContent;
      try {
        await navigator.clipboard.writeText(command);
        document.getElementById("ignore-copy").textContent =
          "Disable command copied. Run it in PowerShell, then re-run the analyzer or watcher.";
      } catch (_error) {
        document.getElementById("ignore-copy").textContent =
          "Copy failed in the browser. You can still select the command manually and run it in PowerShell.";
      }
    }

    function renderDashboard() {
      summaryCards();
      updateTabButtons();
      renderLatestPanel();
      renderAllPanel();
      renderReviewPanel();
      bindDynamicButtons();
    }

    function bindDynamicButtons() {
      document.querySelectorAll("[data-action][data-key]").forEach((button) => {
        button.addEventListener("click", () => {
          const key = button.getAttribute("data-key");
          const action = button.getAttribute("data-action");
          if (action === "complete") {
            markCompleted(key);
          } else if (action === "restore") {
            restoreOpen(key);
          } else if (action === "ignore") {
            openIgnoreModal(key);
          }
        });
      });
    }

    document.querySelectorAll(".tab-button").forEach((button) => {
      button.addEventListener("click", () => {
        activeTab = button.dataset.tab;
        updateTabButtons();
      });
    });
    document.getElementById("ignore-close").addEventListener("click", closeIgnoreModal);
    document.getElementById("ignore-local").addEventListener("click", markIgnoredLocally);
    document.getElementById("ignore-copy-command").addEventListener("click", copyDisableCommand);
    document.getElementById("ignore-modal").addEventListener("click", (event) => {
      if (event.target.id === "ignore-modal") {
        closeIgnoreModal();
      }
    });

    setupAutoRefresh();
    renderDashboard();
  </script>
</body>
</html>
""" % report_json


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


def detect_repeated_clicks(session, rules):
    """
    Two friction patterns the grab-based detector misses, both derived from
    ui_interaction/controller_input click events:

      * interaction_latency_frustration: 3+ clicks on the same control in a
        rapid burst (<=RAPID_CLICK_GAP apart) with no intervening state change,
        i.e. the user re-issuing input because nothing visibly happened.
      * target_discoverability: repeated taps that landed on no control
        (source == 'background_tap'), i.e. clicking in the wrong spot.
    """
    findings = []
    events = session.get("events", [])

    clicks = [
        e for e in events
        if e.get("type") in ("ui_interaction", "controller_input")
        and e.get("action", "click") == "click"
    ]

    # --- A) rapid repeated clicks on the same control --------------------- #
    by_focus = {}
    for e in clicks:
        if e.get("source") == "background_tap":
            continue  # misses handled separately in section B
        focus = e.get("target") or e.get("component") or "unknown"
        by_focus.setdefault(focus, []).append(e)

    for focus, focus_clicks in by_focus.items():
        # Walk the ordered clicks, growing a cluster while gaps stay small.
        clusters = []
        cluster = [focus_clicks[0]]
        for prev, cur in zip(focus_clicks, focus_clicks[1:]):
            prev_t, cur_t = prev.get("t"), cur.get("t")
            if (isinstance(prev_t, (int, float))
                    and isinstance(cur_t, (int, float))
                    and (cur_t - prev_t) <= RAPID_CLICK_GAP):
                cluster.append(cur)
            else:
                clusters.append(cluster)
                cluster = [cur]
        clusters.append(cluster)

        worst = max(clusters, key=len)
        if len(worst) < MIN_RAPID_CLICKS:
            continue

        rule_id = ("interaction_latency_frustration"
                   if "interaction_latency_frustration" in rules
                   else "press_feedback_confirmation")
        if rule_id not in rules:
            continue

        gaps = [
            round(b["t"] - a["t"], 3)
            for a, b in zip(worst, worst[1:])
            if isinstance(a.get("t"), (int, float)) and isinstance(b.get("t"), (int, float))
        ]
        span = round(worst[-1]["t"] - worst[0]["t"], 2) if gaps else 0.0
        fastest = min(gaps) if gaps else 0.0
        evidence = (
            "{n} clicks on '{f}' in {span}s (gaps down to {fast}s) with no "
            "intervening state change logged; the user re-issued the same input "
            "repeatedly.".format(n=len(worst), f=focus, span=span, fast=fastest)
        )
        fix = (
            "Give '{f}' immediate press feedback (visual/audio) and confirm the "
            "action registers on the first press so users stop re-tapping; if the "
            "control is genuinely slow, show a pending/progress state.".format(f=focus)
        )
        related = [
            r for r in ("press_feedback_confirmation", "dead_control_no_response")
            if r in rules and r != rule_id
        ]
        findings.append(make_finding(
            rules, rule_id, focus, evidence, fix, related=related))

    # --- B) repeated taps that hit no control ("wrong spot") ------------- #
    if "target_discoverability" in rules:
        missed_by_region = {}
        for e in clicks:
            if e.get("source") != "background_tap":
                continue
            region = e.get("component") or "unknown"
            missed_by_region.setdefault(region, []).append(e)

        for region, misses in missed_by_region.items():
            if len(misses) < MIN_MISSED_TAPS:
                continue
            times = [e["t"] for e in misses if isinstance(e.get("t"), (int, float))]
            span = round(max(times) - min(times), 2) if len(times) >= 2 else 0.0
            evidence = (
                "{n} taps in '{r}' landed on no interactive control over {span}s; "
                "the user repeatedly clicked in the wrong spot.".format(
                    n=len(misses), r=region, span=span)
            )
            fix = (
                "Make the intended control in '{r}' easier to find and hit: grow "
                "its size/hit area to >=56dp, add a clear affordance (elevation or "
                "contrast), and place it where the user is already looking so taps "
                "land on target.".format(r=region)
            )
            related = [
                r for r in ("touch_target_size", "control_affordance_visibility")
                if r in rules
            ]
            findings.append(make_finding(
                rules, "target_discoverability", region, evidence, fix, related=related))

    return findings


DETECTORS = [detect_grab_targets, detect_ui_issue_flags, detect_repeated_clicks]


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #

def analyze_with_heuristics(session, rules):
    findings = []
    for detector in DETECTORS:
        findings.extend(detector(session, rules))
    for finding in findings:
        finding.setdefault("analysis_method", "heuristic")
    findings.sort(key=lambda finding: severity_rank(finding.get("severity")))
    return findings


def analyze_session(session, base_rules, lens_registry, telemetry_summary, args):
    mode_requested = normalize_analysis_mode(args.analysis_mode)
    requested_lens_ids = normalize_requested_lens_ids(args.lens, lens_registry)
    lens_context = extract_session_lens_context(session, requested_lens_ids, lens_registry)
    heuristic_findings = analyze_with_heuristics(session, base_rules)
    retrieved_lens_focus, retrieved_lens_rules = retrieve_lens_focus(
        session,
        telemetry_summary,
        heuristic_findings,
        lens_context,
        lens_registry,
    )
    analysis_rules = dict(base_rules)
    analysis_rules.update(retrieved_lens_rules)
    issue_flag_findings = [
        finding
        for finding in heuristic_findings
        if finding.get("rule_id") in ISSUE_FLAG_TO_RULE.values()
        or finding.get("rule_id") == "unmapped_issue_flag"
    ]

    ai_result = {
        "status": "disabled",
        "reason": "Heuristic mode was requested.",
        "provider": "openai",
        "model": args.ai_model,
    }
    findings = heuristic_findings
    mode_used = "heuristic"

    if mode_requested in {"ai", "hybrid"}:
        ai_result = run_ai_analysis(
            session,
            analysis_rules,
            telemetry_summary,
            heuristic_findings,
            args,
            lens_context,
            retrieved_lens_focus,
        )
        if ai_result.get("status") == "used":
            findings = merge_findings(ai_result.get("findings", []), issue_flag_findings)
            mode_used = mode_requested
        else:
            findings = heuristic_findings

    session_summary = (
        ai_result.get("session_summary")
        or build_fallback_session_summary(telemetry_summary, findings)
    )
    analysis_engine = {
        "mode_requested": mode_requested,
        "mode_used": mode_used,
        "ai_status": ai_result.get("status"),
        "ai_provider": ai_result.get("provider"),
        "ai_model": ai_result.get("model"),
        "active_lenses": lens_context.get("current_active_lenses", []),
        "retrieval_lenses": lens_context.get("retrieval_lenses", []),
        "retrieved_focus_rule_count": len(retrieved_lens_focus),
    }
    if ai_result.get("overall_confidence"):
        analysis_engine["overall_confidence"] = ai_result["overall_confidence"]
    if ai_result.get("reason"):
        analysis_engine["reason"] = ai_result["reason"]
    if ai_result.get("error"):
        analysis_engine["error"] = ai_result["error"]
    if ai_result.get("sampled_event_count") is not None:
        analysis_engine["sampled_event_count"] = ai_result["sampled_event_count"]
    if ai_result.get("total_event_count") is not None:
        analysis_engine["total_event_count"] = ai_result["total_event_count"]

    return findings, session_summary, analysis_engine, {
        **lens_context,
        "retrieved_focus_rules": retrieved_lens_focus,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze Android XR session telemetry JSON files."
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--input-dir",
        default=None,
        help="Directory containing session_*.json files (default: hci_for_glasses/data).",
    )
    source_group.add_argument(
        "--input-file",
        default=None,
        help="Analyze one specific session JSON file.",
    )
    parser.add_argument(
        "--report-path",
        default=REPORT_PATH,
        help="Where to write analysis_report.json (default: %(default)s).",
    )
    parser.add_argument(
        "--user-report-path",
        default=None,
        help="Optional readable HTML report path. Defaults to report path with .html.",
    )
    parser.add_argument(
        "--summary-path",
        default=SUMMARY_PATH,
        help="Optional summary.json for ground-truth checks (default: %(default)s).",
    )
    parser.add_argument(
        "--lens",
        action="append",
        default=[],
        help=(
            "Enable specialization lenses by id. Repeat the flag or pass a comma-"
            "separated list, for example --lens medical --lens low_vision."
        ),
    )
    parser.add_argument(
        "--list-lenses",
        action="store_true",
        help="Print available specialization lenses and exit.",
    )
    parser.add_argument(
        "--analysis-mode",
        default=os.environ.get("XR_ANALYSIS_MODE", DEFAULT_ANALYSIS_MODE),
        choices=("heuristic", "ai", "hybrid"),
        help=(
            "Use heuristic-only analysis, AI-only analysis, or hybrid mode "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--ai-model",
        default=DEFAULT_AI_MODEL,
        help="Model name used for the AI pass (default: %(default)s).",
    )
    parser.add_argument(
        "--ai-api-key-env",
        default=DEFAULT_AI_API_KEY_ENV,
        help="Environment variable that holds the OpenAI API key (default: %(default)s).",
    )
    parser.add_argument(
        "--ai-base-url",
        default=DEFAULT_AI_BASE_URL,
        help="Chat Completions endpoint for the AI pass (default: %(default)s).",
    )
    parser.add_argument(
        "--ai-timeout-seconds",
        type=float,
        default=DEFAULT_AI_TIMEOUT_SECONDS,
        help="HTTP timeout for the AI request (default: %(default)s).",
    )
    parser.add_argument(
        "--ai-max-events",
        type=int,
        default=DEFAULT_AI_MAX_EVENTS,
        help="Maximum number of events sampled into the AI prompt (default: %(default)s).",
    )
    parser.add_argument(
        "--ai-max-findings",
        type=int,
        default=DEFAULT_AI_MAX_FINDINGS,
        help="Maximum heuristic candidate findings sent to the AI prompt (default: %(default)s).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    lens_registry = load_lens_registry()
    if args.list_lenses:
        print("Available specialization lenses:")
        for lens in build_available_lens_summaries(lens_registry):
            print(
                "  - {id}: {name} ({count} rules)".format(
                    id=lens["id"],
                    name=lens["name"],
                    count=lens["rule_count"],
                )
            )
        return
    try:
        rubric_path = find_rubric_path()
    except FileNotFoundError as exc:
        print(str(exc))
        return

    rules, rubric_document, disabled_rule_ids = load_rubric(rubric_path)
    try:
        session_paths, source_path = resolve_session_paths(args.input_dir, args.input_file)
    except FileNotFoundError as exc:
        print(str(exc))
        return

    if not session_paths:
        print(
            "No session files found in {} - expected {}.".format(
                source_path, SESSION_GLOB)
        )
        return

    report = {
        "rubric": os.path.basename(rubric_path),
        "rubric_path": display_path(rubric_path),
        "rubric_source": rubric_document.get("source"),
        "disabled_rule_ids": disabled_rule_ids,
        "source": display_path(source_path),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "python_executable": sys.executable,
        "rules_manager_path": display_path(RULES_MANAGER_PATH),
        "available_lenses": build_available_lens_summaries(lens_registry),
        "analysis_engine": {
            "mode_requested": normalize_analysis_mode(args.analysis_mode),
            "mode_used": "heuristic",
            "ai_status": "disabled",
            "ai_provider": "openai",
            "ai_model": args.ai_model,
        },
        "sessions": []
    }
    print("=" * 72)
    print("Android XR Spatial HCI review  -  {} sessions".format(len(session_paths)))
    print("=" * 72)

    for path in session_paths:
        with open(path, encoding="utf-8") as f:
            session = json.load(f)
        telemetry_summary = summarize_telemetry(session)
        findings_raw, session_summary, analysis_engine, lens_context = analyze_session(
            session, rules, lens_registry, telemetry_summary, args
        )
        findings = [decorate_finding(finding) for finding in findings_raw]
        report["sessions"].append({
            "file": os.path.basename(path),
            "session_id": session.get("session_id"),
            "app": session.get("app"),
            "scene": session.get("scene"),
            "started_at_epoch_ms": session.get("started_at_epoch_ms"),
            "ended_at_epoch_ms": session.get("ended_at_epoch_ms"),
            "telemetry_summary": telemetry_summary,
            "researcher_summary": session_summary,
            "analysis_engine": analysis_engine,
            "lens_context": lens_context,
            "findings": findings,
        })

        print("\n{}  [{} / {}]".format(
            os.path.basename(path), session.get("app"), session.get("scene")))
        print(
            "  telemetry: {events} events, {clicks} clicks, {rotations} rotations, "
            "{transforms} transforms, {spatial} spatial transforms, {resizes} resizes, "
            "{hands} hand snapshots".format(
                events=telemetry_summary["event_count"],
                clicks=telemetry_summary["click_count"],
                rotations=telemetry_summary["rotation_change_count"],
                transforms=telemetry_summary["transform_change_count"],
                spatial=telemetry_summary["spatial_transform_count"],
                resizes=telemetry_summary["spatial_resize_count"],
                hands=telemetry_summary["hand_pose_count"],
            )
        )
        if telemetry_summary["targets"]:
            print("  targets:   {}".format(", ".join(telemetry_summary["targets"])))
        if telemetry_summary["components"]:
            print("  components: {}".format(", ".join(telemetry_summary["components"])))
        if lens_context.get("retrieval_lenses"):
            print("  lenses:    {}".format(", ".join(lens_context["retrieval_lenses"])))
        print("  analysis:  {}".format(
            analysis_engine.get("mode_used", "heuristic").upper()))
        print("  summary:   {}".format(session_summary))
        if analysis_engine.get("reason") and analysis_engine.get("mode_used") == "heuristic":
            print("  note:      {}".format(analysis_engine["reason"]))
        if lens_context.get("retrieved_focus_rules"):
            print("  lens focus:")
            for focus_rule in lens_context["retrieved_focus_rules"]:
                print(
                    "      {} / {}".format(
                        focus_rule["lens_id"],
                        focus_rule["rule_id"],
                    )
                )
        if not findings:
            print("  [OK] no rubric violations detected (clean)")
        for fnd in findings:
            print("  [{}] {}".format(fnd["severity"].upper(), fnd["rule_id"]))
            print("      evidence: {}".format(fnd["evidence_from_telemetry"]))
            print("      fix:      {}".format(fnd["suggested_fix"]))
            if fnd.get("judgment"):
                print("      judgment: {}".format(fnd["judgment"]))
            if fnd.get("related_rule_ids"):
                print("      related:  {}".format(", ".join(fnd["related_rule_ids"])))

    session_engines = [session["analysis_engine"] for session in report["sessions"]]
    if session_engines:
        used_ai = any(engine.get("mode_used") in {"ai", "hybrid"} for engine in session_engines)
        first_engine = session_engines[0]
        report["analysis_engine"]["mode_used"] = (
            normalize_analysis_mode(args.analysis_mode) if used_ai else "heuristic"
        )
        report["analysis_engine"]["ai_status"] = (
            "used" if used_ai else first_engine.get("ai_status")
        )
        if first_engine.get("reason"):
            report["analysis_engine"]["reason"] = first_engine["reason"]
        if first_engine.get("error"):
            report["analysis_engine"]["error"] = first_engine["error"]
        report["analysis_engine"]["active_lenses"] = sorted({
            lens_id
            for session in report["sessions"]
            for lens_id in session.get("lens_context", {}).get("current_active_lenses", [])
        })

    report_dir = os.path.dirname(args.report_path)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
    with open(args.report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nwrote {}".format(args.report_path))

    user_report_path = args.user_report_path or derive_user_report_path(args.report_path)
    user_report_dir = os.path.dirname(user_report_path)
    if user_report_dir:
        os.makedirs(user_report_dir, exist_ok=True)
    with open(user_report_path, "w", encoding="utf-8") as f:
        f.write(render_user_report(report))
    print("wrote {}".format(user_report_path))

    _ground_truth_check(report, args.summary_path)


def _ground_truth_check(report, summary_path):
    """If summary.json exists, cross-check detected rules vs injected issues."""
    if not os.path.exists(summary_path):
        return
    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)
    report_files = {session["file"] for session in report["sessions"]}
    summary_sessions = [
        session for session in summary.get("sessions", [])
        if session.get("file") in report_files
    ]
    if not summary_sessions:
        return

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
    for s in summary_sessions:
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
