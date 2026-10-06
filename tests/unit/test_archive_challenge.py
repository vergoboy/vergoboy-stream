from archive_challenge import ManualOnlyChallengeHandler


def test_manual_handler_classifies_security_question_without_answering_it():
    result = ManualOnlyChallengeHandler().inspect("سوال امنیتی: ۸ ضرب در ۵ چند می‌شود؟ <input name='secureq_ans'>")
    assert result.present is True
    assert result.kind == "security_question"


def test_manual_handler_classifies_captcha_markup():
    result = ManualOnlyChallengeHandler().inspect('<div class="g-recaptcha"></div>')
    assert result.present is True
    assert result.kind == "captcha"


def test_popup_with_inputs_does_not_hide_the_real_login_challenge():
    """Regression: <input> inside a popup has no end tag and must not skew depth."""
    from tests.unit.fake_digimoviez import POPUP
    page = (POPUP + '<form class="dashboard_form"><input type="password" name="password">'
            '<input type="hidden" name="secureq_key" value="1"><span>سوال امنیتی: ۶ در ۸</span>'
            '<input name="secureq_ans"></form>')
    assert ManualOnlyChallengeHandler().inspect(page).kind == "security_question"
    assert ManualOnlyChallengeHandler().inspect(POPUP).present is False
