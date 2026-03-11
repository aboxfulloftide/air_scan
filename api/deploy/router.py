from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pathlib import Path
from pydantic import BaseModel
import asyncio
import json
import os
import re

router = APIRouter(prefix="/api/deploy", tags=["deploy"])

DEPLOY_DIR   = Path(__file__).resolve().parent.parent.parent / "deploy"
TARGETS_DIR  = DEPLOY_DIR / "targets"
SECRETS_FILE = DEPLOY_DIR / ".secrets"   # gitignored, JSON {name: password}
DEPLOY_SCRIPT = DEPLOY_DIR / "deploy.sh"

# Fields written to the .conf file (no password — that lives in .secrets)
CONF_FIELDS = [
    "HOST", "USER", "REMOTE_PATH", "SCP_FLAGS", "AUTH",
    "SERVICE_TYPE", "SERVICE_NAME", "START_CMD", "STOP_CMD",
    "PRE_START", "POST_DEPLOY",
]

DEPLOYABLE_FILES = {
    "wifi_scanner.py":   {"desc": "Scapy monitor-mode scanner, direct DB push", "for": "linux"},
    "router_capture.sh": {"desc": "Capture script (iw scan + tcpdump)", "for": "openwrt"},
}


def _pass_var(name: str) -> str:
    """Derive the env var name for a target's password, e.g. 'office-pi5' -> 'OFFICE_PI5_PASS'."""
    return re.sub(r'[^A-Z0-9]', '_', name.upper()) + "_PASS"


def _load_secrets() -> dict:
    if SECRETS_FILE.exists():
        return json.loads(SECRETS_FILE.read_text())
    return {}


def _save_secrets(secrets: dict):
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_FILE.write_text(json.dumps(secrets, indent=2))
    SECRETS_FILE.chmod(0o600)


def _safe_name(name: str) -> str:
    clean = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not clean:
        raise HTTPException(status_code=400, detail="Invalid target name")
    return clean


def _parse_conf(path: Path) -> dict:
    data = {"name": path.stem}
    content = path.read_text()

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            data["description"] = line[2:]
            break

    for field in CONF_FIELDS:
        ml_match = re.search(rf'^{field}="(.*?)"', content, re.MULTILINE | re.DOTALL)
        if ml_match:
            val = ml_match.group(1).strip()
            val = re.sub(r'\$\{[^:}]+:-([^}]+)\}', r'\1', val)
            data[field] = val
            continue
        sl_match = re.search(rf'^{field}=(.+)$', content, re.MULTILINE)
        if sl_match:
            val = sl_match.group(1).strip().strip('"').strip("'")
            val = re.sub(r'\$\{[^:}]+:-([^}]+)\}', r'\1', val)
            data[field] = val

    files_match = re.search(r'FILES=\(([^)]*)\)', content)
    if files_match:
        data["FILES"] = [f.strip().strip('"').strip("'")
                         for f in files_match.group(1).split() if f.strip()]

    # Indicate whether a password is stored (never return the value)
    secrets = _load_secrets()
    data["has_password"] = path.stem in secrets

    return data


def _write_conf(path: Path, data: dict):
    lines = []
    desc = data.get("description", "")
    if desc:
        lines.append(f"# {desc}")

    # PASS_VAR is auto-derived at deploy time — write it so deploy.sh knows which var to use
    if data.get("AUTH") == "sshpass":
        data = {**data, "PASS_VAR": _pass_var(data.get("name", path.stem))}
        # Insert PASS_VAR after AUTH
        fields = list(CONF_FIELDS)
        auth_idx = fields.index("AUTH")
        fields.insert(auth_idx + 1, "PASS_VAR")
    else:
        fields = list(CONF_FIELDS)

    for field in fields:
        val = data.get(field, "")
        if not val:
            continue
        if "\n" in val:
            lines.append(f'{field}="')
            lines.append(val.rstrip())
            lines.append('"')
        elif any(c in val for c in '>&|$;(){}\'\" '):
            lines.append(f'{field}="{val}"')
        else:
            lines.append(f'{field}={val}')

    files = data.get("FILES", [])
    if files:
        lines.append(f"FILES=({' '.join(files)})")

    lines.append("")
    path.write_text("\n".join(lines))


