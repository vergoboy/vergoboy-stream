from __future__ import annotations

from archive_challenge import ChallengeInspection, ChallengeHandler, ManualOnlyChallengeHandler, _normalize
from archive_question import solve_security_question


class AutomaticChallengeHandler(ChallengeHandler):
    def __init__(self) -> None:
        self._base = ManualOnlyChallengeHandler()

    def inspect(self, html: str) -> ChallengeInspection:
        return self._base.inspect(html)

    def solve(self, html: str) -> dict[str, str]:
        answer = solve_security_question(html or "")
        if not answer:
            return {}
        return {"secureq_ans": answer}
    def can_auto_solve(self, inspection) -> bool:
        from archive_challenge import ChallengeInspection
        return inspection.present and inspection.kind == "security_question"
