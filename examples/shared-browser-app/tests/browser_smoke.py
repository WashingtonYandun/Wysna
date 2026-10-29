"""Exercise the shared pricing page in the runner's installed headless browser."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading


EXPECTED_RESULT = "Estimated total: $38.97"


def _tail(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return (value or "")[-4000:]


def run_headless_browser(browser, url):
    failures = []
    for attempt in range(2):
        with tempfile.TemporaryDirectory(prefix="wysna-browser-profile-") as profile:
            command = [
                browser,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=5000",
                "--dump-dom",
                url,
            ]
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=(os.name == "posix"),
                )
            except OSError as error:
                raise SystemExit(f"headless browser could not start: {error}") from error

            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                stdout, stderr = process.communicate()
                failures.append(
                    f"attempt {attempt + 1}: timed out after 30 seconds\n"
                    f"stderr:\n{_tail(stderr)}\nstdout:\n{_tail(stdout)}"
                )
            else:
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                if result.returncode == 0 and EXPECTED_RESULT in result.stdout:
                    if attempt:
                        print("[RETRY] headless browser succeeded on the second attempt")
                    return result
                failures.append(
                    f"attempt {attempt + 1}: exit code {result.returncode}\n"
                    f"stderr:\n{_tail(result.stderr)}\nstdout:\n{_tail(result.stdout)}"
                )

        if attempt == 0:
            print("[RETRY] headless browser smoke failed; retrying with a clean profile", file=sys.stderr)

    raise SystemExit("headless browser smoke failed twice:\n" + "\n".join(failures))


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
            run_headless_browser(browser, url)
        finally:
            server.shutdown()
            server_thread.join(timeout=5)

    print("[OK] shared Wysna pricing application in headless Chrome")


if __name__ == "__main__":
    main()
