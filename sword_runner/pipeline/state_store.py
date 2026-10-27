from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import ensure_dir, now_iso, read_json, write_json_atomic


@dataclass
class StateStore:
    run_dir: Path

    @property
    def path(self) -> Path:
        return self.run_dir / "state.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "steps": {},
                "artifacts": {},
            }
        return read_json(self.path)

    def save(self, state: dict[str, Any]) -> None:
        ensure_dir(self.run_dir)
        state["updated_at"] = now_iso()
        write_json_atomic(self.path, state)

    def mark_step(
        self,
        state: dict[str, Any],
        step: str,
        status: str,
        **payload: Any,
    ) -> None:
        state.setdefault("steps", {})[step] = {
            "status": status,
            "updated_at": now_iso(),
            **payload,
        }
        if status in {"done", "skipped", "queued"} and (state.get("blocked") or {}).get("step") == step:
            state.pop("blocked", None)
        self.save(state)

    def set_artifact(self, state: dict[str, Any], name: str, path: Path | str) -> None:
        state.setdefault("artifacts", {})[name] = str(path)
        self.save(state)

    def mark_blocked(self, state: dict[str, Any], step: str, error: Exception | str, **payload: Any) -> None:
        state["blocked"] = {
            "step": step,
            "error": str(error),
            "error_type": type(error).__name__ if isinstance(error, Exception) else "",
            "updated_at": now_iso(),
            **payload,
        }
        self.save(state)
