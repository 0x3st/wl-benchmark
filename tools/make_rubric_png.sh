#!/usr/bin/env bash
# Re-render rubric.md -> rubric.png for every essay task dir.
# Usage: bash tools/make_rubric_png.sh
set -euo pipefail
cd "$(dirname "$0")/.."

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[[ -x "$CHROME" ]] || CHROME="$(command -v chromium || command -v google-chrome)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/wrap_head.html" << 'EOF'
<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body { font-family: "PingFang SC","Hiragino Sans GB",sans-serif; font-size: 12.5px;
       line-height: 1.65; color: #24292f; width: 900px; box-sizing: border-box;
       margin: 0; padding: 26px 30px; background: #fff; }
h1 { font-size: 20px; border-bottom: 3px solid #0969da; padding-bottom: 8px; margin: 0 0 14px; }
h2 { font-size: 15px; border-bottom: 1px solid #d0d7de; padding-bottom: 5px; margin: 22px 0 10px; color: #0969da; }
h3 { font-size: 13px; margin: 16px 0 6px; }
p { margin: 7px 0; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 11.5px; }
th { background: #f0f4fa; text-align: left; }
th, td { border: 1px solid #d0d7de; padding: 5px 9px; vertical-align: top; }
tr:nth-child(even) td { background: #fafbfc; }
blockquote { margin: 10px 0; padding: 8px 14px; background: #fff8e6;
             border-left: 4px solid #d4a72c; color: #5a4a12; }
code { font-family: Menlo,monospace; font-size: 11px; background: #f0f2f5; padding: 1px 4px; border-radius: 3px; }
pre { background: #f6f8fa; padding: 10px 14px; border-radius: 6px; }
pre code { background: none; padding: 0; }
ul, ol { padding-left: 20px; margin: 7px 0; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 18px 0; }
</style></head><body>
EOF

for dir in wl_benchmark/tasks_data/essay/*/; do
  [[ -f "$dir/rubric.md" ]] || continue
  name="$(basename "$dir")"
  npx --yes marked "$dir/rubric.md" > "$WORK/body.html"
  cat "$WORK/wrap_head.html" "$WORK/body.html" > "$WORK/wrap.html"
  echo "</body></html>" >> "$WORK/wrap.html"
  "$CHROME" --headless --disable-gpu --hide-scrollbars \
    --force-device-scale-factor=1 \
    --screenshot="$dir/rubric.png" \
    --window-size=900,3400 --default-background-color=FFFFFFFF \
    "file://$WORK/wrap.html" 2>&1 | grep -o "[0-9]* bytes written" || true
  echo "rendered $name/rubric.png"
done
