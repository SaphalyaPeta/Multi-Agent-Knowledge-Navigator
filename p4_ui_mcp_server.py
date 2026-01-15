"""
P4 as MCP Tool (UI Automation)

Implements an MCP server (Streamable HTTP) exposing these tools:
    ui_click(element_name: str) -> dict
    ui_type(element_name: str, text: str) -> dict
    ui_get_mouse_position() -> dict   (calibration helper)

Notes:
- Uses a JSON coordinate mapping file (default: coordinate_map.json).
- Designed for local dev with Open WebUI; add as: http://localhost:3004/mcp
- Coordinates are absolute screen pixels; they change if you move/resize windows or change scaling.

"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Any, Tuple

import pyautogui
from mcp.server.fastmcp import FastMCP


DEFAULT_PORT = int(os.getenv("P4_MCP_PORT", "3004"))
DEFAULT_COORDS_FILE = os.getenv("P4_COORDS_FILE", "coordinate_map.json")

# Safety defaults
pyautogui.FAILSAFE = True   # move mouse to top-left corner to abort
pyautogui.PAUSE = 0.05      # small delay after each action


def _load_coords(path: str) -> Dict[str, Dict[str, float]]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Coordinate map not found: {path}. Create coordinate_map.json in the same folder."
        )

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("coordinate_map.json must be a JSON object {name: {x:..., y:...}}")

    # Validate entries
    for name, xy in data.items():
        if not isinstance(xy, dict) or "x" not in xy or "y" not in xy:
            raise ValueError(f"Invalid mapping for '{name}'. Expected {{'x': number, 'y': number}}.")
        if not isinstance(xy["x"], (int, float)) or not isinstance(xy["y"], (int, float)):
            raise ValueError(f"Invalid x/y types for '{name}'. x and y must be numbers.")
    return data


def _get_xy(coords: Dict[str, Dict[str, float]], element_name: str) -> Tuple[int, int]:
    if element_name not in coords:
        raise KeyError(
            f"Unknown element '{element_name}'. Add it to {DEFAULT_COORDS_FILE}."
        )
    x = int(coords[element_name]["x"])
    y = int(coords[element_name]["y"])
    return x, y


# Create FastMCP instance.
# Some MCP versions allow "port=" in the constructor; others don't.
try:
    mcp = FastMCP(name="p4-ui-automation", port=DEFAULT_PORT)
except TypeError:
    mcp = FastMCP(name="p4-ui-automation")

# Load coordinates once on startup
COORDS: Dict[str, Dict[str, float]] = _load_coords(DEFAULT_COORDS_FILE)


@mcp.tool()
def ui_get_mouse_position() -> Dict[str, int]:
    """
    Calibration helper:
    Hover your mouse over the UI target (e.g., X on last browser tab),
    then call this tool to get the (x, y) to paste into coordinate_map.json.
    """
    x, y = pyautogui.position()
    return {"ok": True, "x": int(x), "y": int(y)}


@mcp.tool()
def ui_click(element_name: str) -> Dict[str, Any]:
    """
    Click a UI element by name using coordinate_map.json.

    Example:
      ui_click("browser_close_last_tab")
    """
    try:
        x, y = _get_xy(COORDS, element_name)
        pyautogui.moveTo(x, y, duration=0.05)
        pyautogui.click()
        return {"ok": True, "success": True, "element": element_name, "x": x, "y": y}
    except Exception as e:
        return {"ok": False, "success": False, "element": element_name, "error": str(e)}


@mcp.tool()
def ui_type(element_name: str, text: str) -> Dict[str, Any]:
    """
    Click a UI element by name, then type text.

    Example:
      ui_type("search_box", "hello world")
    """
    try:
        x, y = _get_xy(COORDS, element_name)
        pyautogui.moveTo(x, y, duration=0.05)
        pyautogui.click()
        time.sleep(0.05)  # ensure focus
        pyautogui.write(text, interval=0.01)
        return {
            "ok": True,
            "success": True,
            "element": element_name,
            "x": x,
            "y": y,
            "typed_chars": len(text),
        }
    except Exception as e:
        return {"ok": False, "success": False, "element": element_name, "error": str(e)}


def _run_fastmcp_compat() -> None:
    """
    Run Streamable HTTP in a way compatible with different MCP versions.
    - Preferred: mcp.run(transport="streamable-http") and let constructor port apply.
    - Fallback: try passing port if supported.
    """
    try:
        mcp.run(transport="streamable-http")
        return
    except TypeError:
        pass

    # Some versions accept port in run()
    try:
        mcp.run(transport="streamable-http", port=DEFAULT_PORT)
        return
    except TypeError:
        pass

    # Last fallback
    mcp.run()


if __name__ == "__main__":
    _run_fastmcp_compat()