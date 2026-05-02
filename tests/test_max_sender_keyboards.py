from runtime.messenger_senders import MaxBotSender


def _button_texts(attachment):
    return [button['text'] for row in attachment['payload']['buttons'] for button in row]


def test_max_main_keyboard_matches_telegram_main_labels():
    attachment = MaxBotSender._keyboard_for_text(
        'Главное меню\n\nВыберите маршрут',
        external_user_id='42',
    )

    assert attachment is not None
    assert attachment['type'] == 'inline_keyboard'
    assert _button_texts(attachment) == [
        '🌿 Попробовать бесплатно',
        '🔐 Полный маршрут',
        '💳 Тарифы',
        '🎁 Подарить',
        '📈 Мой прогресс',
        '🧠 Настройки',
        '📣 Посоветовать',
        '🌤 Погода',
    ]


def test_max_demo_keyboard_matches_telegram_demo_kind_labels():
    attachment = MaxBotSender._keyboard_for_text(
        '🌿 Бесплатная практика\n\nВыберите короткий маршрут',
        external_user_id='42',
    )

    assert attachment is not None
    assert _button_texts(attachment) == [
        '🚗 Практика на утро / дорогу',
        '🌙 Практика на вечер / домой',
        '⬅️ Назад',
    ]


def test_max_score_keyboard_matches_telegram_mood_scale_range():
    attachment = MaxBotSender._keyboard_for_text(
        'Шкала оценки: -10 — стало сильно хуже. Нажмите число ниже от -10 до +10.',
        external_user_id='42',
    )

    assert attachment is not None
    texts = _button_texts(attachment)
    assert texts[0] == '-10'
    assert texts[-2:] == ['+10', '⬅️ Меню']
    assert len([text for text in texts if text not in {'⬅️ Меню'}]) == 21


def test_max_full_route_keyboard_has_same_continue_controls_as_telegram_context():
    attachment = MaxBotSender._keyboard_for_text(
        '🔐 Полный маршрут\n\nНажмите «🎧 Получить аудио»',
        external_user_id='42',
    )

    assert attachment is not None
    assert _button_texts(attachment) == ['🎧 Получить аудио', '✅ Прослушал', '⬅️ Меню']


def test_max_payment_buttons_are_links_not_dead_text_buttons():
    attachment = MaxBotSender._keyboard_for_text(
        'Главное меню',
        external_user_id='42',
    )

    assert attachment is not None
    buttons = [button for row in attachment['payload']['buttons'] for button in row]
    pay_button = next(button for button in buttons if button['text'] == '💳 Тарифы')
    gift_button = next(button for button in buttons if button['text'] == '🎁 Подарить')

    assert pay_button['type'] == 'link'
    assert 'kind=subscription' in pay_button['url']
    assert gift_button['type'] == 'link'
    assert 'kind=gift' in gift_button['url']
