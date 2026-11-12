#!/usr/bin/env bash
# Build MangaTL.app (a thin launcher for gui.py) so the window can be opened from Finder, Spotlight or the Dock.
# Usage: scripts/make_app.sh [destination_dir]   (default: the project folder; drag it to /Applications if you like)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$ROOT}"
APP="$DEST/MangaTL.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>MangaTL</string>
  <key>CFBundleDisplayName</key><string>MangaTL</string>
  <key>CFBundleIdentifier</key><string>local.mangatl.gui</string>
  <key>CFBundleExecutable</key><string>MangaTL</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/MangaTL" <<LAUNCH
#!/bin/bash
exec "$ROOT/vendor/manga-image-translator/.venv/bin/python" "$ROOT/gui.py"
LAUNCH
chmod +x "$APP/Contents/MacOS/MangaTL"
echo "Built $APP"
