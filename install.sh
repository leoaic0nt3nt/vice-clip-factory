#!/usr/bin/env bash
set -euo pipefail

profile="autopilot"
system_deps=1
preload_model=0
in_place=0
release="0.4.0"
while (($#)); do
  case "$1" in
    --profile) profile="${2:?Specify core or autopilot}"; shift 2 ;;
    --skip-system-deps) system_deps=0; shift ;;
    --download-model) preload_model=1; shift ;;
    --in-place) in_place=1; shift ;;
    --help|-h)
      echo "Vice Clip Factory installer (macOS/Linux)"
      echo "bash install.sh [--profile core|autopilot] [--skip-system-deps] [--download-model] [--in-place]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$profile" == "core" || "$profile" == "autopilot" ]] || { echo "Profile: core or autopilot"; exit 2; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$script_dir"
if [[ -f "$script_dir/pyproject.toml" && -d "$script_dir/src/viceclip" ]]; then
  if ((!in_place)); then
    source_dir="${VICECLIP_INSTALL_DIR:-$HOME/.local/share/viceclip/$release}"
    if [[ "$source_dir" != "$script_dir" && ! -e "$source_dir" ]]; then
      mkdir -p "$source_dir/output"
      for item in src assets scripts docs sales examples install.sh pyproject.toml LICENSE THIRD-PARTY.md README.md AGENTS.md upstream.lock.json; do
        [[ ! -e "$script_dir/$item" ]] || cp -R "$script_dir/$item" "$source_dir/"
      done
      for item in pdf demo; do
        [[ ! -d "$script_dir/output/$item" ]] || cp -R "$script_dir/output/$item" "$source_dir/output/"
      done
    fi
    [[ -f "$source_dir/pyproject.toml" && -d "$source_dir/src/viceclip" ]] || { echo "Choose an empty VICECLIP_INSTALL_DIR."; exit 1; }
  fi
else
  source_dir="${VICECLIP_INSTALL_DIR:-$HOME/.local/share/viceclip/$release}"
  command -v git >/dev/null || { echo "Install Git, then run the installer again."; exit 1; }
  if [[ -e "$source_dir" ]]; then
    [[ -f "$source_dir/pyproject.toml" && -d "$source_dir/src/viceclip" ]] || { echo "Choose an empty VICECLIP_INSTALL_DIR."; exit 1; }
  else
    mkdir -p "$(dirname -- "$source_dir")"
    git clone --depth 1 --branch "v$release" https://github.com/leoaic0nt3nt/vice-clip-factory.git "$source_dir"
  fi
fi

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  if ((system_deps)); then
    case "$(uname -s)" in
      Darwin)
        command -v brew >/dev/null || { echo "Install Homebrew from https://brew.sh and run again."; exit 1; }
        brew install ffmpeg ;;
      Linux)
        command -v apt-get >/dev/null || { echo "Install FFmpeg through your system package manager."; exit 1; }
        if [[ "$(id -u)" == 0 ]]; then apt-get update; apt-get install -y ffmpeg python3-venv; else sudo apt-get update; sudo apt-get install -y ffmpeg python3-venv; fi ;;
      *) echo "Supported systems: macOS and Linux."; exit 1 ;;
    esac
  else
    echo "FFmpeg/ffprobe missing. Install them and run again."; exit 1
  fi
fi

python_bin=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    python_bin="$candidate"; break
  fi
done
[[ -n "$python_bin" ]] || { echo "Install Python 3.12 from https://python.org and run again."; exit 1; }

echo "Vice Clip Factory · installing $profile"
if [[ ! -f "$source_dir/.venv/pyvenv.cfg" || ! -x "$source_dir/.venv/bin/python" ]]; then
  "$python_bin" -m venv "$source_dir/.venv"
fi
package="$source_dir"
[[ "$profile" == "autopilot" ]] && package="$source_dir[autopilot]"
"$source_dir/.venv/bin/python" -m pip install --disable-pip-version-check -e "$package"
if [[ "$profile" == "autopilot" ]]; then
  codex_binary="$(command -v codex || true)"
  if [[ -z "$codex_binary" && -x /Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex ]]; then
    codex_binary="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
  fi
  if [[ -n "$codex_binary" ]]; then
    codex_version="$("$codex_binary" --version 2>/dev/null || true)"
    if ! "$source_dir/.venv/bin/python" -c 'import re,sys;v=re.search(r"(\d+)\.(\d+)\.(\d+)",sys.argv[1]);sys.exit(0 if v and tuple(map(int,v.groups())) >= (0,169,0) else 1)' "$codex_version"; then
      codex_binary=""
    fi
  fi
  if [[ -z "$codex_binary" ]]; then
    command -v npm >/dev/null || { echo "Install Node.js from https://nodejs.org, then run this installer again."; exit 1; }
    npm install --prefix "$source_dir/.tools/codex" --no-audit --no-fund @openai/codex@0.169.0
    codex_binary="$source_dir/.tools/codex/node_modules/.bin/codex"
  fi
  codex_binary="$("$source_dir/.venv/bin/python" -c 'from pathlib import Path;import sys;print(Path(sys.argv[1]).resolve())' "$codex_binary")"
  [[ "$codex_binary" == "$source_dir/.venv/bin/codex" ]] || ln -sfn "$codex_binary" "$source_dir/.venv/bin/codex"
fi
if ((preload_model)) && [[ "$profile" == "autopilot" ]]; then
  "$source_dir/.venv/bin/python" -c 'from faster_whisper import WhisperModel; WhisperModel("small",device="cpu",compute_type="int8"); print("Speech model ready")'
fi
"$source_dir/.venv/bin/viceclip" doctor >/dev/null
echo
echo "Installed. Next:"
"$source_dir/.venv/bin/python" -c 'import shlex,sys;print("source "+shlex.quote(sys.argv[1]))' "$source_dir/.venv/bin/activate"
[[ "$profile" != "autopilot" ]] || echo "codex login    # once, using your Codex account"
echo "viceclip setup"
echo "viceclip autopilot start --persistent"
echo "viceclip autopilot status"
