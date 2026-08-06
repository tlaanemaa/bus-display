import ast
from pathlib import Path
import re


DEPLOY = Path("deploy.bat")
SRC = Path("src")


def _copy_positions(text: str, destination: str) -> list[int]:
    pattern = re.compile(
        r"^\s*%MP%\s+%CONN%\s+fs\s+cp\s+.*?\"" + re.escape(destination) + r"\"\s*$",
        re.MULTILINE,
    )
    return [match.start() for match in pattern.finditer(text)]


def _top_level_loops(text: str):
    return re.finditer(
        r"for %%F in \((?P<inputs>[^\n]*)\) do \((?P<body>.*?)^\)",
        text,
        re.MULTILINE | re.DOTALL,
    )


def test_main_is_tiny_source_shim_for_precompiled_app_runtime():
    main_path = SRC / "main.py"
    app_path = SRC / "app.py"
    assert app_path.is_file()
    main_source = main_path.read_text(encoding="utf-8")
    assert len(main_source.encode("utf-8")) <= 512
    tree = ast.parse(main_source)
    executable = [
        node for node in tree.body
        if not (isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str))
    ]
    assert len(executable) == 1
    assert isinstance(executable[0], ast.Import)
    assert [alias.name for alias in executable[0].names] == ["app"]
    assert app_path.stat().st_size > 40_000


def test_app_mpy_support_copy_precedes_source_main_and_main_mpy_never_ships():
    text = DEPLOY.read_text(encoding="utf-8")
    loops = list(_top_level_loops(text))
    compile_loop = next(
        match for match in loops if 'mpy_cross "%%F"' in match.group("body")
    )
    assert '"%SRCDIR%\\*.py"' in compile_loop.group("inputs")
    assert 'if /I not "%%~nxF"=="main.py"' in compile_loop.group("body")
    assert "app.py" not in compile_loop.group("body")

    support_loop = next(
        match for match in loops if '":%%~nxF"' in match.group("body")
    )
    assert '"%SRCDIR%\\*.mpy"' in support_loop.group("inputs")
    assert 'if /I not "%%~nxF"=="main.mpy"' in support_loop.group("body")
    assert support_loop.start() < _copy_positions(text, ":main.py")[0]


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

    support_loop = next(
        (match for match in _top_level_loops(text)
         if '":%%~nxF"' in match.group("body")),
        None,
    )
    assert support_loop is not None
    inputs = support_loop.group("inputs")
    assert '"%SRCDIR%\\*.mpy"' in inputs
    assert '"%SRCDIR%\\*.json"' in inputs
    assert "main.py" not in inputs
    assert '"%SRCDIR%\\*.py"' not in inputs
    support_body = support_loop.group("body")
    assert 'if /I not "%%~nxF"=="main.mpy"' in support_body
