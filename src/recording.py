"""Save continuous PD actions and the exact simulator game-over state."""

import json
from pathlib import Path

from src.evaluation.scenarios import Scenario
from src.telemetry.state import READ_STATE


def save_episode(page, outcome: str, scenario: Scenario, output_path: Path) -> dict:
    snapshot = page.evaluate("""() => {
        const state = window.__pdController;
        return {
            trace: state.trace, result: state.result, final: state.final,
            config: state.config,
            final_approach_start_frame: state.finalApproachStartFrame,
        };
    }""")
    data = {
        "controller": "pd", "scenario": scenario.as_dict(), "outcome": outcome,
        "guidance": "continuous", "config": snapshot["config"],
        "final_approach_start_frame": snapshot["final_approach_start_frame"],
        "records": snapshot["trace"], "result": snapshot["result"],
        "final": snapshot["final"] or page.evaluate(READ_STATE),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2) + "\n")
    return data
