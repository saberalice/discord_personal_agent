import json

from discord_personal_agent.preview import main


def test_demo_exports_readable_preview_and_complete_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["--demo"])
    folders = list((tmp_path / "local-exports").iterdir())
    assert len(folders) == 1
    text = (folders[0] / "prompt-preview.txt").read_text()
    data = json.loads((folders[0] / "sources.json").read_text())
    assert "Owen (15:03) : 今天討論部署方式" in text
    assert "[附件：未讀取內容]" in text
    assert data["selected_count"] == 4
    assert data["system_count"] == 1
    assert len(data["messages"]) == 3
    assert data["messages"][1]["author_id"] == "123"
    assert data["messages"][1]["source_url"].endswith("/2")
    assert "今天討論部署方式" not in capsys.readouterr().out
    assert (folders[0] / "sources.json").stat().st_mode & 0o777 == 0o600
