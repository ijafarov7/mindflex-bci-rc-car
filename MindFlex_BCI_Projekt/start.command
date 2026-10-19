#!/bin/bash
# Doppelklick auf dem Mac startet das Programm.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Erster Start: richte Python-Umgebung ein ..."
  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt || { echo "Installation fehlgeschlagen."; read -n1; exit 1; }
fi
./.venv/bin/python main.py
