"""
hm_files.py — drop-in replacement for WinSCP.py that uses the HTTPS-based
/admin-api/files.php endpoint instead of FTP. No 8-connection cap, no
data-channel timeouts, no firewall games — just one TLS request per op.

Auth: HM_ADMIN_OP_PASSWORD env var (or 'admin_op_password' in
config.json). Same value the rest of the admin-api ecosystem uses.

CLI:
    python hm_files.py ping
    python hm_files.py ls [path]
    python hm_files.py stat <path>
    python hm_files.py get <remote_path> <local_path>
    python hm_files.py put <local_path> <remote_path>
    python hm_files.py rm <path>
    python hm_files.py mkdir <path>
    python hm_files.py mv <from> <to>

Default remote root segment is `public_html`. Allowed roots (mirrors the
PHP whitelist): public_html, tmp, logs, dkim-private.pem, oracle-config.json,
secrets.php, reports.

Programmatic API:
    from hm_files import Client
    c = Client.from_config()
    c.put('public_html/foo.txt', b'hello')
    print(c.list('public_html')[:5])
"""

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "https://www.aieng.co.za/admin-api/files.php"  # handelsmark.co.za retiring; HM_FILES_BASE_URL env still honored
DEFAULT_REMOTE_ROOT = "public_html"
DEFAULT_TIMEOUT_S = 60


def _load_config() -> dict:
    config = {
        "base_url": DEFAULT_BASE_URL,
        "admin_op_password": "",
        "verify_tls": True,
        "timeout": DEFAULT_TIMEOUT_S,
    }
    cfg_path = Path(__file__).parent / "config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"FATAL: config.json invalid JSON ({exc}).\n")
            sys.exit(2)
        for key in config:
            if key in data:
                config[key] = data[key]

    env_map = {
        "base_url": "HM_FILES_BASE_URL",
        "admin_op_password": "HM_ADMIN_OP_PASSWORD",
        "timeout": "HM_FILES_TIMEOUT",
    }
    for key, env in env_map.items():
        val = os.environ.get(env, "").strip()
        if not val:
            continue
        if key == "timeout":
            config[key] = int(val)
        else:
            config[key] = val

    if not config["admin_op_password"]:
        sys.stderr.write(
            "FATAL: no admin op password. Set HM_ADMIN_OP_PASSWORD env var "
            "or add 'admin_op_password' key to config.json.\n"
        )
        sys.exit(2)
    return config


class FilesAPIError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class Client:
    def __init__(self, base_url: str, op_password: str, *,
                 verify_tls: bool = True, timeout: int = DEFAULT_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        self.op_password = op_password
        self.timeout = timeout
        if verify_tls:
            self.ssl_ctx = ssl.create_default_context()
        else:
            self.ssl_ctx = ssl.create_default_context()
            self.ssl_ctx.check_hostname = False
            self.ssl_ctx.verify_mode = ssl.CERT_NONE

    @classmethod
    def from_config(cls) -> "Client":
        cfg = _load_config()
        return cls(
            cfg["base_url"],
            cfg["admin_op_password"],
            verify_tls=bool(cfg.get("verify_tls", True)),
            timeout=int(cfg.get("timeout", DEFAULT_TIMEOUT_S)),
        )

    def _build_url(self, action: str, **params) -> str:
        params["action"] = action
        return f"{self.base_url}?{urllib.parse.urlencode(params)}"

    def _request(self, url: str, *, method: str = "GET",
                 body: bytes | None = None,
                 extra_headers: dict[str, str] | None = None,
                 raw: bool = False):
        headers = {
            "X-HM-Op-Password": self.op_password,
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_ctx)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise FilesAPIError(exc.code, text) from None
        if raw:
            return resp.read()
        text = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise FilesAPIError(resp.status, text)

    # ------------------- public API -------------------

    def ping(self) -> dict:
        return self._request(self._build_url("ping"))

    def list(self, path: str = DEFAULT_REMOTE_ROOT) -> list[dict]:
        out = self._request(self._build_url("list", path=path))
        return out.get("data", [])

    def stat(self, path: str) -> dict:
        out = self._request(self._build_url("stat", path=path))
        return out.get("data", {})

    def get(self, remote_path: str) -> bytes:
        return self._request(self._build_url("get", path=remote_path), raw=True)

    def put(self, remote_path: str, body: bytes) -> dict:
        return self._request(
            self._build_url("put", path=remote_path),
            method="POST",
            body=body,
            extra_headers={"Content-Type": "application/octet-stream"},
        )

    def delete(self, path: str) -> dict:
        return self._request(self._build_url("delete", path=path), method="POST", body=b"")

    def mkdir(self, path: str) -> dict:
        return self._request(self._build_url("mkdir", path=path), method="POST", body=b"")

    def rename(self, src: str, dst: str) -> dict:
        return self._request(self._build_url("rename", path=src, to=dst), method="POST", body=b"")


def _print_listing(entries: list[dict]) -> None:
    if not entries:
        print("  (empty)")
        return
    for e in entries:
        size = e.get("size")
        size_str = "        -" if size is None else f"{size:>9}"
        print(f"  {e.get('mode',''):<4}  {size_str}  {e.get('type','?'):<4}  {e.get('name','')}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip())
        return 1
    cmd = argv[1]
    client = Client.from_config()

    if cmd == "ping":
        print(json.dumps(client.ping(), indent=2))
        return 0

    if cmd == "ls":
        path = argv[2] if len(argv) > 2 else DEFAULT_REMOTE_ROOT
        entries = client.list(path)
        print(f"\nContents of {path}: ({len(entries)} entries)")
        _print_listing(entries)
        return 0

    if cmd == "stat":
        if len(argv) < 3:
            sys.stderr.write("usage: hm_files.py stat <path>\n"); return 2
        print(json.dumps(client.stat(argv[2]), indent=2))
        return 0

    if cmd == "get":
        if len(argv) < 4:
            sys.stderr.write("usage: hm_files.py get <remote> <local>\n"); return 2
        remote, local = argv[2], argv[3]
        body = client.get(remote)
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(body)
        print(f"Downloaded {len(body)} bytes -> {local}")
        return 0

    if cmd == "put":
        if len(argv) < 4:
            sys.stderr.write("usage: hm_files.py put <local> <remote>\n"); return 2
        local, remote = argv[2], argv[3]
        body = Path(local).read_bytes()
        result = client.put(remote, body)
        print(json.dumps(result, indent=2))
        return 0

    if cmd in ("rm", "delete"):
        if len(argv) < 3:
            sys.stderr.write("usage: hm_files.py rm <path>\n"); return 2
        print(json.dumps(client.delete(argv[2]), indent=2))
        return 0

    if cmd == "mkdir":
        if len(argv) < 3:
            sys.stderr.write("usage: hm_files.py mkdir <path>\n"); return 2
        print(json.dumps(client.mkdir(argv[2]), indent=2))
        return 0

    if cmd == "mv":
        if len(argv) < 4:
            sys.stderr.write("usage: hm_files.py mv <from> <to>\n"); return 2
        print(json.dumps(client.rename(argv[2], argv[3]), indent=2))
        return 0

    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except FilesAPIError as exc:
        sys.stderr.write(f"\nAPI error: {exc}\n")
        if exc.status == 403:
            sys.stderr.write(
                "  -> auth or whitelist failure. Check HM_ADMIN_OP_PASSWORD "
                "and that the path starts with an allowed root.\n"
            )
        elif exc.status == 404:
            sys.stderr.write(
                "  -> endpoint or target not found. Confirm "
                "https://handelsmark.co.za/admin-api/files.php is deployed.\n"
            )
        sys.exit(1)
