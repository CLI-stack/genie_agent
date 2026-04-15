r"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         filebridge_server.py                               ║
║                    Windows-side File Bridge Server                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  PURPOSE                                                                    ║
║    Run this on your Windows machine to enable two-way file transfer         ║
║    between Windows and Linux using filebridge.sh on the Linux side.         ║
║                                                                             ║
║  FEATURES                                                                   ║
║    ✓  Browse files via browser (GET /)                                      ║
║    ✓  Download any file (GET /filename)                                     ║
║    ✓  Upload files from Linux (POST /upload)                                ║
║                                                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  HOW TO START                                                               ║
║                                                                             ║
║    # Default: port 8888, serve current directory                            ║
║    python filebridge_server.py                                              ║
║                                                                             ║
║    # Custom port                                                            ║
║    python filebridge_server.py 9000                                         ║
║                                                                             ║
║    # Custom port + custom directory                                         ║
║    python filebridge_server.py 9000 C:\Users\abinbaba\shared               ║
║                                                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  HOW TO USE FROM LINUX  (run filebridge.sh on Linux side)                  ║
║                                                                             ║
║    # List files on Windows                                                  ║
║    ./filebridge.sh list                                                     ║
║                                                                             ║
║    # Download file from Windows → Linux                                     ║
║    ./filebridge.sh download "report.pptx"                                  ║
║    ./filebridge.sh download "data.csv" /proj/mydir                         ║
║                                                                             ║
║    # Upload file from Linux → Windows                                       ║
║    ./filebridge.sh upload /tmp/results.pptx                                ║
║    ./filebridge.sh upload /tmp/results.pptx renamed_file.pptx              ║
║                                                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  NOTES                                                                      ║
║    • Replaces "python -m http.server" — adds upload support                 ║
║    • Files are saved to the directory this server is serving                ║
║    • Press Ctrl+C to stop the server                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import os
import shutil
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        status = args[1] if len(args) > 1 else "?"
        if str(status).startswith(("4", "5")):
            super().log_message(fmt, *args)
        else:
            print(f"  {self.command:6s} {self.path}  →  {status}")

    # ── GET: directory listing or file download ────────────────────────────
    def do_GET(self):
        path = urllib.parse.unquote(self.path.lstrip("/"))
        target = ROOT / path

        if target.is_dir() or path == "":
            self._send_listing(target if target.is_dir() else ROOT)
        elif target.is_file():
            self._send_file(target)
        else:
            self._send_error(404, f"Not found: {path}")

    # ── POST /upload: receive file from Linux ─────────────────────────────
    def do_POST(self):
        if self.path != "/upload":
            self._send_error(404, "POST only supported at /upload")
            return

        filename    = self.headers.get("X-Filename", "uploaded_file")
        filename    = Path(urllib.parse.unquote(filename)).name  # strip path traversal
        dest        = ROOT / filename
        length      = int(self.headers.get("Content-Length", 0))

        try:
            with open(dest, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)

            size = dest.stat().st_size
            msg  = f"Uploaded: {filename}  ({size:,} bytes)  →  {dest}"
            print(f"  [UPLOAD] {msg}")
            self._send_text(200, msg)
        except Exception as e:
            self._send_error(500, f"Upload failed: {e}")

    # ── helpers ────────────────────────────────────────────────────────────
    def _send_file(self, path: Path):
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _send_listing(self, directory: Path):
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        rows = []
        for e in entries:
            name  = urllib.parse.quote(e.name)
            icon  = "📁" if e.is_dir() else "📄"
            size  = f'{e.stat().st_size:,} B' if e.is_file() else ""
            rows.append(
                f'<tr><td><a href="/{name}">{icon} {e.name}</a></td>'
                f'<td style="color:#aaa;padding-left:20px">{size}</td></tr>'
            )
        body = f"""<!DOCTYPE HTML><html><head><meta charset="utf-8">
<style>
  body {{font-family:monospace;padding:30px;background:#1a1a2e;color:#eee}}
  h2   {{color:#60cdff}} a {{color:#60cdff;text-decoration:none}}
  a:hover {{text-decoration:underline}}
  table {{border-collapse:collapse;width:100%}}
  tr:hover {{background:#2a2a3e}}
  td {{padding:6px 4px}}
  hr {{border-color:#333}} code {{background:#2a2a3e;padding:2px 6px;border-radius:3px}}
</style></head><body>
<h2>📂 {directory}</h2><hr>
<table>{''.join(rows)}</table><hr>
<p style="color:#888">Upload from Linux:<br>
<code>./filebridge.sh upload /path/to/file</code></p>
</body></html>""".encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, msg):
        body = (msg + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code, msg):
        print(f"  [ERROR {code}] {msg}")
        self._send_text(code, f"Error {code}: {msg}")


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    print("=" * 62)
    print("  filebridge_server.py  —  Windows File Bridge Server")
    print("=" * 62)
    print(f"  URL      :  http://0.0.0.0:{PORT}/")
    print(f"  Directory:  {ROOT.resolve()}")
    print(f"  Upload   :  POST http://<this-ip>:{PORT}/upload")
    print(f"  Stop     :  Ctrl+C")
    print("=" * 62)
    print()
    HTTPServer((HOST, PORT), Handler).serve_forever()
