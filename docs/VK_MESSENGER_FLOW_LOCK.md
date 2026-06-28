# VK messenger flow lock

Do not globally rewrite this flow without updating the regression tests.

Locked production contract:

1. VK demo audio flow is:
   `demo -> demo_work/demo_home -> pre score -> native VK audio -> done -> post score -> graph/post actions`.
2. After `done`, any score from the second scale must become `post_score_received`.
   It must not create a new `pre_score_received` and must not send another audio.
3. VK `.opus` / `.ogg` audio uses native `audio_message`.
   It must not fall back to `doc` for native audio files.
4. VK payment messages must not show raw YooKassa checkout URLs in the message body.
   URLs are allowed only as `open_link` buttons.
5. VK regular keyboards that are not inline must be `one_time=true`,
   so score/settings panels do not stick to the bottom of the screen.
6. Progress charts must not be sent through `send_audio_file`; VK progress charts must use `send_image_file` / VK `photos.*` upload.

Relevant lock tests:
- `tests/test_vk_user_journey_e2e.py`
- `tests/test_vk_callback_buttons_contract.py`
- `tests/test_vk_keyboard_parity.py`
- `tests/test_vk_native_audio_preparation.py`
