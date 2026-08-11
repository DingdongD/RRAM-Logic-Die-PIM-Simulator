#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSIONS="$ROOT/third_party/versions.json"
JOBS="${JOBS:-$(command -v nproc >/dev/null 2>&1 && nproc || echo 4)}"
PYTHON="${PYTHON:-python3}"

read_json() {
  "$PYTHON" - "$VERSIONS" "$1" "$2" <<'PY'
import json, sys
p, tool, key = sys.argv[1:]
print(json.load(open(p))[tool][key])
PY
}

checkout_pinned() {
  local tool="$1"
  local dir="$2"
  local url commit
  url="$(read_json "$tool" repository)"
  commit="$(read_json "$tool" commit)"
  if [[ ! -d "$dir/.git" ]]; then
    rm -rf "$dir"
    git init "$dir"
    git -C "$dir" remote add origin "$url"
  fi
  git -C "$dir" fetch --depth 1 origin "$commit"
  git -C "$dir" checkout --detach "$commit"
  local actual
  actual="$(git -C "$dir" rev-parse HEAD)"
  [[ "$actual" == "$commit" ]] || { echo "pin mismatch for $tool: $actual != $commit" >&2; exit 1; }
}

mkdir -p "$ROOT/third_party"

if [[ "${SKIP_DESTINY:-0}" != "1" ]]; then
  checkout_pinned destiny "$ROOT/third_party/destiny"
  make -C "$ROOT/third_party/destiny" -j"$JOBS"
  [[ -x "$ROOT/third_party/destiny/destiny" ]] || { echo "DESTINY build failed" >&2; exit 1; }
fi

if [[ "${SKIP_RAMULATOR2:-0}" != "1" ]]; then
  checkout_pinned ramulator2 "$ROOT/third_party/ramulator2"
  cmake -S "$ROOT/third_party/ramulator2" -B "$ROOT/third_party/ramulator2/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$ROOT/third_party/ramulator2/build" -j"$JOBS"
  "$PYTHON" -m pip install -e "$ROOT/third_party/ramulator2"
  expected="$(read_json ramulator2 python_package_version)"
  actual="$($PYTHON - <<'PY'
from importlib.metadata import version
print(version('ramulator'))
PY
)"
  [[ "$actual" == "$expected" ]] || { echo "Ramulator package mismatch: $actual != $expected" >&2; exit 1; }
fi

echo "Pinned third-party backends are ready."
