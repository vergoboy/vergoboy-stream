from archive_challenge import ManualOnlyChallengeHandler


def test_manual_handler_classifies_security_question_without_answering_it():
    result = ManualOnlyChallengeHandler().inspect("سوال امنیتی: ۸ ضرب در ۵ چند می‌شود؟ <input name='secureq_ans'>")
    assert result.present is True
    assert result.kind == "security_question"


def test_manual_handler_classifies_captcha_markup():
    result = ManualOnlyChallengeHandler().inspect('<div class="g-recaptcha"></div>')
    assert result.present is True
    assert result.kind == "captcha"
