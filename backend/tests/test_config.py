from app.core.config import load_env_file


def test_env_file_is_exported_to_process_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("LINKCHAT_TEST_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('LINKCHAT_TEST_KEY="abc 123"\n')
    load_env_file(env)
    import os

    assert os.environ["LINKCHAT_TEST_KEY"] == "abc 123"
    monkeypatch.delenv("LINKCHAT_TEST_KEY")


def test_missing_env_file_is_ignored(tmp_path):
    load_env_file(tmp_path / "nope.env")
