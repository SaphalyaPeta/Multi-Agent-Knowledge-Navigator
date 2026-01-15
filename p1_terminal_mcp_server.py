"""
P1 as MCP Tool (Terminal) — P5 READY

Exposes exactly 3 MCP tools:
  - initiate_terminal() -> dict
  - execute_command(cmd: str, timeout_sec: float = 20.0) -> dict
  - terminate_terminal() -> dict

Notes:
- Persistent bash session: `cd` persists across commands.
- Streamable HTTP endpoint: http://localhost:3003/mcp

Security warning:
- Executes shell commands as your user. Use only on a trusted machine.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
import select
from dataclasses import dataclass
from typing import Optional, Dict, Any

from mcp.server.fastmcp import FastMCP

DEFAULT_PORT = int(os.getenv("P1_MCP_PORT", "3003"))
DEFAULT_SHELL = os.getenv("P1_SHELL", "/bin/bash")

# Prevent Open WebUI/tool UI from choking on huge outputs
MAX_OUTPUT_CHARS = int(os.getenv("P1_MAX_OUTPUT_CHARS", "20000"))
MAX_OUTPUT_LINES = int(os.getenv("P1_MAX_OUTPUT_LINES", "800"))


@dataclass
class ShellSession:
    proc: subprocess.Popen


_session: Optional[ShellSession] = None
_lock = asyncio.Lock()


def _start_shell() -> ShellSession:
    args = [DEFAULT_SHELL, "--noprofile", "--norc"]
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=os.path.expanduser("~"),  # start in the user's HOME
        text=True,
        bufsize=1,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Failed to start shell with pipes")

    # Reduce prompt noise
    proc.stdin.write("export PS1=''\n")
    proc.stdin.flush()

    return ShellSession(proc=proc)


def _stop_shell() -> None:
    global _session
    if _session is None:
        return

    try:
        _session.proc.terminate()
        _session.proc.wait(timeout=2)
    except Exception:
        try:
            _session.proc.kill()
        except Exception:
            pass
    finally:
        _session = None


def _ensure_shell() -> subprocess.Popen:
    global _session
    if _session is None:
        _session = _start_shell()

    if _session.proc.poll() is not None:
        _session = _start_shell()

    return _session.proc


def _read_until_done(
    proc: subprocess.Popen,
    cwd_marker: str,
    done_marker: str,
    timeout_sec: float,
) -> Dict[str, Any]:
    """Read merged stdout until DONE marker appears. Also capture CWD marker."""
    assert proc.stdout is not None

    start = time.time()
    lines: list[str] = []
    exit_code: Optional[int] = None
    cwd_after: Optional[str] = None

    stored_chars = 0
    stored_lines = 0
    truncated = False

    while True:
        if time.time() - start > timeout_sec:
            return {
                "ok": False,
                "error": f"timeout after {timeout_sec:.1f}s",
                "stdout": "".join(lines),
                "exit_code": exit_code,
                "cwd_after": cwd_after,
                "truncated": truncated,
            }

        rlist, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not rlist:
            continue

        line = proc.stdout.readline()
        if line == "":
            return {
                "ok": False,
                "error": "shell session ended unexpectedly",
                "stdout": "".join(lines),
                "exit_code": exit_code,
                "cwd_after": cwd_after,
                "truncated": truncated,
            }

        # Capture cwd
        if line.startswith(cwd_marker):
            cwd_after = line.split(":", 1)[1].strip()
            continue

        # Stop on done marker
        if line.startswith(done_marker):
            try:
                exit_code = int(line.split(":", 1)[1].strip())
            except Exception:
                exit_code = None
            break

        # Store normal output with truncation protection
        if (not truncated) and stored_lines < MAX_OUTPUT_LINES and (stored_chars + len(line) <= MAX_OUTPUT_CHARS):
            lines.append(line)
            stored_lines += 1
            stored_chars += len(line)
        else:
            truncated = True

    return {
        "ok": True,
        "stdout": "".join(lines),
        "exit_code": exit_code,
        "cwd_after": cwd_after,
        "truncated": truncated,
    }


async def _execute(cmd: str, timeout_sec: float) -> Dict[str, Any]:
    async with _lock:
        proc = _ensure_shell()
        assert proc.stdin is not None

        uid = uuid.uuid4().hex
        cwd_marker = f"__MCP_CWD_{uid}__"
        done_marker = f"__MCP_DONE_{uid}__"

        # Run cmd, then print cwd + done markers
        payload = (
            cmd.rstrip("\n") + "\n"
            "__mcp_ec=$?\n"
            f'printf "{cwd_marker}:%s\\n" "$PWD"\n'
            f'printf "{done_marker}:%s\\n" "$__mcp_ec"\n'
        )

        proc.stdin.write(payload)
        proc.stdin.flush()

        return _read_until_done(proc, cwd_marker=cwd_marker, done_marker=done_marker, timeout_sec=timeout_sec)


# FastMCP version compatibility (some versions accept port=, some don't)
try:
    mcp = FastMCP(name="p1-terminal", port=DEFAULT_PORT)
except TypeError:
    mcp = FastMCP(name="p1-terminal")


@mcp.tool()
async def initiate_terminal() -> Dict[str, Any]:
    """Start/reset the persistent terminal session."""
    async with _lock:
        _stop_shell()
        _ensure_shell()

    res = await _execute("pwd", timeout_sec=8.0)
    return {
        "ok": True,
        "message": "terminal session initiated",
        "cwd": (res.get("stdout") or "").strip() or res.get("cwd_after"),
    }


@mcp.tool()
async def terminate_terminal() -> Dict[str, Any]:
    """Safely close the persistent terminal session."""
    async with _lock:
        _stop_shell()
    return {"ok": True, "message": "terminal session terminated"}


@mcp.tool()
async def execute_command(cmd: str, timeout_sec: float = 20.0) -> Dict[str, Any]:
    """Execute a shell command and return stdout + exit code + cwd_after."""
    if cmd is None or not str(cmd).strip():
        return {"ok": False, "error": "empty command", "stdout": "", "exit_code": None, "cwd_after": None}

    t0 = time.time()
    res = await _execute(cmd, timeout_sec=float(timeout_sec))
    res["cmd"] = cmd
    res["duration_ms"] = int((time.time() - t0) * 1000)

    # If command succeeded but printed nothing (like `cd`), return a small stdout so UI doesn't look "stuck"
    if res.get("ok") and res.get("exit_code") == 0 and not (res.get("stdout") or "").strip():
        res["stdout"] = "(ok)\n"

    return res


if __name__ == "__main__":
    # Streamable HTTP exposes /mcp
    mcp.run(transport="streamable-http")
