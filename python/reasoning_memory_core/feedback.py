"""feedback for the DSH reasoning-memory core."""

from __future__ import annotations

from typing import Any




def _record_feedback(request: dict[str, Any]) -> dict[str, Any]:
    items = request.get("memories")
    rating = request.get("rating")
    if not isinstance(items, list) or rating not in ("helpful", "harmful", "neutral"):
        raise ValueError("feedback requires memory records and helpful/harmful/neutral rating")
    updates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        feedback = item.get("feedback") if isinstance(item.get("feedback"), dict) else {}
        counts = {
            key: max(0, int(feedback.get(key, 0)))
            for key in ("helpful", "harmful", "neutral")
        }
        counts[rating] += 1
        updates.append({"memoryId": str(item.get("memoryId", "")), "feedback": counts})
    return {"updates": updates}
