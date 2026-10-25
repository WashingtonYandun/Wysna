"""Exercise the shared pricing page in the runner's installed headless browser."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import threading


EXPECTED_RESULT = "Estimated total: $38.97"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_smoke.py <staged-project-directory>")

    project_root = Path(sys.argv[1]).resolve()
    if not (project_root / "web" / "index.html").is_file():
        raise SystemExit("staged browser application is missing web/index.html")
    if not (project_root / "build" / "pricing.mjs").is_file():
        raise SystemExit("staged browser application is missing build/pricing.mjs")

    browser = None
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        browser = shutil.which(name)
        if browser:
            break
    if browser is None:
        raise SystemExit("a Chromium-based browser is required for the browser smoke")

    handler = partial(SimpleHTTPRequestHandler, directory=str(project_root))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        url = f"http://127.0.0.1:{server.server_port}/web/"
        try:
            result = subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--virtual-time-budget=5000",
                    "--dump-dom",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SystemExit(f"headless browser invocation failed: {error}") from error
        finally:
            server.shutdown()

    if result.returncode != 0:
        detail = result.stderr.strip() or "headless browser exited unsuccessfully"
        raise SystemExit(detail)
    if EXPECTED_RESULT not in result.stdout:
        raise SystemExit(
            "browser page did not render the shared Wysna result; "
            f"expected {EXPECTED_RESULT!r}\n{result.stdout}"
        )

    print("[OK] shared Wysna pricing application in headless Chrome")


if __name__ == "__main__":
    main()
