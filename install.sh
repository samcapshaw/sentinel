#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
app_dir="$HOME/.local/share/sentinel-screensaver"
mkdir -p "$app_dir" "$HOME/.local/bin"
for file in display.py sentinel.py live.py orbits.py cameras.json coastlines.json requirements.txt; do
    install -m 644 "$source_dir/$file" "$app_dir/$file"
done
python -m venv --system-site-packages "$app_dir/venv"
"$app_dir/venv/bin/python" -m pip install -r "$app_dir/requirements.txt"
cat > "$HOME/.local/bin/sentinel-screensaver" <<'WRAPPER'
#!/bin/sh
if [ "${1:-}" = "--terminal" ]; then
    shift
    exec /usr/bin/python "$HOME/.local/share/sentinel-screensaver/sentinel.py" "$@"
fi
exec "$HOME/.local/share/sentinel-screensaver/venv/bin/python" "$HOME/.local/share/sentinel-screensaver/display.py" "$@"
WRAPPER
chmod 755 "$HOME/.local/bin/sentinel-screensaver"
if command -v screensaver-manager >/dev/null; then
    manager_config="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/screensaver-manager.json"
    if [[ -f "$manager_config" ]]; then cp -p "$manager_config" "$manager_config.sentinel-backup.$(date +%s)"; fi
    existing_id=$(python - "$manager_config" <<'PYTHON'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
for item in json.loads(p.read_text()).get('custom',[]) if p.exists() else []:
    if item.get('command')=='"$HOME/.local/bin/sentinel-screensaver"':
        print('custom:'+item['id']);break
PYTHON
)
    if [[ -z "$existing_id" ]]; then existing_id=$(screensaver-manager custom add Sentinel '"$HOME/.local/bin/sentinel-screensaver"'); fi
    screensaver-manager select "$existing_id"
fi
printf 'Sentinel installed. Run sentinel-screensaver --preview to preview.\n'
