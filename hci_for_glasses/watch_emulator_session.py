"""
Watch XR emulator telemetry sessions, mirror them locally while the emulator is
running, and analyze each session when it ends.

Typical usage from the repo root:

    C:\\Users\\Erin Mitt\\AppData\\Local\\Python\\bin\\python.exe \
        hci_for_glasses\\watch_emulator_session.py

The script attaches to the latest active session if one is already running,
otherwise waits for the next new session file. Each completed session gets its
own JSON + HTML analysis report, and the latest completed session is also copied
to stable latest_analysis.* files for convenience.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REMOTE_DIR = (
    "/sdcard/Android/data/com.example.helloandroidxr/files/xr-telemetry/sessions"
)
DEFAULT_LOCAL_DIR = os.path.join(BASE_DIR, "device_sessions", "watched")
DEFAULT_REPORT_DIR = os.path.join(BASE_DIR, "device_sessions", "reports")
DEFAULT_ANALYZER_PATH = os.path.join(BASE_DIR, "analyze_sessions.py")
NO_SUMMARY_PATH = os.path.join(BASE_DIR, "_no_summary.json")
SESSION_PREFIX = "session_"
SESSION_SUFFIX = ".json"
ALL_SESSIONS_ANALYSIS_JSON = "all_sessions_analysis.json"
ALL_SESSIONS_ANALYSIS_HTML = "all_sessions_analysis.html"
LATEST_ANALYSIS_JSON = "latest_analysis.json"
LATEST_ANALYSIS_HTML = "latest_analysis.html"
LATEST_SESSION_JSON = "latest_session.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Mirror XR emulator sessions locally and auto-analyze them when "
            "each session ends."
        )
    )
    parser.add_argument(
        "--adb-path",
        default="adb",
        help="ADB executable to use (default: %(default)s).",
    )
    parser.add_argument(
        "--serial",
        default=None,
        help="Specific adb device/emulator serial to monitor.",
    )
    parser.add_argument(
        "--remote-dir",
        default=DEFAULT_REMOTE_DIR,
        help="Remote emulator directory containing session JSON files.",
    )
    parser.add_argument(
        "--local-dir",
        default=DEFAULT_LOCAL_DIR,
        help="Local directory where mirrored session files are stored.",
    )
    parser.add_argument(
        "--report-dir",
        default=DEFAULT_REPORT_DIR,
        help="Local directory where per-session analysis reports are written.",
    )
    parser.add_argument(
        "--analyzer-path",
        default=DEFAULT_ANALYZER_PATH,
        help="Path to analyze_sessions.py.",
    )
    parser.add_argument(
        "--python-path",
        default=sys.executable,
        help="Python executable used to run analyze_sessions.py.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--open-target",
        choices=("report", "raw", "both", "none"),
        default="report",
        help="Which output file(s) to open after each completed analysis.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Exit after analyzing one session instead of continuing to watch.",
    )
    parser.add_argument(
        "--lens",
        action="append",
        default=[],
        help=(
            "Pass specialization lenses through to analyze_sessions.py. Repeat the "
            "flag or pass a comma-separated list, for example --lens medical."
        ),
    )
    return parser.parse_args()


def build_adb_command(adb_path, serial, *args):
    command = [adb_path]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    return command


def run_command(command):
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def device_is_available(adb_path, serial):
    result = run_command(build_adb_command(adb_path, serial, "get-state"))
    return result.returncode == 0 and result.stdout.strip() == "device"


def list_remote_sessions(adb_path, serial, remote_dir):
    result = run_command(build_adb_command(adb_path, serial, "shell", "ls", remote_dir))
    if result.returncode != 0:
        return []

    names = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if name.startswith(SESSION_PREFIX) and name.endswith(SESSION_SUFFIX):
            names.append(name)
    return sorted(names)


def pull_remote_session(adb_path, serial, remote_dir, filename, local_dir):
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, filename)
    remote_path = "{}/{}".format(remote_dir.rstrip("/"), filename)
    result = run_command(
        build_adb_command(adb_path, serial, "pull", remote_path, local_path)
    )
    if result.returncode != 0:
        return None
    return local_path


def load_session(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def session_has_ended(session_data):
    return bool(session_data and session_data.get("ended_at_epoch_ms"))


def build_report_path(report_dir, session_path):
    os.makedirs(report_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(session_path))[0]
    return os.path.join(report_dir, "{}_analysis.json".format(base_name))


def build_user_report_path(report_path):
    base_name, _ = os.path.splitext(report_path)
    return base_name + ".html"


def build_all_sessions_report_paths(report_dir):
    os.makedirs(report_dir, exist_ok=True)
    return (
        os.path.join(report_dir, ALL_SESSIONS_ANALYSIS_JSON),
        os.path.join(report_dir, ALL_SESSIONS_ANALYSIS_HTML),
    )


def latest_analysis_paths(report_dir, local_dir):
    return {
        "report_json": os.path.join(report_dir, LATEST_ANALYSIS_JSON),
        "report_html": os.path.join(report_dir, LATEST_ANALYSIS_HTML),
        "session_json": os.path.join(local_dir, LATEST_SESSION_JSON),
    }


def extend_analysis_command_with_lenses(command, lens_args):
    for lens_value in lens_args or []:
        command.extend(["--lens", lens_value])
    return command


def run_analysis(args, session_path, report_path, user_report_path):
    command = [
        args.python_path,
        args.analyzer_path,
        "--input-file",
        session_path,
        "--report-path",
        report_path,
        "--user-report-path",
        user_report_path,
        "--summary-path",
        NO_SUMMARY_PATH,
    ]
    extend_analysis_command_with_lenses(command, args.lens)
    result = subprocess.run(command, check=False)
    return result.returncode == 0


def run_analysis_dir(
    args,
    sessions_dir,
    report_path,
    user_report_path,
):
    command = [
        args.python_path,
        args.analyzer_path,
        "--input-dir",
        sessions_dir,
        "--report-path",
        report_path,
        "--user-report-path",
        user_report_path,
        "--summary-path",
        NO_SUMMARY_PATH,
    ]
    extend_analysis_command_with_lenses(command, args.lens)
    result = subprocess.run(command, check=False)
    return result.returncode == 0


def open_paths(open_target, session_path, dashboard_path):
    if open_target == "none":
        return

    paths = []
    if open_target in ("raw", "both"):
        paths.append(session_path)
    if open_target in ("report", "both"):
        paths.append(dashboard_path)

    if hasattr(os, "startfile"):
        for path in paths:
            os.startfile(path)  # pylint: disable=no-member


def newest_session_name(remote_sessions):
    return remote_sessions[-1] if remote_sessions else None


def latest_analyzed_session_name(report_dir):
    report_path = os.path.join(report_dir, LATEST_ANALYSIS_JSON)
    if not os.path.exists(report_path):
        return None

    report_data = load_session(report_path)
    if not report_data:
        return None

    sessions = report_data.get("sessions", [])
    if sessions and sessions[0].get("file"):
        return sessions[0]["file"]

    source = report_data.get("source")
    if source:
        return os.path.basename(source)
    return None


def inspect_remote_session(args, filename):
    local_path = pull_remote_session(
        args.adb_path,
        args.serial,
        args.remote_dir,
        filename,
        args.local_dir,
    )
    if not local_path:
        return None, None
    return local_path, load_session(local_path)


def inspect_session_batch(args, session_names):
    completed_sessions = []
    active_sessions = []

    for name in session_names:
        local_path, session_data = inspect_remote_session(args, name)
        if not session_data or not local_path:
            continue
        if session_has_ended(session_data):
            completed_sessions.append((name, local_path))
        else:
            active_sessions.append((name, local_path))

    return completed_sessions, active_sessions


def find_startup_candidates(args, remote_sessions):
    completed_sessions, active_sessions = inspect_session_batch(args, remote_sessions)
    newest_active = active_sessions[-1] if active_sessions else None
    newest_completed = completed_sessions[-1] if completed_sessions else None
    return newest_active, newest_completed


def copy_latest_aliases(args, local_session_path, report_path, dashboard_html_path):
    aliases = latest_analysis_paths(args.report_dir, args.local_dir)
    os.makedirs(args.report_dir, exist_ok=True)
    os.makedirs(args.local_dir, exist_ok=True)
    shutil.copyfile(local_session_path, aliases["session_json"])
    shutil.copyfile(report_path, aliases["report_json"])
    shutil.copyfile(dashboard_html_path, aliases["report_html"])
    return aliases


def reset_tracking_state():
    return None, None, False


def finalize_tracked_session(args, target_name, local_target_path, known_sessions, message):
    print(message.format(target_name))
    exit_code = finalize_session(args, local_target_path)
    known_sessions.add(target_name)
    return exit_code


def watch_sessions(args):
    target_name = None
    local_target_path = None
    saw_target_data = False
    known_sessions = set()
    # (session_name, event_count) last rendered live, so we only re-run the
    # analyzer for an active session when its contents actually change.
    active_signature = None

    print("Watching emulator session exports in {}".format(args.remote_dir))
    print("Mirroring files into {}".format(args.local_dir))

    if device_is_available(args.adb_path, args.serial):
        remote_sessions = list_remote_sessions(args.adb_path, args.serial, args.remote_dir)
        known_sessions.update(remote_sessions)
        last_analyzed_name = latest_analyzed_session_name(args.report_dir)
        newest_active, newest_completed = find_startup_candidates(args, remote_sessions)

        if newest_completed:
            completed_name, completed_local_path = newest_completed
            if completed_name != last_analyzed_name and completed_local_path:
                print(
                    "Analyzing newest unprocessed completed session {}".format(
                        completed_name
                    )
                )
                exit_code = finalize_session(args, completed_local_path)
                if args.once or exit_code != 0:
                    return exit_code
                print("Waiting for the next session...")

        if newest_active:
            target_name, local_target_path = newest_active
            saw_target_data = True
            print("Attached to active session {}".format(target_name))
            # Render its current contents right away instead of waiting for the
            # first change, so the dashboard is never stuck on an older session.
            session_data = load_session(local_target_path)
            if session_data and refresh_active_dashboard(args, local_target_path):
                active_signature = (target_name, len(session_data.get("events", [])))
        elif remote_sessions:
            if newest_completed and newest_completed[0] == last_analyzed_name:
                print("Newest completed session was already analyzed.")
            else:
                print(
                    "Ignoring {} existing completed session file(s).".format(
                        len(remote_sessions)
                    )
                )

    while True:
        available = device_is_available(args.adb_path, args.serial)
        if available:
            remote_sessions = list_remote_sessions(
                args.adb_path, args.serial, args.remote_dir
            )

            if target_name and target_name in remote_sessions:
                newest_name = newest_session_name(remote_sessions)
                if newest_name and newest_name != target_name and local_target_path and saw_target_data:
                    exit_code = finalize_tracked_session(
                        args,
                        target_name,
                        local_target_path,
                        known_sessions,
                        "Newer session detected; finalizing previous tracked session {}",
                    )
                    if args.once or exit_code != 0:
                        return exit_code
                    target_name, local_target_path, saw_target_data = reset_tracking_state()
                    print("Checking new sessions that appeared while the previous one was active...")
                    continue

            if target_name is None:
                new_sessions = [name for name in remote_sessions if name not in known_sessions]
                if new_sessions:
                    completed_sessions, active_sessions = inspect_session_batch(
                        args, new_sessions
                    )
                    for completed_name, completed_local_path in completed_sessions:
                        exit_code = finalize_tracked_session(
                            args,
                            completed_name,
                            completed_local_path,
                            known_sessions,
                            "Analyzing completed session {}",
                        )
                        if args.once or exit_code != 0:
                            return exit_code
                    if active_sessions:
                        target_name, local_target_path = active_sessions[-1]
                        saw_target_data = True
                        print("Tracking new active session {}".format(target_name))
                    else:
                        known_sessions.update(new_sessions)

            if target_name and target_name in remote_sessions:
                local_path = pull_remote_session(
                    args.adb_path,
                    args.serial,
                    args.remote_dir,
                    target_name,
                    args.local_dir,
                )
                if local_path:
                    local_target_path = local_path
                    session_data = load_session(local_target_path)
                    if session_data:
                        saw_target_data = True
                        if session_has_ended(session_data):
                            exit_code = finalize_tracked_session(
                                args,
                                target_name,
                                local_target_path,
                                known_sessions,
                                "Session ended; analyzing {}",
                            )
                            if args.once or exit_code != 0:
                                return exit_code
                            target_name, local_target_path, saw_target_data = reset_tracking_state()
                            active_signature = None
                            print("Waiting for the next session...")
                        else:
                            # Session still running: refresh the dashboard live so
                            # in-progress activity shows up instead of waiting for
                            # the session to end (or a newer one to supersede it).
                            signature = (target_name, len(session_data.get("events", [])))
                            if signature != active_signature and refresh_active_dashboard(
                                args, local_target_path
                            ):
                                active_signature = signature

            elif target_name and target_name not in remote_sessions:
                if local_target_path and saw_target_data:
                    exit_code = finalize_tracked_session(
                        args,
                        target_name,
                        local_target_path,
                        known_sessions,
                        "Tracked session vanished from device; analyzing last mirrored copy for {}",
                    )
                    if args.once or exit_code != 0:
                        return exit_code
                    target_name, local_target_path, saw_target_data = reset_tracking_state()
                    print("Waiting for the next session...")

        else:
            if local_target_path and saw_target_data:
                exit_code = finalize_tracked_session(
                    args,
                    target_name,
                    local_target_path,
                    known_sessions,
                    "Emulator disconnected; analyzing last mirrored copy for {}",
                )
                if args.once or exit_code != 0:
                    return exit_code
                target_name, local_target_path, saw_target_data = reset_tracking_state()
                print("Waiting for the emulator to reconnect...")

        time.sleep(args.poll_seconds)


def generate_reports(args, local_session_path):
    """
    Run the analyzer and refresh the latest_analysis.* aliases for one session.
    Returns a dict of output paths on success, or None on failure. Shared by
    both finalize (session ended) and the live refresh of an active session.
    """
    latest_report_path = build_report_path(args.report_dir, local_session_path)
    latest_user_report_path = build_user_report_path(latest_report_path)
    if not run_analysis(
        args,
        local_session_path,
        latest_report_path,
        latest_user_report_path,
    ):
        print("Analysis failed for {}".format(local_session_path))
        return None

    all_report_path, all_user_report_path = build_all_sessions_report_paths(args.report_dir)
    if not run_analysis_dir(
        args,
        args.local_dir,
        all_report_path,
        all_user_report_path,
    ):
        print("All-sessions analysis failed for {}".format(args.local_dir))
        return None

    aliases = copy_latest_aliases(
        args,
        local_session_path,
        latest_report_path,
        all_user_report_path,
    )
    return {
        "latest_report_path": latest_report_path,
        "latest_user_report_path": latest_user_report_path,
        "all_report_path": all_report_path,
        "all_user_report_path": all_user_report_path,
        "aliases": aliases,
    }


def refresh_active_dashboard(args, local_session_path):
    """
    Regenerate the dashboard for an in-progress (not yet ended) session so the
    latest_analysis.* aliases show live activity. Does not print the full
    summary or reopen the browser, and does not finalize the session.
    """
    outputs = generate_reports(args, local_session_path)
    if outputs is None:
        return False
    print("Dashboard refreshed for active session {}".format(
        os.path.basename(local_session_path)))
    return True


def finalize_session(args, local_session_path):
    outputs = generate_reports(args, local_session_path)
    if outputs is None:
        return 1

    print("Session JSON:         {}".format(local_session_path))
    print("Per-session JSON:     {}".format(outputs["latest_report_path"]))
    print("Per-session HTML:     {}".format(outputs["latest_user_report_path"]))
    print("All-sessions JSON:    {}".format(outputs["all_report_path"]))
    print("All-sessions HTML:    {}".format(outputs["all_user_report_path"]))
    print("Latest alias JSON:    {}".format(outputs["aliases"]["report_json"]))
    print("Latest dashboard HTML: {}".format(outputs["aliases"]["report_html"]))
    open_paths(args.open_target, local_session_path, outputs["aliases"]["report_html"])
    return 0


def main():
    args = parse_args()
    return watch_sessions(args)


if __name__ == "__main__":
    raise SystemExit(main())
