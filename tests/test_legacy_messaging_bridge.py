from interfaces.messaging.legacy_bridge import messenger_reply_to_canonical
from services.messenger.text_ui import MessengerReply


def _texts(response):
    return [button.text for row in response.buttons for button in row]


def test_main_menu_reply_maps_to_canonical_buttons():
    response = messenger_reply_to_canonical(MessengerReply(text='Главное меню\n\nВыберите маршрут'))

    assert response.text.startswith('Главное меню')
    assert _texts(response) == [
        '🌿 Попробовать бесплатно',
        '🔐 Полный маршрут',
        '💳 Тарифы',
        '🎁 Подарить',
        '📈 Мой прогресс',
        '🧠 Настройки',
        '📣 Посоветовать',
        '🌤 Погода',
    ]


def test_score_reply_maps_to_canonical_scale_buttons():
    response = messenger_reply_to_canonical(
        MessengerReply(text='Оцените состояние сейчас. Шкала оценки: -10 — +10', meta={'vk_keyboard': 'score_scale'})
    )

    assert _texts(response)[0] == '-10'
    assert _texts(response)[-2:] == ['+10', '⬅️ Меню']
    assert len([text for text in _texts(response) if text != '⬅️ Меню']) == 21


def test_full_route_reply_maps_to_continue_controls():
    response = messenger_reply_to_canonical(MessengerReply(text='🔐 Полный маршрут\n\nПродолжить'))

    assert _texts(response) == ['🎧 Получить аудио', '✅ Прослушал', '⬅️ Меню']


def test_unknown_text_reply_stays_plain_canonical_response():
    response = messenger_reply_to_canonical(MessengerReply(text='Просто текст'))

    assert response.text == 'Просто текст'
    assert response.buttons == ()
