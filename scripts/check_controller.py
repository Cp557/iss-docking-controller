"""Check alignment-gate recovery during motion and explicit frame/time limits."""

import json

from playwright.sync_api import sync_playwright

from scripts.dock import ROOT, prepare_episode, run_episode
from src.evaluation.scenarios import DEFAULT_SCENARIO, NEAR_DOCKING_SCENARIO


def main() -> None:
    root = ROOT
    checks = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            for name, disturbance in [
                ("attitude_recovery", "camera.rotation.x += 5 * Math.PI / 180"),
                ("lateral_recovery", "camera.position.x += 0.5"),
            ]:
                page = context.new_page()
                prepare_episode(page, NEAR_DOCKING_SCENARIO)
                page.evaluate("""() => {
                    for (let i = 0; i < 500 && !isGameOver &&
                         camera.position.z - issObject.position.z > 3.5; i++) render();
                }""")
                before = page.evaluate("""() => ({
                    frame: window.__pdController.frames,
                    start: window.__pdController.finalApproachStartFrame,
                    allowed: window.__pdController.telemetry.approach_allowed,
                    velocity: motionVector.z,
                })""")
                assert before["allowed"] and before["velocity"] < -0.01, before
                assert before["start"] is not None
                page.evaluate(f"() => {{ {disturbance}; camera.updateMatrixWorld(true); }}")
                state = page.evaluate("""() => {
                    const state = window.__pdController;
                    let closedFrames = 0;
                    let reopened = false;
                    let invalidLimit = false;
                    let firstAllowed;
                    let slowestClosedSpeed = Infinity;
                    for (; state.frames < 1500 && !isGameOver;) {
                        render();
                        const t = state.telemetry;
                        if (firstAllowed === undefined) firstAllowed = t.approach_allowed;
                        if (!t.approach_allowed) {
                            closedFrames++;
                            invalidLimit ||= t.approach_speed_limit_m_per_frame !== 0;
                            slowestClosedSpeed = Math.min(slowestClosedSpeed, Math.abs(motionVector.z));
                        } else if (closedFrames) reopened = true;
                    }
                    return {
                        result: state.result, final: state.final,
                        final_approach_start_frame: state.finalApproachStartFrame,
                        first_allowed: firstAllowed, closed_frames: closedFrames,
                        reopened, invalid_limit: invalidLimit,
                        slowest_closed_speed: slowestClosedSpeed,
                    };
                }""")
                assert state["result"] and state["result"]["outcome"] == "SUCCESS", state
                assert state["final_approach_start_frame"] == before["start"], state
                assert not state["first_allowed"] and state["closed_frames"] > 0, state
                assert state["reopened"] and not state["invalid_limit"], state
                assert state["slowest_closed_speed"] < abs(before["velocity"]) / 2, state
                assert state["final"]["frame"] - before["start"] <= 1500, state
                checks[name] = {"before": before, **state}
                print(name + '=PASS', flush=True)
                page.close()

            for name, kwargs, expected in [
                ("frame_limit", {"max_frames": 50}, "FRAME_BUDGET"),
                ("wall_time_limit", {"max_seconds": 1e-9}, "WALL_TIME_LIMIT"),
            ]:
                page = context.new_page()
                recording = root / f"results/{name}.json"
                summary = run_episode(page, DEFAULT_SCENARIO, recording, fast=True, **kwargs)
                assert summary["outcome"] == expected, summary
                data = json.loads(recording.read_text())
                assert data["result"] is None
                if name == 'frame_limit':
                    assert summary["frame"] == len(data["records"]) == 50
                checks[name] = summary
                print(name + '=PASS', flush=True)
                page.close()
        finally:
            browser.close()

    (root / "results/controller-limit-checks.json").write_text(json.dumps(checks, indent=2) + "\n")


if __name__ == "__main__":
    main()
