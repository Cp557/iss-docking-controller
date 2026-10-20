"""Evaluate PD docking on the original scenarios, held-out starts, and disturbances."""

import argparse
import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.dock import CONTROLLER_PATH, ROOT, run_episode
from src.evaluation.scenarios import (
    DEFAULT_SCENARIO, NEAR_DOCKING_SCENARIO,
    random_near_docking_scenario, random_scenario,
)


def scenarios(suite: str):
    if suite in {"existing", "all"}:
        yield "existing_full", DEFAULT_SCENARIO, 6000
        for seed in range(5, 17):
            yield "existing_full", random_scenario(seed), 6000
        yield "existing_near", NEAR_DOCKING_SCENARIO, 1500
        for seed in range(15, 35):
            yield "existing_near", random_near_docking_scenario(seed), 1500
    if suite in {"holdout", "all"}:
        for seed in range(35, 55):
            yield "holdout_full", random_scenario(seed), 6000
            yield "holdout_near", random_near_docking_scenario(seed), 1500
    if suite in {"stress", "all"}:
        for seed in range(100, 120):
            rng = random.Random(seed)
            scenario = replace(
                random_scenario(seed), name="disturbed",
                approach_velocity=rng.uniform(-0.04, 0.04),
                lateral_y_velocity=rng.uniform(-0.02, 0.02),
                lateral_z_velocity=rng.uniform(-0.02, 0.02),
                roll_rate=rng.uniform(-0.03, 0.03),
                pitch_rate=rng.uniform(-0.03, 0.03),
                yaw_rate=rng.uniform(-0.03, 0.03),
            )
            yield "disturbed_full", scenario, 6000


def passed(summary: dict) -> bool:
    return (
        summary["outcome"] == "SUCCESS"
        and summary["frame"] <= summary["frame_budget"]
        and summary["final_approach_frames"] is not None
        and summary["final_approach_frames"] <= 1500
        and summary["range_m"] < 0.2
        and summary["max_angle_deg"] <= 0.2
        and summary["max_velocity_m_per_frame"] <= 0.004
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("existing", "holdout", "stress", "all"), default="existing")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--clock", action="store_true", help="advance animation timers as well as physics")
    args = parser.parse_args()
    if args.max_seconds <= 0:
        parser.error("--max-seconds must be positive")
    output = args.output or ROOT / "results" / f"pd-{args.suite}-results.json"
    cases = []
    controller_hash = hashlib.sha256(CONTROLLER_PATH.read_bytes()).hexdigest()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            for group, scenario, budget in scenarios(args.suite):
                label = f"{group}_{scenario.seed if scenario.seed is not None else 'default'}"
                recording = output.parent / "episodes" / f"{label}.json"
                page = context.new_page()
                try:
                    summary = run_episode(page, scenario, recording, max_frames=budget,
                                          max_seconds=args.max_seconds, fast=not args.clock)
                    summary.update(group=group, frame_budget=budget)
                    summary["passed"] = passed(summary)
                except Exception as error:
                    # Infrastructure errors count as failures and retain their cause.
                    summary = {"group": group, "scenario": scenario.as_dict(),
                               "outcome": "ERROR", "passed": False,
                               "error": str(error)}
                    print(json.dumps(summary), flush=True)
                finally:
                    page.close()
                cases.append(summary)
                groups = sorted({case["group"] for case in cases})
                result = {
                    "controller": "pd", "suite": args.suite,
                    "guidance": "continuous", "controller_sha256": controller_hash,
                    "trials": len(cases), "passed": sum(case["passed"] for case in cases),
                    "groups": {
                        group: {"trials": sum(case["group"] == group for case in cases),
                                "passed": sum(case["group"] == group and case["passed"] for case in cases)}
                        for group in groups
                    },
                    "cases": cases,
                }
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, indent=2) + "\n")
                print(f"progress={result['passed']}/{result['trials']} passed", flush=True)
        finally:
            browser.close()
    print(f"summary={output}", flush=True)
    return 0 if result["passed"] == result["trials"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
