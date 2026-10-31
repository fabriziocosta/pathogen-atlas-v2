#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"

python3 - "$PROJECT_ROOT" <<'PY'
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import webbrowser

project_root = Path(sys.argv[1])
site_dir = project_root / "sites" / "pathogen-atlas-explorer" / "dist"
index_file = site_dir / "index.html"
if not index_file.is_file():
    raise SystemExit(f"Explorer page not found: {index_file}")

handler = partial(SimpleHTTPRequestHandler, directory=str(site_dir))
server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
url = f"http://127.0.0.1:{server.server_port}/"
print(f"Pathogen Atlas Explorer: {url}", flush=True)
print("Leave this window open while using the explorer. Press Ctrl+C to stop it.", flush=True)
webbrowser.open(url, new=2)

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nPathogen Atlas Explorer stopped.", flush=True)
finally:
    server.server_close()
PY
