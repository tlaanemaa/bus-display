from pathlib import Path
import re


DEPLOY = Path("deploy.bat")


def _copy_positions(text: str, destination: str) -> list[int]:
    pattern = re.compile(
        r"^\s*%MP%\s+%CONN%\s+fs\s+cp\s+.*?\"" + re.escape(destination) + r"\"\s*$",
        re.MULTILINE,
    )
    return [match.start() for match in pattern.finditer(text)]


def test_main_is_activated_once_after_support_files_and_before_reset():
    text = DEPLOY.read_text(encoding="utf-8")
    main_copy = _copy_positions(text, ":main.py")
    module_copy = _copy_positions(text, ":%%~nxF")
    lib_copy = _copy_positions(text, ":lib/%%~nxF")
    font_copy = _copy_positions(text, ":fonts/%%~nxF")
    reset = text.index("%MP% %CONN% reset")

    assert main_copy and len(main_copy) == 1
    assert module_copy and lib_copy and font_copy
    assert max(module_copy) < main_copy[0]
    assert max(lib_copy) < main_copy[0]
    assert max(font_copy) < main_copy[0]
    assert main_copy[0] < reset

    top_level_loops = re.finditer(
        r"for %%F in \((?P<inputs>[^\n]*)\) do \((?P<body>.*?)^\)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    support_loop = next(
        (match for match in top_level_loops if '":%%~nxF"' in match.group("body")),
        None,
    )
    assert support_loop is not None
    inputs = support_loop.group("inputs")
    assert '"%SRCDIR%\\*.mpy"' in inputs
    assert '"%SRCDIR%\\*.json"' in inputs
    assert "main.py" not in inputs
    assert '"%SRCDIR%\\*.py"' not in inputs
