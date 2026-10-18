#!/usr/bin/env bash
# MyMemory — double-click to install this provider into ~/.hermes/plugins/MyMemory.

cd "$(dirname "$0")"
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

echo ""
bash "./scripts/install.sh"
status=$?

echo ""
if [[ $status -eq 0 ]]; then
  echo "Install finished successfully."
else
  echo "Install failed (exit $status). See output above."
fi
echo ""
read -r -p "Press Enter to close…" _

exit $status
