#!/bin/sh
# The kernel releases the runner lock when its owner and child exit.
exec "$HOME/.rc-clean/venv/bin/python" "$HOME/bin/rc-clean-runner.py"
