#!/usr/bin/env bash
# Clone and build the upstream ext-apps basic-host at a pinned commit (for A15).
set -euo pipefail
EXT_APPS_REF="${EXT_APPS_REF:-82221c0c8ce7661efa6771c9d461511b1650495f}"
DEST="${EXT_APPS_DIR:-build/ext-apps}"  # no dot-dirs: basic-host's express sendFile rejects them
if [ ! -d "$DEST/.git" ]; then
  mkdir -p "$(dirname "$DEST")"
  git clone --filter=blob:none https://github.com/modelcontextprotocol/ext-apps.git "$DEST"
fi
git -C "$DEST" fetch --depth 1 origin "$EXT_APPS_REF" 2>/dev/null || true
git -C "$DEST" checkout -q "$EXT_APPS_REF"
(cd "$DEST" && npm ci --no-audit --no-fund)
(cd "$DEST/examples/basic-host" && npm run build)
echo "basic-host built in $DEST/examples/basic-host"
