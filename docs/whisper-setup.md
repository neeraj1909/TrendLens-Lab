# Optional local Whisper setup

SocialRum Lab can use whisper.cpp for local English transcription. This setup is optional and user initiated: the application does not install software or download a model while it runs. After setup, transcription runs locally and works offline.

## What the setup installs

- **Engine:** [whisper.cpp](https://github.com/ggml-org/whisper.cpp), MIT licensed, pinned to commit `482279b8a18855ba741d7b88c30fcb3874691f59` (version 1.9.5).
- **Model:** `ggml-tiny.en.bin`, approximately 78 MB, SHA-256 `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f`.
- **Install directory:** `~/.local/share/socialrum-lab/whisper/`.

The script builds whisper.cpp from the pinned source using CMake and a local C++ compiler. It disables native CPU tuning and AVX variants so the result does not require AVX on the host. It requires `git`, `cmake`, `curl`, `sha256sum`, and a C++ compiler. No sudo, paid API, or hosted service is used. Setup needs internet access once to fetch source/model unless you provide already available local copies.

The script refuses to replace an existing install path. To retry after a successful installation, manually move or remove that specific directory first. On any setup failure, the script removes only the install directory it created and its temporary build directory.

## Install

From the repository root, run:

```bash
bash scripts/setup-whisper.sh
```

Then set the runtime paths in the shell where you start SocialRum Lab:

```bash
export SOCIALRUM_WHISPER_CLI="$HOME/.local/share/socialrum-lab/whisper/bin/whisper-cli"
export SOCIALRUM_WHISPER_MODEL="$HOME/.local/share/socialrum-lab/whisper/models/ggml-tiny.en.bin"
```

The app only reads these paths; it does not fetch or set up Whisper automatically. If either variable is unset or the files are absent, local transcription is unavailable.

## Use existing local proof artifacts

For maintainers validating the installer without repeating downloads, it accepts local source and model paths. The source must be a checkout at the pinned commit, and the model is still SHA-256 checked:

```bash
WHISPER_CPP_SOURCE_DIR=/path/to/whisper.cpp \
WHISPER_TINY_EN_MODEL_FILE=/path/to/ggml-tiny.en.bin \
bash scripts/setup-whisper.sh
```

These variables only change setup inputs; they do not modify the source tree or model file. The normal application has no access to them.
