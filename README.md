# ISS Docking Controller

A PD controller that automatically docks in the [SpaceX ISS Docking Simulator](https://iss-sim.spacex.com/).

https://github.com/user-attachments/assets/2750ff73-fa76-40d2-a8af-d93bf3d79b5d

Default 200 m approach, shown at **1.5× speed**.

## How it works

The controller corrects position and orientation every simulation frame:

- **Proportional (P):** pushes the craft toward the docking position and orientation.
- **Derivative (D):** damps linear and angular motion to reduce overshoot.

A distance-based speed limit slows the approach near the port. An alignment check brakes forward motion while the craft is off-center or poorly oriented.

Python and **Playwright** launch Chromium, load the simulator, set starting conditions, and record results. The **JavaScript** controller runs inside the page and uses the simulator's own thruster controls and physics. Three.js and GSAP come from the simulator; FFmpeg is optional for exporting videos.

## Run

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m scripts.dock --headed
```

The default start is fixed. Add `--seed 42` for a repeatable random start, or `--near --seed 42` for a start near the port. Change the seed to try another starting state.

## Random starts and validation

The controller docked successfully from **94 distinct starting positions**:

- **53 full-distance starts:** 120–250 m along the approach axis, with varied lateral offsets and orientations.
- **41 near-port starts:** 6–10 m along the approach axis, with small position and orientation errors.
- **20 of the full-distance starts** also include initial linear and angular motion.

The suite contains two fixed starts and 92 seeded starts. Each pass must meet the simulator's docking tolerances and time/frame budgets. Separate checks cover recovery from disturbances, timeouts, and exact action replay.

```bash
.venv/bin/python -m scripts.evaluate --suite all
.venv/bin/python -m scripts.check_controller
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m scripts.replay --fast --fast-start
```

[Validation results](demo/pd-results.json) · [Recorded actions](demo/pd-recording.json) · [Control details](REFERENCE.md)

An independent browser-simulator experiment.
