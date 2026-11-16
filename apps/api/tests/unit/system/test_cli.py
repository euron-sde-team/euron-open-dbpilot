"""The ``dbpilot`` command registers every subcommand the deployment runs (compose and the
ECS task definitions call ``serve``, ``worker``, ``migrate``, ``seed`` and ``mockai``):
a ``command: ["worker"]`` with no matching subcommand would crash-loop the container."""

from __future__ import annotations

import pytest

from dbpilot import cli


@pytest.mark.parametrize(
    ("argv", "fn"),
    [
        (["serve"], cli._serve),
        (["worker"], cli._worker),
        (["migrate"], cli._migrate),
        (["revision", "-m", "x"], cli._revision),
        (["create-admin", "--email", "a@b.test"], cli._admin),
        (["seed"], cli._seed),
        (["seed-demo"], cli._seed),
        (["openapi"], cli._openapi),
        (["mockai"], cli._mockai),
    ],
)
def test_every_subcommand_is_registered(argv: list[str], fn: object) -> None:
    args = cli.build_parser().parse_args(argv)
    assert args.fn is fn


def test_unknown_subcommands_fail_loudly() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["scheduler"])


def test_ports_default_per_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API takes PORT (through Settings, 8080 by default); the mock model provider keeps
    its own 5650, so both run side by side; --port overrides either."""
    seen: dict[str, int] = {}
    real = cli.build_parser

    def patched() -> object:
        parser = real()
        choices = parser._subparsers._group_actions[0].choices  # type: ignore[union-attr]
        choices["serve"].set_defaults(fn=lambda a: seen.__setitem__("serve", a.port))
        choices["mockai"].set_defaults(fn=lambda a: seen.__setitem__("mockai", a.port))
        return parser

    monkeypatch.setattr(cli, "build_parser", patched)
    cli.main(["mockai"])
    cli.main(["serve"])
    assert seen == {"mockai": 5650, "serve": 8080}
    cli.main(["serve", "--port", "5999"])
    assert seen["serve"] == 5999
