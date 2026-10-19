#!/usr/bin/env bash
# Run for every initial, ordinary and resumed invocation. No global environment.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
REPORT="$ROOT/.cache/pipeline/preflight.json"
ARGS=("$@")
# Find report before any preparation so even parsing/startup failures replace it.
for ((i=0; i<${#ARGS[@]}; i++)); do
  if [[ ${ARGS[i]} == --report && $((i+1)) -lt ${#ARGS[@]} ]]; then REPORT=${ARGS[i+1]}; fi
  if [[ ${ARGS[i]} == --report=* ]]; then REPORT=${ARGS[i]#--report=}; fi
done
json_string() {
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\r'/\\r}; s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}
STAGE=initialization
ERROR=""
write_failure() {
  local status=$1 temp
  mkdir -p -- "$(dirname -- "$REPORT")" || return
  temp=$(mktemp "${REPORT}.XXXXXX") || return
  { printf '{"schema_version":1,"ok":false,"completed":false,"exit_code":%s,"stage":' "$status"
    json_string "$STAGE"
    printf ',"error":'; json_string "${ERROR:-Bootstrap failed during $STAGE (exit $status)}"
    printf ',"recorded_at":'; json_string "$(date -u +%FT%TZ)"; printf '}\n'
  } > "$temp"
  mv -- "$temp" "$REPORT"
}
on_exit() {
  local status=$?
  if (( status != 0 )); then
    # A completed preflight already includes its exact failure. An interpreter
    # startup failure leaves completed=false and must get a fresh failure record.
    if [[ $STAGE != preflight ]] || ! grep -Eq '"completed"[[:space:]]*:[[:space:]]*true' "$REPORT" 2>/dev/null; then
      write_failure "$status"
    fi
  fi
}
trap on_exit EXIT
write_failure 1
STAGE=arguments
while (( $# )); do
  case "$1" in
    --report) [[ $# -ge 2 && -n $2 && $2 != --* ]] || { ERROR="Missing value for $1"; exit 2; }; shift 2 ;;
    --report=*) [[ -n ${1#--report=} ]] || { ERROR="Missing value for --report"; exit 2; }; shift ;;
    *) ERROR="Unknown bootstrap argument: $1"; exit 2 ;;
  esac
done
STAGE=uv
command -v uv >/dev/null || { ERROR="uv is required; install uv before bootstrap"; exit 127; }
# Always target the project venv, even when called inside another active venv.
export UV_PROJECT_ENVIRONMENT="$HERE/.venv"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.cache/pipeline/uv}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$ROOT/.cache/pipeline/python}"
run_checked() {
  local log status
  log=$(mktemp "${REPORT}.stderr.XXXXXX")
  if "$@" 2>"$log"; then
    cat "$log" >&2
    rm -f -- "$log"
  else
    status=$?
    ERROR=$(cat "$log")
    printf '%s\n' "$ERROR" >&2
    rm -f -- "$log"
    return "$status"
  fi
}
export NO_COLOR=1
export UV_NO_PROGRESS=1
STAGE=locked_sync
run_checked uv sync --project "$HERE" --locked --no-dev
STAGE=preflight
run_checked uv run --project "$HERE" --locked --no-sync python "$HERE/preflight.py" "${ARGS[@]}"
