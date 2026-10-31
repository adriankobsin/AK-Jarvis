"""Hermes profile installer, skills and starter memory."""
import importlib.util
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("hermes_profile", REPO / "deploy" / "hermes_profile.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)


def test_profile_install_merges_and_respects_learned_state(tmp_path, monkeypatch):
    for k in ("OPENAI_API_KEY", "GITHUB_PAT", "XERO_CLIENT_ID", "HA_URL", "HA_TOKEN", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WAVE_FILES_DIR", str(tmp_path / "files"))
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".env").write_text("JARVIS_MCP_TOKEN=t0k\nGITHUB_PAT=ghp_x\nOPENAI_API_KEY=sk-o\nJARVIS_SITE_HOME_TOKEN=s1\n")
    (home / "config.yaml").write_text(yaml.safe_dump({
        "model": {"provider": "openrouter", "default": "x"},
        "telegram": {"require_mention": True},
        "mcp_servers": {"my_own": {"url": "https://example.com/mcp"}}}))
    (home / "SOUL.md").write_text("default persona")

    rep = hp.install(home, "cloud", {"home": "http://home-pc:8788/mcp"}, "http://127.0.0.1:8788/mcp")
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["model"] == {"provider": "anthropic", "default": "claude-opus-5-5"}
    assert cfg["telegram"] == {"require_mention": True}                  # the user's own settings stay
    assert cfg["mcp_servers"]["my_own"]["url"] == "https://example.com/mcp"
    srv = cfg["mcp_servers"]
    assert srv["jarvis"]["enabled"] and srv["jarvis"]["headers"]["Authorization"] == "Bearer ${JARVIS_MCP_TOKEN}"
    assert srv["github"]["enabled"] and not srv["xero"]["enabled"] and not srv["homeassistant"]["enabled"]
    assert not srv["ms365"]["enabled"]                                   # needs the device login first
    site = srv["jarvis_home"]
    assert site["enabled"] and site["url"] == "http://home-pc:8788/mcp" and "integration_write" in site["tools"]["include"]
    assert "run_shell_command" not in site["tools"]["include"]
    assert cfg["fallback_providers"][0]["base_url"] == "https://api.openai.com/v1"
    assert cfg["delegation"]["api_key"] == "${OPENAI_API_KEY}"
    assert cfg["tts"]["edge"]["voice"] == "en-GB-RyanNeural" and cfg["terminal"]["backend"] == "docker"
    assert "xero" in rep["disabled"] and "jarvis" in rep["enabled"]
    assert list(home.glob("config.yaml.bak-*"))                        # old config backed up

    soul = (home / "SOUL.md").read_text()
    assert soul.startswith("# Jarvis") and hp.MARK in soul and list(home.glob("SOUL.md.bak-*"))
    assert (home / "memories" / "USER.md").read_text().startswith("Adrian")
    assert {"teach-me", "email-calendar", "quotation", "building-control"} <= set(rep["skills"])

    # second run: what Hermes learned is never overwritten; user-locked skills are left alone
    (home / "memories" / "USER.md").write_text("Adrian prefers tea.")
    locked = home / "skills" / "wave" / "quotation" / "SKILL.md"
    locked.write_text("---\nname: quotation\ndescription: mine\nuser-locked: true\n---\nMy own way.")
    rep2 = hp.install(home, "cloud", {}, "http://127.0.0.1:8788/mcp")
    assert (home / "memories" / "USER.md").read_text() == "Adrian prefers tea."
    assert "memories_seeded" not in rep2 and locked.read_text().endswith("My own way.")
    assert len(list(home.glob("SOUL.md.bak-*"))) == 1                  # our own SOUL isn't re-backed-up


def test_no_openai_means_no_gpt_routing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = hp.build_config("all", {"ELEVENLABS_API_KEY": "e"}, {}, "http://127.0.0.1:8788/mcp")
    assert "fallback_providers" not in cfg and "delegation" not in cfg
    assert cfg["tts"]["provider"] == "elevenlabs" and cfg["tts"]["elevenlabs"]["voice_id"] == "onwK4e9ZLuTAKqWW03F9"


def test_skills_are_valid_agent_skills():
    skills = sorted((REPO / "hermes" / "skills" / "wave").glob("*/SKILL.md"))
    assert len(skills) >= 12
    for p in skills:
        text = p.read_text()
        m = re.match(r"^---\n(.*?)\n---\n(.+)", text, re.S)
        assert m, p
        meta = yaml.safe_load(m.group(1))
        assert meta["name"] == p.parent.name
        assert 20 < len(meta["description"]) <= 1024
        assert m.group(2).strip().startswith("#")


def test_starter_memory_fits_hermes_limits():
    mem = REPO / "hermes" / "memories"
    user, memory = (mem / "USER.md").read_text(), (mem / "MEMORY.md").read_text()
    assert len(user) <= 1375 * 0.7 and len(memory) <= 2200 * 0.7   # leave room to learn
    assert "§" in user and "§" in memory
    assert len((REPO / "hermes" / "SOUL.md").read_text().split()) < 2000


def test_free_brains_chain_after_openai():
    env = {"OPENAI_API_KEY": "o", "GROQ_API_KEY": "g", "GEMINI_API_KEY": "m", "GROQ_MODEL": "llama-x"}
    cfg = hp.build_config("cloud", env, {}, "http://127.0.0.1:8788/mcp")
    chain = cfg["fallback_providers"]
    assert [c["base_url"] for c in chain] == ["https://api.openai.com/v1",
                                              "https://generativelanguage.googleapis.com/v1beta/openai",
                                              "https://api.groq.com/openai/v1"]
    assert chain[2]["model"] == "llama-x" and chain[2]["key_env"] == "GROQ_API_KEY" and "label" not in chain[2]
    assert cfg["auxiliary"]["web_extract"]["base_url"].startswith("https://generativelanguage")
    assert cfg["auxiliary"]["compression"]["provider"] == "anthropic"     # private chat stays on Claude
    assert "fallback_providers" not in hp.build_config("cloud", {}, {}, "http://x/mcp")
