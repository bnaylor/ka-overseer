import json

import configure


def test_flags_merge_into_existing_config(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"repo": "a/b", "gating_bot": "bot", "round_cap": 6}))
    assert configure.main(["--repo", "acme/widgets", "--round-cap", "4"], path=p) == 0
    cfg = json.loads(p.read_text())
    assert cfg["repo"] == "acme/widgets"
    assert cfg["round_cap"] == 4
    assert cfg["gating_bot"] == "bot"          # untouched flags keep their value


def test_empty_advisory_bot_is_allowed(tmp_path):
    p = tmp_path / "config.json"
    assert configure.main(["--repo", "acme/widgets", "--advisory-bot", ""], path=p) == 0
    assert json.loads(p.read_text())["advisory_bot"] == ""


def test_bad_repo_and_bad_regex_are_refused_without_writing(tmp_path):
    p = tmp_path / "config.json"
    assert configure.main(["--repo", "not a repo"], path=p) == 2
    assert configure.main(["--repo", "a/b", "--session-pattern", "("], path=p) == 2
    assert not p.exists()


def test_show_does_not_write(tmp_path, capsys):
    p = tmp_path / "config.json"
    assert configure.main(["--repo", "acme/widgets", "--show"], path=p) == 0
    assert not p.exists()
    assert '"acme/widgets"' in capsys.readouterr().out


def test_cwd_prefixes_expand_home(tmp_path):
    p = tmp_path / "config.json"
    configure.main(["--repo", "a/b", "--cwd-prefix", "~/src/widgets"], path=p)
    assert not json.loads(p.read_text())["cwd_prefixes"][0].startswith("~")
