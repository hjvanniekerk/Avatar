"""
WinSCP.py — thin Python wrapper around the installed WinSCP.com scripting
binary. Runs FTP/SFTP commands using the same WinSCP engine the operator
already uses interactively, so anything that works in the GUI also works
from this script.

Why a wrapper rather than ftplib directly?
    Afrihost's pure-ftpd announces high data ports that are unreachable
    for python.exe on this network (passive AND active both time out from
    Python), but WinSCP's GUI connects fine to the same host/port. Driving
    WinSCP.com sidesteps the protocol gymnastics entirely.

Credentials are loaded from (in priority order):
    1. HM_CPANEL_HOST / HM_CPANEL_PORT / HM_CPANEL_USER / HM_CPANEL_PASSWORD /
       HM_CPANEL_REMOTE / HM_CPANEL_LOCAL / HM_CPANEL_TLS env vars
    2. config.json next to this script
    3. DEFAULT_CONFIG below
The script NEVER prompts for a password — if it can't find one it exits 2.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

WINSCP_COM_CANDIDATES = [
    r"C:\Program Files (x86)\WinSCP\WinSCP.com",
    r"C:\Program Files\WinSCP\WinSCP.com",
]
WINSCP_EXE_CANDIDATES = [
    r"C:\Program Files (x86)\WinSCP\WinSCP.exe",
    r"C:\Program Files\WinSCP\WinSCP.exe",
]


DEFAULT_CONFIG = {
    "host": "bayek.aserv.co.za",
    "port": 21,
    "username": "Home@handelsmark.co.za",
    "password": "",
    "remote_path": "public_html",
    "local_path": "./site_files",
    "use_tls": False,
}


def find_winscp() -> str:
    """Prefer WinSCP.exe /console. Reason: on this operator's machine the
    data channel is silently blocked for WinSCP.com (and python.exe) — most
    likely a per-binary AV outbound rule that was never asked to whitelist
    .com because it's never been launched interactively. WinSCP.exe was
    approved on first GUI launch and its data-channel connections succeed.
    /console makes .exe behave like .com (no GUI window, runs scripts to
    completion)."""
    for path in WINSCP_EXE_CANDIDATES:
        if Path(path).is_file():
            return path
    for path in WINSCP_COM_CANDIDATES:
        if Path(path).is_file():
            return path
    sys.stderr.write(
        "FATAL: WinSCP not found. Install from https://winscp.net/eng/download.php\n"
    )
    sys.exit(3)


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    config_file = Path(__file__).parent / "config.json"
    if config_file.exists():
        try:
            config.update(json.loads(config_file.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"FATAL: config.json is not valid JSON ({exc}).\n")
            sys.exit(2)

    env_map = {
        "host": "HM_CPANEL_HOST",
        "port": "HM_CPANEL_PORT",
        "username": "HM_CPANEL_USER",
        "password": "HM_CPANEL_PASSWORD",
        "remote_path": "HM_CPANEL_REMOTE",
        "local_path": "HM_CPANEL_LOCAL",
        "use_tls": "HM_CPANEL_TLS",
    }
    for key, env in env_map.items():
        val = os.environ.get(env, "").strip()
        if not val:
            continue
        if key == "port":
            config[key] = int(val)
        elif key == "use_tls":
            config[key] = val.lower() in ("1", "true", "yes", "on")
        else:
            config[key] = val

    missing = [k for k in ("host", "username", "password") if not config.get(k)]
    if missing:
        sys.stderr.write(
            f"FATAL: missing {', '.join(missing)} in config.json / "
            f"HM_CPANEL_* env vars. Refusing to prompt.\n"
        )
        sys.exit(2)

    return config


def _open_command(config: dict) -> str:
    """Build the WinSCP `open` line. ftps=explicit if use_tls else plain FTP.
    Username + password are URL-encoded so '@' in either field doesn't break
    WinSCP's session-URL parser."""
    scheme = "ftpes" if config.get("use_tls") else "ftp"
    user = quote(str(config["username"]), safe="")
    pwd = quote(str(config["password"]), safe="")
    return f'open {scheme}://{user}:{pwd}@{config["host"]}:{config["port"]}/ -timeout=20'


