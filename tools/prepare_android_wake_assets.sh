#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${JARVIS_REPO:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
PROJECT="$REPO/android/jarvis-voice-client"
AAR="$PROJECT/app/libs/sherpa-onnx-1.13.2.aar"
ASSETS="$PROJECT/app/src/main/assets/sherpa-kws-v1831"
MODEL_NAME="sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
CACHE_ROOT="${JARVIS_ANDROID_ASSET_CACHE:-$HOME/.cache/jarvis-android-wake}"
MODEL_ARCHIVE="$CACHE_ROOT/$MODEL_NAME.tar.bz2"
MODEL_ROOT="$CACHE_ROOT/model"

for cmd in curl unzip jar tar awk find; do
  command -v "$cmd" >/dev/null 2>&1 || {
    printf 'Missing required Android wake-asset command: %s\n' "$cmd" >&2
    exit 1
  }
done

mkdir -p "$PROJECT/app/libs" "$ASSETS" "$MODEL_ROOT" "$CACHE_ROOT"

if [[ ! -s "$AAR" ]]; then
  tmp_aar="$AAR.tmp.$$"
  trap 'rm -f -- "$tmp_aar"' EXIT
  if ! curl -fL --retry 4 --retry-delay 3 \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.2/sherpa-onnx-1.13.2.aar" \
    -o "$tmp_aar"; then
    curl -fL --retry 4 --retry-delay 3 \
      "https://downloads.sourceforge.net/project/sherpa-onnx.mirror/v1.13.2/sherpa-onnx-1.13.2.aar" \
      -o "$tmp_aar"
  fi
  unzip -t "$tmp_aar" >/dev/null
  mv "$tmp_aar" "$AAR"
  trap - EXIT
else
  unzip -t "$AAR" >/dev/null
fi

classes="$CACHE_ROOT/sherpa-classes.jar"
unzip -p "$AAR" classes.jar > "$classes"
jar tf "$classes" | grep -Fq 'com/k2fsa/sherpa/onnx/KeywordSpotter.class'

if [[ ! -s "$MODEL_ARCHIVE" ]]; then
  tmp_model="$MODEL_ARCHIVE.tmp.$$"
  trap 'rm -f -- "$tmp_model"' EXIT
  curl -fL --retry 4 --retry-delay 3 \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$MODEL_NAME.tar.bz2" \
    -o "$tmp_model"
  mv "$tmp_model" "$MODEL_ARCHIVE"
  trap - EXIT
fi

tar -tjf "$MODEL_ARCHIVE" >/dev/null
rm -rf -- "$MODEL_ROOT"
mkdir -p "$MODEL_ROOT"
tar -xjf "$MODEL_ARCHIVE" -C "$MODEL_ROOT"
MODEL_DIR="$(find "$MODEL_ROOT" -type d -name "$MODEL_NAME" -print -quit)"
[[ -n "$MODEL_DIR" ]] || { echo "Wake model directory was not found in archive" >&2; exit 1; }

choose_model() {
  local component="$1" selected
  selected="$(find "$MODEL_DIR" -maxdepth 1 -type f \
    -name "${component}-epoch-13-avg-2-chunk-16-left-64.int8.onnx" -print -quit)"
  if [[ -z "$selected" ]]; then
    selected="$(find "$MODEL_DIR" -maxdepth 1 -type f \
      -name "${component}-epoch-13-avg-2-chunk-16-left-64.onnx" -print -quit)"
  fi
  [[ -n "$selected" ]] || { echo "Missing $component wake model" >&2; return 1; }
  printf '%s\n' "$selected"
}

cp "$(choose_model encoder)" "$ASSETS/encoder.onnx"
cp "$(choose_model decoder)" "$ASSETS/decoder.onnx"
cp "$(choose_model joiner)" "$ASSETS/joiner.onnx"
cp "$MODEL_DIR/tokens.txt" "$ASSETS/tokens.txt"

JARVIS_PRONUNCIATION="$(awk 'toupper($1) == "JARVIS" {$1=""; sub(/^ /, ""); print; exit}' "$MODEL_DIR/en.phone")"
HEY_PRONUNCIATION="$(awk 'toupper($1) == "HEY" {$1=""; sub(/^ /, ""); print; exit}' "$MODEL_DIR/en.phone")"
[[ -n "$JARVIS_PRONUNCIATION" && -n "$HEY_PRONUNCIATION" ]] || {
  echo "Jarvis/Hey phoneme entries are missing from wake model" >&2
  exit 1
}

printf '%s :2.0 @JARVIS\n' "$JARVIS_PRONUNCIATION" > "$ASSETS/keywords-jarvis.txt"
printf '%s %s :2.2 @HEY_JARVIS\n' "$HEY_PRONUNCIATION" "$JARVIS_PRONUNCIATION" > "$ASSETS/keywords-hey-jarvis.txt"

for file in encoder.onnx decoder.onnx joiner.onnx tokens.txt keywords-jarvis.txt keywords-hey-jarvis.txt; do
  [[ -s "$ASSETS/$file" ]] || { echo "Prepared wake asset is empty: $file" >&2; exit 1; }
done

echo "Jarvis Android offline wake assets are ready."
