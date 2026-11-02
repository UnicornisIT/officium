"""Render review annotations as SVG overlays; preserve all original screenshots."""
import base64
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Rectangles use fractions of each original screenshot: x, y, width, height.
CHANGES = {
 '01': ('Поля одной высоты и на одной линии; переключатель занимает ширину блока.',
        [[.018,.36,.965,.21],[.018,.66,.63,.28]], [[.018,.375,.965,.15],[.018,.68,.965,.255]]),
 '02': ('Одинаковая высота полей, оформление списков и понятные подписи расположения контакта.',
        [[.035,.23,.93,.075],[.035,.52,.93,.08],[.035,.68,.93,.085]], [[.035,.23,.93,.075],[.035,.52,.93,.09],[.035,.68,.93,.085]]),
 '03': ('Единая высота полей, интервалы между подписью и полем, выравнивание кнопки.',
        [[.018,.055,.965,.08],[.018,.15,.965,.18],[.018,.95,.18,.045]], [[.018,.05,.965,.08],[.018,.14,.965,.18],[.018,.95,.18,.045]]),
 '04': ('Высота полей, оформление выбора плательщика, загрузки чека и кнопки.',
        [[.01,.06,.98,.075],[.01,.48,.22,.05],[.01,.825,.98,.065],[.01,.91,.98,.065]], [[.01,.06,.98,.075],[.01,.44,.27,.055],[.01,.805,.98,.085],[.01,.925,.98,.065]]),
 '05': ('Форма в две колонки; подпись по центру тумблера; оформление файла и кнопки.',
        [[.02,.15,.96,.38],[.02,.78,.5,.04],[.02,.845,.96,.05],[.02,.91,.96,.04]], [[.16,.20,.68,.30],[.16,.70,.49,.05],[.16,.79,.68,.075],[.16,.88,.14,.065]]),
 '06': ('Общая высота полей и кнопки сохранения расхода.',
        [[.032,.20,.28,.07],[.032,.30,.28,.23],[.032,.895,.14,.055]], [[.032,.205,.28,.055],[.032,.305,.28,.26],[.032,.895,.14,.055]]),
 '07': ('Общая высота полей и кнопки сохранения дохода.',
        [[.02,.20,.29,.09],[.02,.29,.29,.22],[.02,.87,.14,.045]], [[.02,.19,.29,.09],[.02,.29,.29,.22],[.02,.87,.14,.05]]),
 '08': ('Выравнивание текста и иконки кнопки «Пересчитать план».',
        [[.81,.76,.18,.16]], [[.81,.76,.18,.16]]),
 '09': ('Текст уведомлений по центру тумблера; обновлена подпись профиля и выровнена кнопка.',
        [[.35,.14,.36,.065],[.27,.235,.40,.065]], [[.35,.115,.36,.04],[.27,.228,.46,.065]]),
 '10': ('Оформление выпадающего списка и выравнивание кнопки добавления.',
        [[.045,.20,.24,.025],[.045,.285,.24,.03]], [[.045,.20,.24,.025],[.045,.285,.24,.03]]),
 '11': ('Компактная карточка: аватар и имя сверху, роль, статус, входы и дата — в сетке ниже. Аватар фиксированного размера 38 × 38 пикселей.',
        [[.045,.49,.91,.445]], [[.045,.65,.91,.265]]),
 '12': ('Оформление выпадающих списков в фильтрах. Количество строк журнала — тестовые данные.',
        [[.04,.17,.91,.07]], [[.04,.082,.91,.034]]),
 '13': ('Одинаковые размеры полей документа, оформление загрузки скана и кнопки.',
        [[.365,.275,.60,.255],[.365,.77,.60,.045],[.365,.815,.16,.04]], [[.365,.267,.60,.215],[.365,.775,.60,.045],[.365,.82,.16,.035]]),
 '14': ('Высота полей, интервалы, оформление выбора файла и кнопки обслуживания.',
        [[.225,.235,.55,.05],[.225,.64,.55,.05],[.225,.85,.55,.05],[.225,.91,.55,.05]], [[.225,.21,.55,.045],[.225,.642,.55,.045],[.225,.858,.55,.05],[.225,.916,.55,.045]]),
 '15': ('Общая высота полей в форме долга. На длинной форме заметен накопленный сдвиг строк.',
        [[.025,.12,.95,.075],[.025,.20,.95,.065],[.025,.29,.95,.065]], [[.025,.12,.95,.075],[.025,.20,.95,.065],[.025,.29,.95,.065]]),
 '16': ('Русские подписи и оформление выбора файла выписки, выравнивание кнопки.',
        [[.07,.22,.75,.065],[.82,.23,.11,.055]], [[.07,.22,.75,.065],[.82,.23,.11,.055]]),
 '17': ('Высота полей и кнопок, расстояние между действиями внизу окна.',
        [[.04,.23,.92,.11],[.04,.41,.92,.10],[.64,.86,.32,.075]], [[.04,.22,.92,.11],[.04,.40,.92,.10],[.64,.86,.32,.09]]),
}


