from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from keyboards.inline import kb_main,kb_mood_scale
from runtime import messenger_max_ui as max_ui
from runtime import messenger_vk_ui as vk_ui

def tg(m):
    return [[(str(b.text),str(b.callback_data or '')) for b in r] for r in m.inline_keyboard]

def mx(a):
    return [[(str(b['text']),str((b.get('payload') or {}).get('command') or '')) for b in r] for r in a['payload']['buttons']]

def vk(s):
    out=[]
    for r in json.loads(s)['buttons']:
        row=[]
        for b in r:
            p=json.loads(b['action'].get('payload') or '{}')
            row.append((str(b['action']['label']),str(p.get('command') or '')))
        out.append(row)
    return out

def flat(rows):
    return [x for r in rows for x in r]

def eq(name,a,e):
    if a!=e:
        raise AssertionError(f'{name} mismatch actual={a!r} expected={e!r}')

def main():
    main_rows=tg(kb_main(None))
    eq('MAX main rows',mx(max_ui.main_menu_attachment()),main_rows)
    eq('VK main flat',flat(vk(vk_ui.vk_main_keyboard_json(None))),flat(main_rows))
    pre_rows=tg(kb_mood_scale(123,stage='pre'))
    eq('MAX mood pre rows',mx(max_ui.score_scale_attachment(123,stage='pre')),pre_rows)
    eq('VK mood pre flat',flat(vk(vk_ui.vk_score_scale_keyboard_json(123,stage='pre'))),flat(pre_rows))
    text='Главное меню\n\nВыберите маршрут.\n\n• 📈 Мой прогресс'
    eq('VK runtime main flat',flat(vk(vk_ui.with_vk_keyboard('vk',{'_text_for_keyboard':text})['keyboard_json'])),flat(main_rows))
    at=max_ui.native_keyboard_attachments(text)
    if not at:
        raise AssertionError('MAX runtime main produced no attachment')
    eq('MAX runtime main rows',mx(at[0]),main_rows)
    print('OK messenger parity verifier')

if __name__=='__main__':
    main()
