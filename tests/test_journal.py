from jarvis.store import Store
from jarvis.tools.base import ToolContext, build_registry


def test_journal_shared_between_models(tmp_path):
    from jarvis.config import load_config
    cfg = load_config(overrides={"assistant": {"data_dir": str(tmp_path)}})
    store = Store(tmp_path / "j.db")
    reg = build_registry(cfg)
    ctx = ToolContext(config=cfg, store=store)
    t = reg._tools
    t["journal_write"].func(ctx, kind="code_change", title="Phone gateway accepts secret in URL",
                            body="jarvis/phone.py _authorized(); commit 3be14ba", project="Jarvis",
                            model="claude-opus-5-5", channel="cli")
    t["journal_write"].func(ctx, kind="handoff", title="Chrome tunnel not connecting yet",
                            body="Next: run ssh -R by hand and read the error", project="jarvis", model="gemini-flash")
    found = t["journal_search"].func(ctx, query="secret gateway")
    assert "accepts secret in URL" in found and "claude-opus-5-5" in found
    recent = t["journal_recent"].func(ctx, project="jarvis")
    assert recent.index("Chrome tunnel") < recent.index("Phone gateway") and "jarvis (2" in recent
    store.log("user", "remember the anchor camera spec")
    assert "anchor camera" in t["journal_search"].func(ctx, query="anchor")
