"""Interactive sessions retain accepted state without replaying earlier inputs."""

from io import StringIO
from pathlib import Path
import subprocess
import sys

import pytest

from error.Error import WysnaError
from runtime.Environment import Environment
from runtime.Values import WysnaInt
from tooling.ReplSession import ReplSession


def test_variables_and_errors_preserve_consistent_state():
    output = StringIO()
    session = ReplSession(stdout=output)
    session.execute('let mut value = 41')
    session.execute('value = value + 1')
    with pytest.raises(WysnaError) as failure:
        session.execute('let wrong: int = "hello"')
    assert failure.value.code == 'W2001'
    session.execute('print(value)')
    with pytest.raises(WysnaError):
        session.execute('let temporary = 99; print(1 / 0)')
    with pytest.raises(WysnaError) as failure:
        session.execute('print(temporary)')
    assert failure.value.code == 'W1001'
    session.execute('let temporary = 7')
    session.execute('print(temporary)')
    assert output.getvalue() == '42\n7\n'


def test_failed_input_keeps_completed_mutations_and_does_not_replay_output():
    output = StringIO()
    session = ReplSession(stdout=output)
    session.execute('let mut value = 0')
    session.execute('print("once")')
    with pytest.raises(WysnaError):
        session.execute('value = value + 1; print(1 / 0)')
    session.execute('print(value)')
    assert output.getvalue() == 'once\n1\n'


def test_generic_specializations_structs_and_main():
    output = StringIO()
    session = ReplSession(stdout=output)
    session.execute('fn identity[T](value: T) -> T { return value }')
    session.execute('print(identity(3))')
    session.execute('print(identity("text"))')
    session.execute('struct Box[T] { value: T }')
    session.execute('let box = Box[int] { value: 9 }')
    session.execute('print(box.value)')
    session.execute('fn main() { print("main") }')
    session.execute('main()')
    assert output.getvalue() == '3\ntext\n9\nmain\n'


def test_import_initialization_runs_once_and_changes_require_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    module = tmp_path / 'library.wysna'
    module.write_text('print("initialized")\npub fn value() -> int { return 12 }\n', encoding='utf-8')
    output = StringIO()
    session = ReplSession(stdout=output)
    session.execute('import "library.wysna" as library')
    session.execute('print(library.value())')
    session.execute('print(library.value())')
    assert output.getvalue() == 'initialized\n12\n12\n'
    module.write_text('print("changed")\npub fn value() -> int { return 13 }\n', encoding='utf-8')
    with pytest.raises(WysnaError) as failure:
        session.execute('print(library.value())')
    assert failure.value.code == 'W1003'
    assert output.getvalue() == 'initialized\n12\n12\n'


def test_sessions_are_independent():
    first = ReplSession(stdout=StringIO())
    second = ReplSession(stdout=StringIO())
    first.execute('let value = 42')
    with pytest.raises(WysnaError) as failure:
        second.execute('print(value)')
    assert failure.value.code == 'W1001'


def test_enum_variant_shadowing_survives_later_inputs():
    output = StringIO()
    session = ReplSession(stdout=output)
    session.execute('enum Color { Red, Blue }')
    session.execute('let Red = 42')
    session.execute('print(Red)')
    session.execute('print(Red)')
    assert output.getvalue() == '42\n42\n'


def test_failed_shadow_restores_original_binding():
    session = ReplSession(stdout=StringIO())
    session.execute('enum Color { Red, Blue }')
    environment = session.interpreter.global_env
    original = environment.bindings['Red']
    with pytest.raises(WysnaError):
        session.execute('let Red = 42; print(1 / 0)')
    assert environment.bindings['Red'] is original
    session.execute('print(Red)')


def test_declaration_rollback_preserves_prior_mutations_and_mutability():
    environment = Environment()
    environment.declare('value', WysnaInt(1), is_mut=True)
    environment.begin_declaration_journal()
    environment.assign('value', WysnaInt(2))
    environment.declare('value', WysnaInt(3))
    environment.declare('new', WysnaInt(4), is_mut=True)
    environment.end_declaration_journal(rollback=True)
    assert environment.lookup('value').value == 2
    assert 'value' in environment.mutables
    assert not environment.exists('new') and 'new' not in environment.mutables


def test_cli_survives_invalid_input_and_retains_previous_state():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / 'main.py'), 'repl'],
        input='let value = 42\nprint(value)\nlet bad: int = "text"\nprint(value)\nexit\n',
        text=True, capture_output=True, cwd=root, timeout=60,
    )
    assert result.returncode == 0
    assert result.stdout.count('42') >= 2
    assert 'W2001' in result.stdout
    assert 'Traceback' not in result.stdout + result.stderr