class TargetConf(BaseModel):
    name: str
    description: str = ""
    HOST: str
    USER: str = "matheau"
    REMOTE_PATH: str = "/home/matheau/code/air_scan/scanners/"
    SCP_FLAGS: str = ""
    AUTH: str = "key"
    password: str = ""         # plaintext, stored in .secrets, never written to .conf
    SERVICE_TYPE: str = "systemd"
    SERVICE_NAME: str = ""
    START_CMD: str = ""
    STOP_CMD: str = ""
    PRE_START: str = ""
    POST_DEPLOY: str = ""
    FILES: list[str] = []


@router.get("/targets")
async def list_targets():
    targets = []
    if not TARGETS_DIR.exists():
        return targets
    for conf in sorted(TARGETS_DIR.glob("*.conf")):
        try:
            targets.append(_parse_conf(conf))
        except Exception:
            targets.append({"name": conf.stem, "error": "failed to parse"})
    return targets


@router.get("/targets/{name}")
async def get_target(name: str):
    name = _safe_name(name)
    path = TARGETS_DIR / f"{name}.conf"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")
    return _parse_conf(path)


def _save_target(name: str, body: TargetConf):
    path = TARGETS_DIR / f"{name}.conf"
    data = body.model_dump()
    data["name"] = name
    _write_conf(path, data)

    # Save password to .secrets if provided; clear it if auth switched to key
    secrets = _load_secrets()
    if body.AUTH == "sshpass" and body.password:
        secrets[name] = body.password
        _save_secrets(secrets)
    elif body.AUTH == "key" and name in secrets:
        del secrets[name]
        _save_secrets(secrets)


@router.post("/targets")
async def create_target(body: TargetConf):
    name = _safe_name(body.name)
    path = TARGETS_DIR / f"{name}.conf"
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Target '{name}' already exists")
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    _save_target(name, body)
    return _parse_conf(path)


@router.put("/targets/{name}")
async def update_target(name: str, body: TargetConf):
    name = _safe_name(name)
    path = TARGETS_DIR / f"{name}.conf"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")

    new_name = _safe_name(body.name)
    if new_name != name:
        new_path = TARGETS_DIR / f"{new_name}.conf"
        if new_path.exists():
            raise HTTPException(status_code=409, detail=f"Target '{new_name}' already exists")
        path.rename(new_path)
        # Rename secret key too
        secrets = _load_secrets()
        if name in secrets:
            secrets[new_name] = secrets.pop(name)
            _save_secrets(secrets)
        name = new_name

    _save_target(name, body)
    return _parse_conf(TARGETS_DIR / f"{name}.conf")


@router.delete("/targets/{name}")
async def delete_target(name: str):
    name = _safe_name(name)
    path = TARGETS_DIR / f"{name}.conf"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")
    path.unlink()
    secrets = _load_secrets()
    if name in secrets:
        del secrets[name]
        _save_secrets(secrets)
    return {"ok": True}


@router.get("/files")
async def list_scanner_files():
    return [{"name": name, **info} for name, info in DEPLOYABLE_FILES.items()]


def _build_env(name: str) -> dict:
    """Build env for deploy.sh, injecting the target's password if set."""
    env = os.environ.copy()
    secrets = _load_secrets()
    if name in secrets:
        env[_pass_var(name)] = secrets[name]
    return env


async def _stream_deploy(args: list[str], env: dict):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    async for line in proc.stdout:
        yield line
    await proc.wait()
    yield f"\n--- exit code: {proc.returncode} ---\n".encode()


@router.post("/run/{name}")
async def run_deploy(name: str):
    name = _safe_name(name)
    if not (TARGETS_DIR / f"{name}.conf").exists():
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")
    if not DEPLOY_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="deploy.sh not found")
    env = _build_env(name)
    return StreamingResponse(_stream_deploy([str(DEPLOY_SCRIPT), name], env), media_type="text/plain")


@router.post("/run-all")
async def run_deploy_all():
    if not DEPLOY_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="deploy.sh not found")
    # Inject all stored passwords
    env = os.environ.copy()
    for tname, pwd in _load_secrets().items():
        env[_pass_var(tname)] = pwd
    return StreamingResponse(_stream_deploy([str(DEPLOY_SCRIPT), "all"], env), media_type="text/plain")
