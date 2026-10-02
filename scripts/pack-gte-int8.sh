#!/usr/bin/env bash
# Pack Channel 4 GTE int8 + tokenizer for GitHub Releases (never commit the tarball).
set -euo pipefail
SRC="${MYMEMORY_GTE_CACHE:-$HOME/.cache/mymemory/gte-multilingual-base-int8}"
OUT="${1:-$PWD/gte-multilingual-base-int8.tar.gz}"
if [[ ! -f "$SRC/model_int8.onnx" || ! -f "$SRC/tokenizer.json" ]]; then
  echo "error: export GTE first: python -c \"from recall.embed import export_gte_int8; export_gte_int8()\"" >&2
  exit 1
fi
tar -czf "$OUT" -C "$SRC" model_int8.onnx tokenizer.json tokenizer_config.json special_tokens_map.json
ls -lh "$OUT"
echo "Upload as GitHub Release asset gte-int8-v1 / gte-multilingual-base-int8.tar.gz"
echo "  gh release create gte-int8-v1 \"$OUT\" --title \"GTE int8 Channel 4\" --notes \"Pre-exported ONNX for install.sh\""
