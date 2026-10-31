#!/usr/bin/env bash
set -euo pipefail

# Optional, user-initiated offline Whisper setup for SocialRum Lab.
readonly REPO_URL="https://github.com/ggml-org/whisper.cpp.git"
readonly COMMIT="482279b8a18855ba741d7b88c30fcb3874691f59"
readonly MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin"
readonly MODEL_SHA256="921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"

die() { printf 'setup-whisper: %s\n' "$*" >&2; exit 1; }
[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && {
  cat <<'HELP'
Install a pinned whisper.cpp CLI and the verified tiny English model under
~/.local/share/socialrum-lab/whisper for local, offline transcription.
Requires git, cmake, curl, sha256sum, and a C++ compiler. No sudo or paid API.

Optional validation inputs:
  WHISPER_CPP_SOURCE_DIR       Existing checkout at the pinned commit
  WHISPER_TINY_EN_MODEL_FILE   Existing model with the expected SHA-256
HELP
  printf '\nUsage: %s [--help]\n' "$0"
  exit 0
}
[[ $# -eq 0 ]] || die "unknown argument: $1 (try --help)"

for tool in cmake git curl sha256sum; do command -v "$tool" >/dev/null || die "required tool '$tool' is missing; install it using your OS package manager"; done
command -v c++ >/dev/null || command -v g++ >/dev/null || command -v clang++ >/dev/null || die "a C++ compiler is required"

root="${HOME:?HOME must be set}/.local/share/socialrum-lab/whisper"
[[ ! -e "$root" && ! -L "$root" ]] || die "refusing to replace existing path: $root (move it yourself before retrying)"
parent="$(dirname "$root")"
mkdir -p "$parent"
# mkdir is the no-clobber claim: it fails if another process created the path.
mkdir "$root" || die "could not claim install path: $root"
work="$(mktemp -d "${TMPDIR:-/tmp}/socialrum-whisper-setup.XXXXXX")"
cleanup() {
  local rc=$?
  rm -rf -- "$work"
  if (( rc != 0 )); then rm -rf -- "$root"; fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

source_dir="${WHISPER_CPP_SOURCE_DIR:-}"
if [[ -z "$source_dir" ]]; then
  source_dir="$work/whisper.cpp"
  git clone --quiet --no-checkout "$REPO_URL" "$source_dir"
  git -C "$source_dir" checkout --quiet --detach "$COMMIT"
  git -C "$source_dir" submodule update --init --recursive --quiet
else
  [[ -f "$source_dir/CMakeLists.txt" ]] || die "WHISPER_CPP_SOURCE_DIR is not a whisper.cpp source tree"
  actual="$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || true)"
  [[ "$actual" == "$COMMIT" ]] || die "source tree commit must be $COMMIT (got ${actual:-not a git checkout})"
fi

model_input="${WHISPER_TINY_EN_MODEL_FILE:-}"
model_tmp="$work/ggml-tiny.en.bin"
if [[ -n "$model_input" ]]; then
  [[ -f "$model_input" ]] || die "WHISPER_TINY_EN_MODEL_FILE does not exist: $model_input"
  cp -- "$model_input" "$model_tmp"
else
  curl --fail --location --retry 2 --connect-timeout 20 --max-time 900 \
    --max-filesize 100000000 --output "$model_tmp" "$MODEL_URL"
fi
actual_sha="$(sha256sum "$model_tmp" | cut -d ' ' -f1)"
[[ "$actual_sha" == "$MODEL_SHA256" ]] || die "model SHA-256 mismatch: expected $MODEL_SHA256, got $actual_sha"

build="$work/build"
jobs="${JOBS:-2}"
[[ "$jobs" =~ ^[1-4]$ ]] || die "JOBS must be an integer from 1 to 4"
cmake -S "$source_dir" -B "$build" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$root" -DWHISPER_BUILD_TESTS=OFF \
  -DWHISPER_BUILD_EXAMPLES=ON -DGGML_NATIVE=OFF -DGGML_AVX=OFF \
  -DGGML_AVX2=OFF -DGGML_AVX512=OFF -DGGML_FMA=OFF \
  -DGGML_F16C=OFF -DGGML_BMI2=OFF -DGGML_OPENMP=ON
cmake --build "$build" --parallel "$jobs"
cmake --install "$build"
mv "$root/bin/whisper-cli" "$root/bin/whisper-cli.bin"
cat > "$root/bin/whisper-cli" <<'WRAPPER'
#!/bin/sh
here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
LD_LIBRARY_PATH="$here/../lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH
exec "$here/whisper-cli.bin" "$@"
WRAPPER
chmod 0755 "$root/bin/whisper-cli"
mkdir -p "$root/models"
install -m 0644 "$model_tmp" "$root/models/ggml-tiny.en.bin"
install -m 0644 "$source_dir/LICENSE" "$root/LICENSE.whisper.cpp"
printf '%s\n' "$COMMIT" > "$root/WHISPER_CPP_COMMIT"
printf '%s\n' "$MODEL_SHA256" > "$root/MODEL_SHA256"

[[ -x "$root/bin/whisper-cli" ]] || die "build completed without expected CLI at $root/bin/whisper-cli"
printf '\nWhisper is ready for offline use.\n'
printf 'export SOCIALRUM_WHISPER_CLI=%q\n' "$root/bin/whisper-cli"
printf 'export SOCIALRUM_WHISPER_MODEL=%q\n' "$root/models/ggml-tiny.en.bin"
"$root/bin/whisper-cli" --help >/dev/null
printf 'CLI help check: OK\n'
