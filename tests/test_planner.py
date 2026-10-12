import pytest

from foreman.core.models import Project
from foreman.planner import (
    PlanError,
    find_cycle,
    parse_chat,
    parse_tasks,
    render_tasks_md,
    topo_order,
    writable,
)

P = Project(name="todo", repo_url="x", feature="001-initial")


def test_parse_chat_extracts_files_and_command():
    text = """I drafted the spec.

<<<FILE specs/001-initial/spec.md
# Spec
- FR-1.1 Add todos.
>>>

<<<FILE tests/acceptance/test_todos.py
def test_add(): assert False
>>>

<<<ACCEPTANCE_CMD
pytest -q tests/acceptance
>>>

What storage do you want?"""
    out = parse_chat(P, text)
    assert out.files == {
        "specs/001-initial/spec.md": "# Spec\n- FR-1.1 Add todos.",
        "tests/acceptance/test_todos.py": "def test_add(): assert False",
    }
    assert out.acceptance_cmd == "pytest -q tests/acceptance"
    assert "[wrote specs/001-initial/spec.md]" in out.message
    assert "What storage do you want?" in out.message
    assert "FR-1.1" not in out.message  # file bodies are not repeated in the chat


def test_parse_chat_rejects_writes_outside_allowlist():
    text = "<<<FILE src/app.py\nx\n>>>\n<<<FILE ../../etc/passwd\nx\n>>>\n"
    out = parse_chat(P, text)
    assert out.files == {}
    assert out.rejected == ["src/app.py", "../../etc/passwd"]


def test_writable():
    assert writable(P, "specs/001-initial/plan.md")
    assert writable(P, "tests/acceptance/sub/test_x.py")
    assert writable(P, ".foreman/Dockerfile")
    assert not writable(P, "specs/001-initial/nested/x.md")
    assert not writable(P, "specs/other/spec.md")
    assert not writable(P, "src/main.py")


TASKS = """Here you go:
```json
[
  {"key": "T2", "title": "API", "allowed_files": ["src/api.py"], "deps": ["T1"],
   "validation_cmd": "pytest -q tests/test_api.py", "req_refs": "FR-1.2"},
  {"key": "T1", "title": "Store", "allowed_files": ["src/store.py"], "deps": [],
   "validation_cmd": "pytest -q tests/test_store.py"}
]
```"""


def test_parse_tasks_and_order():
    tasks = parse_tasks(TASKS)
    assert [t.key for t in topo_order(tasks)] == ["T1", "T2"]
    assert tasks[0].req_refs == ["FR-1.2"]  # a bare string is accepted as a list


@pytest.mark.parametrize(
    "text, problem",
    [
        ("no json here", "no JSON array"),
        ("[1, 2", "no JSON array"),
        ("[]", "non-empty"),
        ('[{"key": "A", "title": "a", "deps": ["B"]}]', "unknown deps B"),
        ('[{"key": "A", "title": "a"}, {"key": "A", "title": "b"}]', "duplicate keys"),
        (
            '[{"key": "A", "title": "a", "deps": ["B"]}, {"key": "B", "title": "b", '
            '"deps": ["A"]}]',
            "dependency cycle",
        ),
        ('[{"key": "A"}]', "missing title"),
    ],
)
def test_parse_tasks_problems(text, problem):
    with pytest.raises(PlanError, match=problem):
        parse_tasks(text)


def test_find_cycle():
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c", "a"]
    assert find_cycle({"a": ["b"], "b": []}) == []


def test_render_tasks_md():
    md = render_tasks_md(
        P,
        [
            {
                "key": "T1",
                "title": "Store",
                "description": "d",
                "req_refs": ["FR-1.1"],
                "deps": [],
                "allowed_files": ["src/store.py"],
                "context_files": [],
                "validation_cmd": "pytest",
                "todos": ["x"],
                "violations": ["too big"],
            }
        ],
    )
    assert "## T1: Store" in md and "`src/store.py`" in md and "- [ ] x" in md
    assert "> Size check: too big" in md