def png(name):
    data = (ROOT / name).read_bytes()
    w, h = struct.unpack('>II', data[16:24])
    return w, h, 'data:image/png;base64,' + base64.b64encode(data).decode()


def svg_image(name):
    w, h, uri = png(name)
    return w, h, f'<image width="{w}" height="{h}" href="{uri}"/>'


def rectangles(boxes, w, h, scale=1, dx=0, dy=0):
    return ''.join(f'<rect x="{dx+x*w*scale:.2f}" y="{dy+y*h*scale:.2f}" width="{bw*w*scale:.2f}" height="{bh*h*scale:.2f}" fill="none" stroke="#ed2424" stroke-width="3" vector-effect="non-scaling-stroke"/>' for x, y, bw, bh in boxes)


def write_svg(name, w, h, content):
    (ROOT / name).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{content}</svg>', encoding='utf-8')


for n, (note, before, after) in CHANGES.items():
    combined = ''
    for col, (phase, boxes) in enumerate([('before', before), ('after', after)]):
        w, h, content = svg_image(f'{n}-{phase}.png')
        write_svg(f'{n}-{phase}-marked.svg', w, h, content + rectangles(boxes, w, h))
        scale = min(1, 760/w, 1500/h)
        # Pillow rounds thumbnail dimensions to integer pixels.
        scale_x, scale_y = round(w*scale)/w, round(h*scale)/h
        for x, y, bw, bh in boxes:
            combined += f'<rect x="{20+col*800+x*w*scale_x:.2f}" y="{104+y*h*scale_y:.2f}" width="{bw*w*scale_x:.2f}" height="{bh*h*scale_y:.2f}" fill="none" stroke="#ed2424" stroke-width="3"/>'
    w, h, content = svg_image(f'{n}-compare.png')
    write_svg(f'{n}-compare-marked.svg', w, h, content + combined)

index = ROOT / 'index.html'
text = index.read_text(encoding='utf-8')
text = re.sub(r'src="(\d{2})-compare(?:-marked\.svg|\.png)(?:\?[^" ]*)?"', r'src="\1-compare-marked.svg?v=1"', text)
text = re.sub(r'<p class="change-note">.*?</p>', '', text)
for n, (note, *_) in CHANGES.items():
    marker = f'<a class="preview" href="viewer.html?image={n}-compare.png"'
    text = text.replace(marker, f'<p class="change-note" style="margin:16px 24px;color:#536581;line-height:1.6"><span style="color:#ed2424;font-weight:700">□ Изменено:</span> {note}</p>' + marker)
if 'Красными рамками' not in text:
    text = text.replace('17 пронумерованных экранов.', '17 пронумерованных экранов. Красными рамками отмечены изменённые элементы на обеих версиях.')
index.write_text(text, encoding='utf-8')

viewer = ROOT / 'viewer.html'
text = viewer.read_text(encoding='utf-8')
if 'id="marks"' not in text:
    text = text.replace('<a id="original"', '<button id="marks">Скрыть рамки</button><a id="original"')
text = text.replace('image.src=filename;', "image.src=filename.replace('.png','-marked.svg')+'?v=1';")
text = text.replace('image.onload=()=>resize(1);', 'image.onload=()=>resize(scale);')
if 'let marked=true;' not in text:
    text = text.replace("document.getElementById('minus').onclick", "let marked=true;document.getElementById('marks').onclick=()=>{marked=!marked;image.src=(marked?filename.replace('.png','-marked.svg'):filename)+'?v=1';document.getElementById('marks').textContent=marked?'Скрыть рамки':'Показать рамки'};\ndocument.getElementById('minus').onclick")
viewer.write_text(text, encoding='utf-8')
(ROOT / 'annotations.json').write_text(json.dumps(CHANGES, ensure_ascii=False, indent=2), encoding='utf-8')
print('Added red annotations to 17 before/after pairs, gallery and full-size viewer')
