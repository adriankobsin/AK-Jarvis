"""People Jarvis serves. The owner gets the full assistant; children get a separate tutor
profile with their own memory/knowledge database and a restricted, safe tool set."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .config import Config

CHILD_TOOLS = {
    # basics
    "get_datetime", "open_url", "get_weather",
    # own memory & knowledge (separate database from the owner's)
    "remember", "recall", "update_memory", "forget",
    "search_knowledge", "read_knowledge_source", "teach_note", "list_knowledge",
    # homework planner
    "add_task", "list_tasks", "complete_task", "delete_task",
    "set_reminder", "list_reminders", "cancel_reminder",
    # learning
    "ask_expert",
    # kid-specific
    "save_project_file", "read_project_file", "list_project_files", "run_project",
    "flag_for_parent",
}


@dataclass
class Profile:
    id: str
    name: str
    role: str = "owner"              # owner | child
    age: int | None = None
    interests: list[str] = field(default_factory=list)
    school_level: str = ""
    notes: str = ""

    @property
    def is_child(self) -> bool:
        return self.role == "child"


def load_profiles(cfg) -> dict[str, Profile]:
    a = cfg["assistant"]
    profiles = {"owner": Profile("owner", a.get("user_name") or "the user", "owner")}
    for p in cfg.get("profiles") or []:
        prof = Profile(
            id=str(p["id"]).lower(), name=p.get("name", p["id"]), role=p.get("role", "child"),
            age=p.get("age"), interests=p.get("interests", []), school_level=p.get("school_level", ""),
            notes=p.get("notes", ""))
        profiles[prof.id] = prof
    return profiles


def profile_config(cfg, profile: Profile) -> Config:
    """Config for one person: children get their own data directory and no device access."""
    c = Config(copy.deepcopy(dict(cfg)))
    c["profile"] = {"id": profile.id, "name": profile.name, "role": profile.role,
                    "age": profile.age, "interests": profile.interests,
                    "school_level": profile.school_level, "notes": profile.notes}
    c["owner_name"] = cfg["assistant"].get("user_name") or "their parent"
    if profile.is_child:
        base = c["assistant"]["data_dir"].rstrip("/")
        c["assistant"]["data_dir"] = f"{base}/profiles/{profile.id}"
        c["assistant"]["user_name"] = profile.name
        c["assistant"]["honorific"] = profile.name
        c["knowledge"]["watch_folders"] = []
        c["briefing"]["enabled"] = False
        c["home_assistant"]["url"] = ""
        c["devices"] = {"http": [], "tcp": [], "wol": []}
        c["safety"]["confirm_shell"] = True
    return c


def find_profile(profiles: dict[str, Profile], spoken: str) -> Profile | None:
    s = spoken.strip().lower()
    for p in profiles.values():
        if s in (p.id, p.name.lower()) or (p.role == "owner" and s in ("owner", "dad", "daddy", "father")):
            return p
    return None
