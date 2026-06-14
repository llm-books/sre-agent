#!/usr/bin/env python3
"""Chaos engine for the synthetic environment.

Reads scenario files and injects faults by POSTing to each target service's
/admin/fault endpoint. The same scenario files are the eval ground truth, so the
chaos the agent sees and the chaos the harness scores against are by construction
identical.

Usage:
    chaos.py list
    chaos.py inject <scenario-name>
    chaos.py clear  <scenario-name>
    chaos.py clear-all
    chaos.py day [--speed N]

The "day" command runs the five timed chaos-day incidents on a compressed clock.
Each scenario's `at_minute` is treated as a minute offset; --speed compresses
minutes into seconds (default 60, so the whole day runs in about a minute).
"""
import argparse
import os
import sys
import time
from pathlib import Path

import requests
import yaml

SCENARIO_DIR = Path(os.environ.get("SCENARIO_DIR", "/scenarios"))
# In the compose network, each service is reachable by name on port 8080.
# Override with SERVICE_BASE to run the engine from the host against localhost.
SERVICE_BASE = os.environ.get("SERVICE_BASE", "http://{target}:8080")


def load_scenarios():
    out = {}
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        if data and "name" in data:
            out[data["name"]] = data
    return out


def service_url(target: str) -> str:
    return SERVICE_BASE.format(target=target)


def inject(scenario: dict):
    inj = scenario.get("inject")
    if not inj:
        print(f"  {scenario['name']}: no live fault (type={scenario.get('type','?')}), skipping")
        return
    target, fault = inj["target"], inj["fault"]
    url = service_url(target) + "/admin/fault"
    requests.post(url, json=fault, timeout=5).raise_for_status()
    print(f"  inject {scenario['name']} -> {target} {fault}")


def clear(scenario: dict):
    inj = scenario.get("inject")
    if not inj:
        return
    target = inj["target"]
    url = service_url(target) + "/admin/reset"
    requests.post(url, timeout=5).raise_for_status()
    print(f"  clear  {scenario['name']} -> {target}")


def clear_all(scenarios: dict):
    targets = {s["inject"]["target"] for s in scenarios.values() if s.get("inject")}
    for target in sorted(targets):
        try:
            requests.post(service_url(target) + "/admin/reset", timeout=5).raise_for_status()
            print(f"  reset {target}")
        except requests.RequestException as exc:
            print(f"  reset {target} failed: {exc}", file=sys.stderr)


def run_day(scenarios: dict, speed: float):
    timed = [s for s in scenarios.values() if "at_minute" in s]
    timed.sort(key=lambda s: s["at_minute"])
    if not timed:
        print("no timed scenarios found", file=sys.stderr)
        return

    print(f"chaos day starting, speed={speed} (1 narrative minute = {60/speed:.2f}s)")
    start = time.monotonic()

    def elapsed_minutes():
        return (time.monotonic() - start) * speed / 60.0

    pending_clear = []  # (clear_at_minute, scenario)
    for scenario in timed:
        fire_at = scenario["at_minute"]
        # wait until this scenario's fire time, clearing anything due meanwhile
        while elapsed_minutes() < fire_at:
            _drain_clears(pending_clear, elapsed_minutes())
            time.sleep(0.1)
        print(f"[{scenario.get('clock','?')}] incident: {scenario['name']} ({scenario.get('difficulty')})")
        try:
            inject(scenario)
        except requests.RequestException as exc:
            print(f"  inject failed: {exc}", file=sys.stderr)
        pending_clear.append((fire_at + scenario.get("duration_minutes", 5), scenario))

    # let the last incidents run out, then clear them
    last_clear = max(c[0] for c in pending_clear)
    while elapsed_minutes() < last_clear:
        _drain_clears(pending_clear, elapsed_minutes())
        time.sleep(0.1)
    _drain_clears(pending_clear, elapsed_minutes())
    print("chaos day complete")


def _drain_clears(pending, now_minutes):
    for entry in list(pending):
        clear_at, scenario = entry
        if now_minutes >= clear_at:
            try:
                clear(scenario)
            except requests.RequestException as exc:
                print(f"  clear failed: {exc}", file=sys.stderr)
            pending.remove(entry)


def main():
    parser = argparse.ArgumentParser(description="Synthetic environment chaos engine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_inj = sub.add_parser("inject"); p_inj.add_argument("name")
    p_clr = sub.add_parser("clear"); p_clr.add_argument("name")
    sub.add_parser("clear-all")
    p_day = sub.add_parser("day"); p_day.add_argument("--speed", type=float, default=60.0)
    args = parser.parse_args()

    scenarios = load_scenarios()

    if args.cmd == "list":
        for name, s in scenarios.items():
            print(f"{name:32} {s.get('difficulty',''):8} {s.get('clock','')}")
    elif args.cmd == "inject":
        inject(scenarios[args.name])
    elif args.cmd == "clear":
        clear(scenarios[args.name])
    elif args.cmd == "clear-all":
        clear_all(scenarios)
    elif args.cmd == "day":
        run_day(scenarios, args.speed)


if __name__ == "__main__":
    main()
