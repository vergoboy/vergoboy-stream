"""Challenge detection, question/key pairing and payload construction (pure)."""
from __future__ import annotations

from pathlib import Path

import pytest

from archive_challenge import ChallengeSolution
from archive_challenge_auto import AutomaticChallengeHandler
from archive_challenge import UnsolvableChallenge
from archive_login_form import ChallengeStaleError, LoginFormError, build_login_payload, parse_login_form
from tests.unit.fake_digimoviez import POPUP

FIXTURE = Path(__file__).parents[1] / "fixtures" / "archive" / "digimoviez_login_challenge.html"


def real_page() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# --- detection ----------------------------------------------------------------
def test_challenge_detected_on_real_login_fixture():
    inspection = AutomaticChallengeHandler().inspect(real_page())
    assert inspection.present and inspection.kind == "security_question"


def test_dormant_popup_alone_is_not_a_challenge():
    page = "<html><body>" + POPUP + "<p>public page</p></body></html>"
    assert AutomaticChallengeHandler().inspect(page).present is False
    assert parse_login_form(page) is None


def test_captcha_is_detected_and_never_solved():
    handler = AutomaticChallengeHandler()
    page = '<form><input type="password" name="password"><div class="g-recaptcha" data-sitekey="x"></div></form>'
    assert handler.inspect(page).kind == "captcha"


# --- question/key pairing -----------------------------------------------------
def test_real_fixture_pairs_key_with_its_own_question():
    form = parse_login_form(real_page())
    assert form.secureq_key == "101"
    assert form.question == "پانصد بعلاوۀ چهل"
    assert form.answer_field == "secureq_ans"
    solution = AutomaticChallengeHandler().solve(form)
    assert (solution.key, solution.answer) == ("101", "540")


def test_popup_decoy_key_and_question_are_never_used():
    page = "<html><body>" + POPUP + real_page().split("<body>", 1)[1]
    form = parse_login_form(page)
    assert form.secureq_key == "101"                 # not the popup's 999
    assert "popup_only_field" not in form.hidden_fields
    assert AutomaticChallengeHandler().solve(form).answer == "540"   # not 1+1


def test_question_split_across_text_nodes_is_still_paired():
    html = ('<form><input type="password" name="password"><div><input type="hidden" name="secureq_key" value="7">'
            '<span>سوال امنیتی:</span> <b>۶ در ۸</b></div><input name="secureq_ans"></form>')
    form = parse_login_form(html)
    assert (form.secureq_key, form.question) == ("7", "۶ در ۸")


def test_two_questions_for_one_key_is_ambiguous_and_refused():
    html = ('<form><input type="password" name="password"><div><input type="hidden" name="secureq_key" value="7">'
            '<span>سوال امنیتی: ۱ بعلاوه ۱</span><span>سوال امنیتی: ۲ بعلاوه ۲</span></div>'
            '<input name="secureq_ans"></form>')
    form = parse_login_form(html)
    assert form.question is None
    with pytest.raises(UnsolvableChallenge) as exc:
        AutomaticChallengeHandler().solve(form)
    assert exc.value.reason == "no_question"


@pytest.mark.parametrize("html,reason", [
    ('<form><input type="password" name="password"><span>سوال امنیتی: ۶ در ۸</span><input name="secureq_ans"></form>', "no_key"),
    ('<form><input type="password" name="password"><input type="hidden" name="secureq_key" value="1"><input name="secureq_ans"></form>', "no_question"),
    ('<form><input type="password" name="password"><input type="hidden" name="secureq_key" value="1"><span>سوال امنیتی: ۶ در ۸</span></form>', "no_answer_field"),
    ('<form><input type="password" name="password"><input type="hidden" name="secureq_key" value="1"><span>سوال امنیتی: رنگ آسمان</span><input name="secureq_ans"></form>', "unsupported_question"),
])
def test_incomplete_or_unknown_challenges_are_unsolvable(html, reason):
    with pytest.raises(UnsolvableChallenge) as exc:
        AutomaticChallengeHandler().solve(parse_login_form(html))
    assert exc.value.reason == reason


# --- hidden fields / payload --------------------------------------------------
def test_hidden_fields_and_submit_button_are_preserved_verbatim():
    form = parse_login_form(real_page())
    payload = build_login_payload(form, username_field="username", username="u", password_field="password",
                                  password="p", solution=AutomaticChallengeHandler().solve(form))
    assert payload == {"login_security_str": "01af9f895c", "_wp_http_referer": "/account/login/",
                       "secureq_key": "101", "loginkon": "", "username": "u", "password": "p", "secureq_ans": "540"}


def test_qr_button_is_not_treated_as_the_submit_button():
    assert parse_login_form(real_page()).submit == ("loginkon", "")


def test_stale_solution_cannot_be_combined_with_a_different_form():
    old = parse_login_form(real_page())
    solution = AutomaticChallengeHandler().solve(old)
    new = parse_login_form(real_page().replace('value="101"', 'value="202"'))
    with pytest.raises(ChallengeStaleError):
        build_login_payload(new, username_field="username", username="u", password_field="password",
                            password="p", solution=solution)
    changed_question = parse_login_form(real_page().replace("چهل", "سی"))
    with pytest.raises(ChallengeStaleError):
        build_login_payload(changed_question, username_field="username", username="u", password_field="password",
                            password="p", solution=solution)


def test_solution_repr_hides_the_answer():
    assert "540" not in repr(ChallengeSolution("1", "q", "secureq_ans", "540"))


# --- form action / malformed markup ------------------------------------------
def test_cross_origin_form_action_is_refused():
    form = parse_login_form('<form action="https://evil.test/steal"><input type="password" name="password"></form>')
    with pytest.raises(LoginFormError):
        form.resolve_action("https://digimoviez.test/account/login/", "https://digimoviez.test/account/login/")


def test_relative_form_action_resolves_on_same_origin():
    form = parse_login_form('<form action="/account/login/"><input type="password" name="password"></form>')
    assert form.resolve_action("https://digimoviez.test/x", "https://digimoviez.test/account/login/") == \
        "https://digimoviez.test/account/login/"


@pytest.mark.parametrize("html", ["", "<<<>>>", "<form><input", "<div><form></div></form><input type=password>", "\x00\x01"])
def test_malformed_html_never_raises(html):
    parse_login_form(html)  # must not raise; result may be None
