#!/usr/bin/env python3
"""Build the reduced compatibility cases; never execute their binaries.

Fixture sources stay unchanged. Each case gets fresh native and Thor copies,
compiler outputs, and caches below the output directory (temporary by default).
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time


HERE = Path(__file__).resolve().parent


def build(command, cwd, log, timeout):
    start = time.monotonic()
    expired = False
    with log.open("wb") as stream:
        child = subprocess.Popen(command, cwd=cwd, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            status = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            expired = True
            os.killpg(child.pid, signal.SIGKILL)
            status = child.wait()
    outcome = "timeout" if expired else "success" if status == 0 else "failure"
    return {"command": command, "returncode": status, "outcome": outcome,
            "killed_by_runner": expired,
            "seconds": round(time.monotonic() - start, 3), "log": str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thor", default=str(HERE.parents[1] / "thor"))
    parser.add_argument("--odin", default="odin")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--only", action="append", help="case name; repeatable")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--require-pass", action="store_true",
                      help="require every selected case to build (the default)")
    mode.add_argument("--recorded-outcomes", action="store_true",
                      help="check the original observations from compiler 0.20.3")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    thor, odin = shutil.which(args.thor), shutil.which(args.odin)
    if not thor or not odin:
        parser.error("both Thor and Odin must be available")
    fixtures = json.loads((HERE / "repros.json").read_text())
    if args.only:
        unknown = set(args.only) - {f["name"] for f in fixtures}
        if unknown:
            parser.error("unknown cases: " + ", ".join(sorted(unknown)))
        fixtures = [f for f in fixtures if f["name"] in args.only]
    output = (args.output_dir or Path(tempfile.mkdtemp(prefix="thor-compat-repros-"))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    print(f"Artifacts: {output}", flush=True)
    results = []
    for fixture in fixtures:
        name = fixture["name"]
        case = output / name
        if case.exists():
            parser.error(f"case output already exists; choose a fresh directory: {case}")
        row = {**fixture}
        for mode in ("odin", "thor"):
            cwd = case / mode
            cwd.mkdir(parents=True)
            source = cwd / ("main." + mode)
            source.write_bytes((HERE / "repros" / fixture["file"]).read_bytes())
            command = [odin if mode == "odin" else thor, "build", str(source)]
            if mode == "odin":
                command.append("-file")
            else:
                command += ["--output-dir", str(cwd / "generated"),
                            "--cache-dir", str(cwd / "cache")]
            command.append("-out:" + str(cwd / "program"))
            row[mode] = build(command, cwd, cwd / "build.log", args.timeout)
        results.append(row)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(f"{name}: Odin={row['odin']['outcome']}, Thor={row['thor']['outcome']} "
              f"(recorded Thor: {fixture['thor_expected']})", flush=True)
    baseline_failed = any(r["odin"]["outcome"] != "success" for r in results)
    unexpected = any(r["thor"]["outcome"] != r["thor_expected"] for r in results)
    thor_failed = any(r["thor"]["outcome"] != "success" for r in results)
    return int(baseline_failed or (unexpected if args.recorded_outcomes else thor_failed))


if __name__ == "__main__":
    raise SystemExit(main())
