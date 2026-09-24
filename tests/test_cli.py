"""命令行入口测试：只启动本项目 CLI 自身，载荷均为无害文本。"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from cmdline_parser import RULES_VERSION, SCHEMA_VERSION, __version__
from cmdline_parser.cli import main

SRC = Path(__file__).resolve().parents[1] / "src"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) if not existing else f"{SRC}{os.pathsep}{existing}"
    return env


def run_cli(*args: str, stdin: bytes = b"", executable: list[str] | None = None):
    command = executable or [sys.executable, "-m", "cmdline_parser"]
    return subprocess.run(
        [*command, *args],
        input=stdin,
        capture_output=True,
        env=_env(),
        timeout=60,
        check=False,
    )


def _json(proc) -> dict:
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout.endswith(b"\n")
    return json.loads(proc.stdout.decode("utf-8"))


def test_stdin_keeps_text_exactly() -> None:
    data = _json(run_cli(stdin=b"netstat -ano | findstr 443\n"))
    assert data["raw"] == "netstat -ano | findstr 443\n"
    assert [t["raw"] for t in data["tokens"]] == ["netstat", "-ano", "findstr", "443"]
    assert [s["raw"] for s in data["symbols"]] == ["|"]
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["processing"]["rules_version"] == RULES_VERSION


def test_stdin_crlf_and_bom_are_kept() -> None:
    data = _json(run_cli(stdin=b"\xef\xbb\xbfa b\r\n"))
    assert data["raw"] == "\ufeffa b\r\n"


def test_compact_output_is_one_line() -> None:
    proc = run_cli(stdin=b"echo a\nb\n")
    assert proc.returncode == 0
    assert proc.stdout.count(b"\n") == 1
    assert proc.stderr == b""


def test_text_option() -> None:
    data = _json(run_cli("--text", "c^md /c ne^tstat"))
    assert data["raw"] == "c^md /c ne^tstat"
    values = {term["value"] for token in data["tokens"] for term in token["terms"]}
    assert {"cmd", "netstat"} <= values


def test_text_option_value_starting_with_dash() -> None:
    data = _json(run_cli("--text=-enc"))
    assert data["raw"] == "-enc"


def test_file_option(tmp_path: Path) -> None:
    path = tmp_path / "line.txt"
    path.write_bytes("tool.exe --output \"C:\\Program Files\\Exam\r\n".encode("utf-8"))
    data = _json(run_cli("--file", str(path)))
    assert data["raw"] == "tool.exe --output \"C:\\Program Files\\Exam\r\n"
    assert [n["code"] for n in data["notices"]] == ["UNCLOSED_QUOTE"]


def test_non_ascii_round_trip() -> None:
    text = "echo 你好 😀 Ｗｒｉｔｅ"
    from_stdin = _json(run_cli(stdin=text.encode("utf-8")))
    from_arg = _json(run_cli("--text", text))
    assert from_stdin["raw"] == from_arg["raw"] == text
    assert from_stdin == from_arg


def test_broken_input_still_succeeds() -> None:
    proc = run_cli(stdin=b"a '\" | & ^ ` ( { [ ] %4 \\x4 \\u00 ==== --x=")
    data = _json(proc)
    assert data["notices"]
    assert proc.stderr == b""


def test_empty_input() -> None:
    data = _json(run_cli(stdin=b""))
    assert data["raw"] == "" and data["tokens"] == []


def test_encoded_command_is_decoded() -> None:
    payload = base64.b64encode("Write-Output hello".encode("utf-16le")).decode("ascii")
    data = _json(run_cli("--text", f"powershell -NoProfile -enc {payload}"))
    views = data["decoded_views"]
    assert [(v["text"], v["charset"], v["hint"]) for v in views] == [
        ("Write-Output hello", "utf-16le", "C-HINT-ENC-001")
    ]


def test_no_decode() -> None:
    payload = base64.b64encode(b"Write-Output hello").decode("ascii")
    data = _json(run_cli("--no-decode", "--text", f"tool {payload}"))
    assert data["decoded_views"] == []
    assert data["processing"]["decode_enabled"] is False


def test_pretty_matches_compact() -> None:
    compact = run_cli("--text", "a | b")
    pretty = run_cli("--pretty", "--text", "a | b")
    assert pretty.stdout.count(b"\n") > 1
    assert _json(pretty) == _json(compact)


def test_version() -> None:
    proc = run_cli("--version")
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == (
        f"cmdline-parser {__version__} (rules {RULES_VERSION}, schema {SCHEMA_VERSION})"
    )


def test_help() -> None:
    proc = run_cli("--help")
    assert proc.returncode == 0
    assert b"--text" in proc.stdout and b"--file" in proc.stdout


def test_missing_file_is_runtime_error(tmp_path: Path) -> None:
    proc = run_cli("--file", str(tmp_path / "missing.txt"))
    assert proc.returncode == 1
    assert proc.stdout == b""
    assert proc.stderr.startswith(b"cmdline-parser: error: cannot read input")


def test_invalid_utf8_stdin_is_runtime_error() -> None:
    proc = run_cli(stdin=b"abc\xff def")
    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"not valid UTF-8 (byte offset 3)" in proc.stderr


def test_text_and_file_are_mutually_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "line.txt"
    path.write_text("a", encoding="utf-8")
    proc = run_cli("--text", "a", "--file", str(path))
    assert proc.returncode == 2
    assert proc.stdout == b""


def test_unknown_option_is_usage_error() -> None:
    proc = run_cli("--bogus")
    assert proc.returncode == 2
    assert proc.stdout == b""


def test_in_process_surrogate_text_is_runtime_error(capsys) -> None:
    assert main(["--text", "\ud800"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot be encoded as UTF-8" in captured.err


def test_in_process_success(capsysbinary) -> None:
    assert main(["--text", "a b"]) == 0
    captured = capsysbinary.readouterr()
    assert json.loads(captured.out)["raw"] == "a b"


def test_console_script_if_installed() -> None:
    name = "cmdline-parser.exe" if os.name == "nt" else "cmdline-parser"
    script = Path(sysconfig.get_path("scripts")) / name
    if not script.exists():
        pytest.skip("console script is not installed in this environment")
    data = _json(run_cli("--text", "a b", executable=[str(script)]))
    assert data["raw"] == "a b"
