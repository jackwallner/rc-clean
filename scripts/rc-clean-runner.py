#!/usr/bin/env python3
"""Run scheduled cleanup with a process lock and a bounded runtime."""

import argparse
import datetime
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def log(message: str, output) -> None:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== {stamp} {message} ===", file=output, flush=True)


def run(script: Path, state: Path, log_path: Path, timeout: float) -> int:
    state.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with (state / "run.flock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("rc-clean skipped: another run is active", flush=True)
            return 0
        # Rotate only after locking, so overlapping launches cannot lose logs.
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            log_path.replace(log_path.with_name(log_path.name + ".1"))
        full_stamp = state / "last-full-sweep"
        full = not full_stamp.exists() or time.time() - full_stamp.stat().st_mtime > 72000
        command = [sys.executable, str(script), "--all-apps", "--delete", "--jobs", "4"]
        if not full:
            command.extend(["--since-days", "3"])
        with log_path.open("a", buffering=1) as output:
            log("start full sweep" if full else "start --since-days 3", output)
            process = None
            status = 1
            try:
                process = subprocess.Popen(
                    command, cwd="/tmp", stdout=output, stderr=output,
                    start_new_session=True, pass_fds=(lock.fileno(),))
                try:
                    status = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    log(f"timeout after {timeout:g}s", output)
                    status = 124
            except (OSError, KeyboardInterrupt) as error:
                log(f"runner failed: {error}", output)
            finally:
                if process is not None and process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            if status == 0:
                (state / "last-success").touch()
                if full:
                    full_stamp.touch()
            log(f"exit {status}", output)
            return status if status >= 0 else 128 - status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, default=Path.home() / "bin/rc-clean-test-users")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".rc-clean")
    parser.add_argument("--log", type=Path, default=Path.home() / "Library/Logs/rc-clean.log")
    parser.add_argument("--timeout", type=float, default=10800)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    return run(args.script.resolve(), args.state_dir, args.log, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
