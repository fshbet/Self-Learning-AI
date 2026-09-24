"""Auto-start at logon (V2 §P7, optional): keeps the self-updating loop alive without a terminal.

Windows → Task Scheduler (``schtasks``, ONLOGON, runs a generated ``.cmd`` launcher that starts the Postgres
container if Docker is available and then ``kp serve``); Linux → a systemd *user* unit; macOS → a launchd agent.
Nothing here needs administrator rights. Logs go to ``data/logs/kp-serve.log``.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _log_dir() -> Path:
    d = get_settings().local_store_path.parent / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def _uv() -> str:
    return shutil.which("uv") or "uv"


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ----------------------------------------------------------------------------- windows


def _windows_launcher(name: str) -> Path:
    log = _log_dir() / "kp-serve.log"
    launcher = _log_dir().parent / f"{name}.cmd"
    launcher.write_text(
        "\r\n".join(
            [
                "@echo off",
                f'cd /d "{PROJECT_ROOT}"',
                "where docker >nul 2>nul && docker compose up -d postgres >nul 2>nul",
                f'"{_uv()}" run kp serve >> "{log}" 2>&1',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return launcher


def _windows_install(name: str) -> str:
    launcher = _windows_launcher(name)
    code, out = _run(
        [
            "schtasks",
            "/Create",
            "/TN",
            name,
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
            "/TR",
            f'cmd /c ""{launcher}""',
        ]
    )
    if code != 0:
        return f"schtasks failed ({code}): {out}"
    return (
        f"Installed scheduled task '{name}' (at logon) → {launcher}\n"
        f"Logs: {_log_dir() / 'kp-serve.log'} · start now: schtasks /Run /TN {name}"
    )


def _windows_uninstall(name: str) -> str:
    code, out = _run(["schtasks", "/Delete", "/TN", name, "/F"])
    launcher = _log_dir().parent / f"{name}.cmd"
    if launcher.exists():
        launcher.unlink()
    return f"Removed scheduled task '{name}'" if code == 0 else f"schtasks failed ({code}): {out}"


def _windows_status(name: str) -> str:
    code, out = _run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
    return out if code == 0 else f"not installed ({out.splitlines()[-1] if out else code})"


# ----------------------------------------------------------------------------- linux (systemd --user)


def _systemd_unit(name: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{name}.service"


def _linux_install(name: str) -> str:
    unit = _systemd_unit(name)
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Self-Learning AI (API + worker + scheduler)",
                "After=network-online.target docker.service",
                "",
                "[Service]",
                f"WorkingDirectory={PROJECT_ROOT}",
                "ExecStartPre=-/usr/bin/env docker compose up -d postgres",
                f"ExecStart={_uv()} run kp serve",
                "Restart=on-failure",
                "RestartSec=10",
                f"StandardOutput=append:{_log_dir() / 'kp-serve.log'}",
                f"StandardError=append:{_log_dir() / 'kp-serve.log'}",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", name]):
        code, out = _run(cmd)
        if code != 0:
            return f"wrote {unit}; `{' '.join(cmd)}` failed: {out}"
    return (
        f"Installed systemd user unit {unit} (enabled, started). "
        "For start-before-login run: loginctl enable-linger $USER"
    )


def _linux_uninstall(name: str) -> str:
    _run(["systemctl", "--user", "disable", "--now", name])
    unit = _systemd_unit(name)
    if unit.exists():
        unit.unlink()
    _run(["systemctl", "--user", "daemon-reload"])
    return f"Removed systemd user unit '{name}'"


def _linux_status(name: str) -> str:
    code, out = _run(["systemctl", "--user", "status", name, "--no-pager"])
    return out or f"not installed ({code})"


# ----------------------------------------------------------------------------- macos (launchd)


def _plist(name: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.knowledgeplatform.{name}.plist"


def _macos_install(name: str) -> str:
    plist = _plist(name)
    plist.parent.mkdir(parents=True, exist_ok=True)
    log = _log_dir() / "kp-serve.log"
    command = f'cd "{PROJECT_ROOT}" && (docker compose up -d postgres || true) && "{_uv()}" run kp serve'
    command = command.replace("&", "&amp;")
    plist.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.knowledgeplatform.{name}</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>{command}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""",
        encoding="utf-8",
    )
    code, out = _run(["launchctl", "load", "-w", str(plist)])
    return f"Installed launchd agent {plist}" + ("" if code == 0 else f"; launchctl load failed: {out}")


def _macos_uninstall(name: str) -> str:
    plist = _plist(name)
    if plist.exists():
        _run(["launchctl", "unload", "-w", str(plist)])
        plist.unlink()
    return f"Removed launchd agent '{name}'"


def _macos_status(name: str) -> str:
    code, out = _run(["launchctl", "list", f"com.knowledgeplatform.{name}"])
    return out if code == 0 else "not installed"


# ----------------------------------------------------------------------------- dispatch


def _os() -> str:
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    return "macos" if platform.system() == "Darwin" else "linux"


def install(name: str = "KnowledgePlatform") -> str:
    return {"windows": _windows_install, "linux": _linux_install, "macos": _macos_install}[_os()](name)


def uninstall(name: str = "KnowledgePlatform") -> str:
    return {"windows": _windows_uninstall, "linux": _linux_uninstall, "macos": _macos_uninstall}[_os()](name)


def status(name: str = "KnowledgePlatform") -> str:
    return {"windows": _windows_status, "linux": _linux_status, "macos": _macos_status}[_os()](name)


__all__ = ["install", "status", "uninstall"]
