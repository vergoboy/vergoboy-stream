"""Challenge handler that answers only the supported security-question formats."""
from __future__ import annotations

from archive_challenge import ChallengeSolution, ManualOnlyChallengeHandler, UnsolvableChallenge
from archive_login_form import LoginForm
from archive_question import UnsupportedQuestion, solve_question_text


class AutomaticChallengeHandler(ManualOnlyChallengeHandler):
    """Detection is inherited; ``solve`` is bound to the form it was given."""

    def solve(self, form: LoginForm) -> ChallengeSolution:
        if not form.secureq_key:
            raise UnsolvableChallenge("no_key")
        if not form.question:
            raise UnsolvableChallenge("no_question")
        if not form.answer_field:
            raise UnsolvableChallenge("no_answer_field")
        try:
            solved = solve_question_text(form.question)
        except UnsupportedQuestion as exc:
            raise UnsolvableChallenge(exc.reason) from None
        return ChallengeSolution(form.secureq_key, form.question, form.answer_field, solved.answer, solved.kind)