def _build_script(config: dict, commands: list[str]) -> str:
    script_lines = [
        "option batch abort",
        "option confirm off",
        # Disable auto-reconnect — pure-ftpd's 421 "Too many connections"
        # would otherwise burn another slot on each retry.
        "option reconnecttime 0",
        _open_command(config),
    ]
    if config.get("remote_path"):
        script_lines.append(f'cd "{config["remote_path"]}"')
    script_lines.extend(commands)
    script_lines.append("close")
    script_lines.append("exit")
    return "\n".join(script_lines) + "\n"


def _invoke_winscp(winscp: str, script_text: str, use_console: bool) -> tuple[int, str]:
    """Run one WinSCP attempt with given binary + /console toggle. Returns
    (returncode, output). 124 means we killed it on timeout."""
    fd, script_path = tempfile.mkstemp(prefix="winscp_", suffix=".txt", text=True)
    log_path = script_path + ".log"
    out_path = script_path + ".out"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script_text)
        argv = [winscp]
        if use_console and winscp.lower().endswith("winscp.exe"):
            argv.append("/console")
        argv.extend([
            f"/script={script_path}",
            "/loglevel=1",
            f"/log={log_path}",
        ])
        with open(out_path, "w", encoding="utf-8") as outfh:
            try:
                proc = subprocess.run(
                    argv,
                    stdout=outfh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    timeout=45,
                )
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                returncode = 124
        try:
            output = Path(out_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            output = ""
        if returncode != 0 and Path(log_path).exists():
            try:
                tail = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
                output += "\n--- WinSCP log tail ---\n" + "\n".join(tail) + "\n"
            except OSError:
                pass
        return returncode, output
    finally:
        for p in (script_path, log_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _looks_like_data_channel_timeout(out: str) -> bool:
    return (
        "Timeout detected" in out
        or "Could not retrieve directory listing" in out
        or "did not finish in" in out
    )


def run_winscp(config: dict, commands: list[str]) -> tuple[int, str]:
    """Execute a list of WinSCP scripting commands. Tries `WinSCP.exe
    /console` first (silent, our default). On data-channel timeout retries
    `WinSCP.exe` without `/console` — the GUI-mode path uses the same
    network call WinSCP's interactive sessions use, so if /console is the
    quirk that breaks the data channel, the retry recovers without the
    operator changing anything."""
    script_text = _build_script(config, commands)
    winscp = find_winscp()

    rc, out = _invoke_winscp(winscp, script_text, use_console=True)
    if rc == 0:
        return rc, out
    # Trigger retry on:
    #   - rc == 124 (we killed it on our 45s wall-clock)
    #   - log/output keywords that mean WinSCP saw the data channel die
    should_retry = (
        winscp.lower().endswith("winscp.exe")
        and (rc == 124 or _looks_like_data_channel_timeout(out))
    )
    if should_retry:
        sys.stderr.write(
            "First attempt didn't complete (rc=%d). Retrying WinSCP.exe in "
            "GUI mode (window will pop briefly)...\n" % rc
        )
        rc2, out2 = _invoke_winscp(winscp, script_text, use_console=False)
        return rc2, out + "\n--- retry without /console ---\n" + out2
    return rc, out


def preflight(config: dict) -> None:
    """TCP-greeting-only check before launching WinSCP. Pure-ftpd sends
    `421 Too many connections (8) from this IP` BEFORE the 220 banner when
    the per-IP cap is hit, so we can detect it without consuming a real
    FTP session slot — just open a TCP socket, read the greeting, close.

    Exits non-zero with a clear message on cap, unreachable host, or
    unexpected banner. Returns silently when the server is ready."""
    sock = socket.socket()
    sock.settimeout(6)
    try:
        sock.connect((config["host"], int(config["port"])))
        banner = b""
        for _ in range(20):
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            banner += chunk
            last_line = banner.splitlines()[-1] if banner.splitlines() else b""
            if last_line.startswith(b"220 ") or last_line.startswith(b"421"):
                break
    except socket.timeout:
        sys.stderr.write(
            f"FATAL: TCP timeout connecting to {config['host']}:{config['port']}. "
            "Server unreachable from this network.\n"
        )
        sys.exit(4)
    except OSError as exc:
        sys.stderr.write(f"FATAL: cannot reach {config['host']}:{config['port']} — {exc}\n")
        sys.exit(4)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    text = banner.decode("ascii", errors="replace")
    if "421" in text and "Too many connections" in text:
        # Don't exit — main() will pivot to HTTPS, which has its own
        # connection counter and isn't affected by the FTP cap. Set a flag
        # on config so the FTP attempt is skipped.
        sys.stderr.write(
            "\n>>> Afrihost 421 FTP cap hit. Skipping FTP attempt and "
            "going straight to HTTPS (HTTPS uses a separate counter).\n"
        )
        config["_ftp_cap_hit"] = True
        return
    if not text.startswith("220"):
        sys.stderr.write(f"FATAL: unexpected FTP banner: {text[:200]!r}\n")
        sys.exit(6)
    # 220 banner = server ready, slots available. Continue to WinSCP.


def _classify_error(out: str) -> str | None:
    """Return a one-line operator-friendly summary if `out` matches a known
    failure pattern, else None. Saves the operator from reading 100 lines
    of WinSCP retry noise."""
    if "Too many connections" in out or "421" in out:
        return ("Afrihost FTP cap hit (8 simultaneous sessions per IP). "
                "Close every WinSCP tab/window and any other FTP client, "
                "wait 2-5 minutes for pure-ftpd to drop the slots, then "
                "retry. Do NOT re-run in a tight loop — each attempt "
                "burns another slot.")
    if "Authentication failed" in out or "Access denied" in out:
        return "Auth rejected. Check config.json username/password."
    if "Host does not exist" in out or "could not be resolved" in out.lower():
        return "DNS / host not resolvable. Check config.json host."
    return None


def list_remote(config: dict, remote_path: str = ".") -> None:
    """List remote_path. "." means the current dir after the post-open `cd`,
    i.e. config["remote_path"]."""
    label = remote_path if remote_path != "." else config.get("remote_path", ".")
    print(f"\nContents of {label}:")
    cmd = "ls" if remote_path == "." else f'ls "{remote_path}"'
    rc, out = run_winscp(config, [cmd])
    print(out.rstrip())
    if rc != 0:
        hint = _classify_error(out)
        if hint:
            sys.stderr.write(f"\n>>> {hint}\n")
        # Don't exit here — main() inspects rc and pivots to HTTPS fallback.


def _ftp_listing_succeeded(out: str) -> bool:
    """Heuristic: a real FTP MLSD/LIST success ends with '226 Operation
    successful' or shows entries. A timeout / cap / auth fail does not."""
    if "226" in out and "Connecting to" in out and "Could not retrieve" not in out:
        return True
    if "Listing of" in out:
        return True
    if "Timeout detected" in out or "Could not retrieve directory listing" in out:
        return False
    return False


def _try_https_fallback() -> int:
    """Auto-pivot when FTP fails. Imports hm_files; if files.php is not
    deployed (HTTP 404), runs bootstrap_admin_api to deploy it; retries.
    Returns 0 on full success, non-zero with clear stderr on any blocker."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        import hm_files  # type: ignore
    except Exception as exc:
        sys.stderr.write(f"\n>>> Could not import hm_files.py: {exc}\n")
        return 50

    try:
        client = hm_files.Client.from_config()
    except SystemExit:
        sys.stderr.write(
            "\n>>> hm_files needs an admin op password.\n"
            "    Set HM_ADMIN_OP_PASSWORD env var, OR add\n"
            "    \"admin_op_password\": \"handelsmark-op\"  (or your real value)\n"
            "    to config.json. Then rerun.\n"
        )
        return 51

    def _attempt_listing() -> tuple[int, str]:
        try:
            entries = client.list("public_html")
            print(f"\n=== HTTPS listing succeeded ({len(entries)} entries) ===")
            for e in entries[:30]:
                size = e.get("size")
                size_str = "        -" if size is None else f"{size:>9}"
                print(f"  {e.get('mode',''):<4}  {size_str}  "
                      f"{e.get('type','?'):<4}  {e.get('name','')}")
            if len(entries) > 30:
                print(f"  ... ({len(entries)} total)")
            return 0, "ok"
        except hm_files.FilesAPIError as exc:
            return exc.status, exc.body

    rc, body = _attempt_listing()
    if rc == 0:
        return 0

    if rc == 404:
        print("\nfiles.php not deployed yet. Auto-running bootstrap_admin_api...")
        try:
            import bootstrap_admin_api  # type: ignore
        except Exception as exc:
            sys.stderr.write(f"\n>>> Could not import bootstrap_admin_api.py: {exc}\n")
            return 52
        boot_rc = bootstrap_admin_api.main()
        if boot_rc != 0:
            sys.stderr.write(
                "\n=== Required setup (one time) ============================\n"
                ">>> Open config.json in this folder and add ONE line with the\n"
                "    cPanel UI password (https://handelsmark.co.za:2083), e.g.:\n"
                "        \"cpanel_login_password\": \"<your cPanel password>\"\n"
                "    Or generate a revocable cPanel API token (cPanel UI ->\n"
                "    'API Tokens' -> Create) and use the token as the value.\n"
                "    Then rerun this script — no more setup ever needed.\n"
                "===========================================================\n"
            )
            return boot_rc
        # bootstrap succeeded — retry the listing
        print("\nBootstrap succeeded. Retrying HTTPS listing...")
        rc2, _ = _attempt_listing()
        return rc2

    if rc == 403:
        sys.stderr.write(
            "\n>>> HTTPS auth rejected. The admin op password is wrong.\n"
            "    Set HM_ADMIN_OP_PASSWORD env var or admin_op_password in\n"
            "    config.json to the value used in your secrets.php (or to\n"
            "    'handelsmark-op' if you've never overridden it).\n"
        )
        return 53

    sys.stderr.write(f"\n>>> HTTPS listing failed: HTTP {rc}\n{body[:300]}\n")
    return 54


def download_file(config: dict, remote_file: str, local_file: str) -> None:
    Path(local_file).parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {remote_file} -> {local_file}")
    rc, out = run_winscp(config, [f'get "{remote_file}" "{local_file}"'])
    print(out.rstrip())
    if rc != 0:
        sys.exit(rc)


def upload_file(config: dict, local_file: str, remote_file: str) -> None:
    print(f"Uploading {local_file} -> {remote_file}")
    rc, out = run_winscp(config, [f'put "{local_file}" "{remote_file}"'])
    print(out.rstrip())
    if rc != 0:
        sys.exit(rc)


def download_directory(config: dict, remote_dir: str, local_dir: str) -> None:
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    print(f"Downloading {remote_dir}/ -> {local_dir}/")
    # Trailing slash + asterisk = recursive get of all contents
    rc, out = run_winscp(config, [f'get -nopreservetime -nopermissions "{remote_dir}/*" "{local_dir}/"'])
    print(out.rstrip())
    if rc != 0:
        sys.exit(rc)


def main() -> int:
    config = load_config()

    # Cheap control-channel preflight first. Catches "421 Too many
    # connections", auth failures, and DNS issues in ~1 second so the user
    # never has to sit through a 30s WinSCP retry cascade.
    print(f"Preflight: {config['username']}@{config['host']}:{config['port']}...")
    preflight(config)
    if config.get("_ftp_cap_hit"):
        print("\n=== Pivoting to HTTPS (FTP cap hit) ===")
        return _try_https_fallback()
    print("Preflight OK.")

    winscp = find_winscp()
    print(f"Handing off to {winscp}...")

    # Try FTP listing. If the data channel times out (universal pattern on
    # this network), pivot to HTTPS via /admin-api/files.php — auto-deploy
    # via cPanel UAPI if files.php isn't there yet.
    cmd = "ls"
    print(f"\nContents of {config.get('remote_path', '.')}:  (FTP attempt)")
    rc, out = run_winscp(config, [cmd])
    print(out.rstrip())
    if rc == 0 and _ftp_listing_succeeded(out):
        return 0

    print("\n=== FTP unable to retrieve listing (data-channel blocked on this "
          "network). Pivoting to HTTPS via /admin-api/files.php ===")
    return _try_https_fallback()


if __name__ == "__main__":
    sys.exit(main())
