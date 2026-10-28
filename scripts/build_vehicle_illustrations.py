"""Build original, recolorable vehicle SVGs. Run from any working directory.

Only the bodywork uses currentColor. Neutral glass, tyres, metalwork and
transparent paint highlights retain their materials for every body color.
"""
from pathlib import Path
import math


OUTPUT = Path(__file__).resolve().parents[1] / 'static' / 'images' / 'vehicles'


def wheel(x, y=120, radius=21, heavy=False):
    rim = radius * .65
    spokes = ''.join(
        f'<path d="M-2 -3 L-2 -{rim-2:.1f} L1 -{rim-1:.1f} L3 -3Z" '
        f'fill="url(#metal)" transform="rotate({angle})"/>'
        for angle in range(0, 360, 36)
    )
    bolts = ''.join(
        f'<circle cx="{math.cos(a)*4:.2f}" cy="{math.sin(a)*4:.2f}" r=".8" fill="#dce4ee"/>'
        for a in [index * math.tau / 5 for index in range(5)]
    )
    return f'''<g transform="translate({x} {y})">
      <circle r="{radius}" fill="url(#tyre)" stroke="#080d15" stroke-width="1.4"/>
      <circle r="{radius-2}" fill="none" stroke="#707a87" stroke-opacity=".32" stroke-width=".7"/>
      <path d="M-{radius-3} 0 A{radius-3} {radius-3} 0 0 1 {radius-3} 0" fill="none" stroke="#b0bdcc" stroke-opacity=".22"/>
      <circle r="{rim:.1f}" fill="#1b2532" stroke="#a3b0c1" stroke-width="1.5"/>
      {spokes}<circle r="5.8" fill="url(#metal)" stroke="#5c6b80" stroke-width=".8"/>{bolts}
      <circle r="1.7" fill="#6b7c92"/>
    </g>'''


def paint(path):
    return f'''<path d="{path}" fill="currentColor" stroke="#253144" stroke-width="1.2" stroke-linejoin="round"/>
    <path d="{path}" fill="url(#paint)"/>
    <path d="{path}" fill="none" stroke="#dce9f8" stroke-opacity=".27" stroke-width=".7"/>'''


def window(path):
    return f'<path d="{path}" fill="url(#glass)" stroke="#1a2637" stroke-width="2" stroke-linejoin="round"/>'


DEFS = '''<defs>
  <linearGradient id="paint" x1="0" y1="0" x2="0" y2="1" gradientUnits="objectBoundingBox">
    <stop stop-color="#fff" stop-opacity=".42"/><stop offset=".22" stop-color="#fff" stop-opacity=".1"/>
    <stop offset=".45" stop-color="#fff" stop-opacity=".02"/><stop offset=".53" stop-color="#fff" stop-opacity=".2"/>
    <stop offset=".68" stop-color="#000" stop-opacity=".05"/><stop offset="1" stop-color="#000" stop-opacity=".48"/>
  </linearGradient>
  <linearGradient id="glass" x1="0" y1="0" x2=".8" y2="1">
    <stop stop-color="#9fbdcc"/><stop offset=".28" stop-color="#445d70"/>
    <stop offset=".54" stop-color="#25394b"/><stop offset="1" stop-color="#101c2b"/>
  </linearGradient>
  <linearGradient id="metal" x1="0" y1="0" x2=".8" y2="1">
    <stop stop-color="#f5f8fc"/><stop offset=".28" stop-color="#8a9bac"/>
    <stop offset=".5" stop-color="#dce5ed"/><stop offset=".72" stop-color="#5d6a7c"/><stop offset="1" stop-color="#a0aebb"/>
  </linearGradient>
  <radialGradient id="tyre"><stop offset=".55" stop-color="#101722"/><stop offset=".78" stop-color="#29313c"/><stop offset=".94" stop-color="#131b26"/><stop offset="1" stop-color="#080d14"/></radialGradient>
  <radialGradient id="shadow"><stop stop-color="#0a1424" stop-opacity=".4"/><stop offset="1" stop-color="#172338" stop-opacity="0"/></radialGradient>
</defs>'''


def passenger(kind):
    shapes = {
        'sedan': ('M27 118L30 100Q33 91 51 88L99 81L132 55Q138 51 149 51H218Q228 51 237 59L267 82L315 91Q326 93 329 107L331 123H306Q304 96 282 96Q260 96 257 123H114Q111 96 89 96Q66 96 64 123H31Z', 55, 230, 267),
        'hatchback': ('M27 118L30 99Q34 90 51 87L98 80L130 51Q138 47 149 47H237Q245 48 250 56L282 87L317 93L326 107V123H306Q304 96 282 96Q260 96 257 123H114Q111 96 89 96Q66 96 64 123H30Z', 51, 242, 278),
        'wagon': ('M27 118L30 99Q34 90 51 87L98 81L130 51Q138 47 149 47H267L290 85L317 91L329 106V123H306Q304 96 282 96Q260 96 257 123H114Q111 96 89 96Q66 96 64 123H30Z', 51, 260, 285),
        'pickup': ('M27 118L29 95L49 83L85 77L107 47Q113 43 125 43H194L213 79H326V123H306Q304 96 282 96Q260 96 257 123H114Q111 96 89 96Q66 96 64 123H29Z', 48, 190, 207),
        'van': ('M28 119L29 80L67 47Q73 39 89 37H310Q325 38 328 52L331 123H306Q304 96 282 96Q260 96 257 123H114Q111 96 89 96Q66 96 64 123H29Z', 43, 310, 320),
    }
    path, roof, roof_end, rear = shapes[kind]
    result = '<ellipse cx="180" cy="142" rx="158" ry="8" fill="url(#shadow)"/>'
    result += '<path d="M45 112H315V129H45Z" fill="#111b29"/>' + paint(path)
    if kind == 'van':
        result += window('M39 80L76 48H110V82H39Z')
        result += '<path d="M122 44H305V91H122Z" fill="#fff" fill-opacity=".025" stroke="#1b293b" stroke-opacity=".35"/>'
        result += '<path d="M116 45V116M225 44V116M232 52V90" fill="none" stroke="#1b293b" stroke-opacity=".45"/>'
        result += '<path d="M82 48L53 79H67L94 48Z" fill="#e7f4ff" fill-opacity=".16"/>'
    else:
        result += window(f'M107 79L{133 if kind != "pickup" else 114} {roof+3}H174V79Z')
        if kind == 'pickup':
            result += window(f'M181 {roof+3}H188L204 78H181Z')
            result += '<path d="M215 83H323M214 86V117" fill="none" stroke="#182737" stroke-opacity=".55"/>'
        else:
            result += window(f'M181 {roof+3}H{min(roof_end-7, 226)}L{min(rear-13,252)} 79H181Z')
            if kind == 'wagon':
                result += window(f'M237 {roof+3}H{roof_end-5}L{rear-4} 79H259Z')
            result += '<path d="M178 83V116M254 83L250 115" fill="none" stroke="#172639" stroke-opacity=".55" stroke-width=".8"/>'
        result += '<path d="M145 57L119 77H133L158 57Z" fill="#e4f3ff" fill-opacity=".18"/>'
        result += '<path d="M107 82L113 116M116 83H251" fill="none" stroke="#142233" stroke-opacity=".65" stroke-width=".8"/>'
    result += '''<path d="M38 98Q160 84 316 96" fill="none" stroke="#fff" stroke-opacity=".28" stroke-width=".8"/>
      <path d="M117 115H253" fill="none" stroke="#eff5fc" stroke-opacity=".34" stroke-width="1.5"/>
      <path d="M37 108H56M310 109H326" stroke="#132131" stroke-width="5"/>
      <path d="M33 96L56 93L53 99L32 103Z" fill="#e0f1fa" stroke="#708ba2" stroke-width=".6"/>
      <path d="M35 97L51 95" stroke="#fff" stroke-width="1.3"/>
      <path d="M315 92L324 97L326 103L316 100Z" fill="#a62335"/>
      <path d="M317 94L323 98" stroke="#ff7c79" stroke-width="1.3"/>
      <path d="M158 87H169M231 87H242" stroke="url(#metal)" stroke-width="2" stroke-linecap="round"/>
      <path d="M113 79L106 75Q99 74 98 79L100 84H113Z" fill="currentColor" stroke="#243346"/>
      <path d="M101 78H109" stroke="#d7e7f1" stroke-opacity=".55"/>
      <path d="M126 120H247" stroke="#0e1825" stroke-width="3"/>
    '''
    if kind == 'wagon':
        result += f'<path d="M141 {roof-5}H{roof_end-7}" stroke="#82909d" stroke-width="2" stroke-linecap="round"/>'
    result += wheel(89) + wheel(282)
    return result


def offroad_wheel(x):
    vents = ''.join(
        f'<ellipse cx="0" cy="-9" rx="3" ry="4" fill="#172131" transform="rotate({angle})"/>'
        for angle in range(0, 360, 60)
    )
    tread = ''.join(
        f'<path d="M-3 -23L0 -25L3 -23" fill="none" stroke="#56606c" stroke-width="1.5" transform="rotate({angle})"/>'
        for angle in range(0, 360, 20)
    )
    return f'''<g transform="translate({x} 117)">
      <circle r="26" fill="url(#tyre)" stroke="#090f17" stroke-width="1.5"/>{tread}
      <circle r="16" fill="url(#metal)" stroke="#8e9baa" stroke-width="1.5"/>{vents}
      <circle r="5.5" fill="#697a8d" stroke="#c5d0dc"/><circle r="2" fill="#233246"/>
    </g>'''


def suv():
    """Tall, boxy off-roader: short overhangs, raised chassis, rugged tyres."""
    body = 'M27 109V81L32 71H104L122 37Q125 31 136 31H291L306 44V111H304Q301 87 274 87Q246 87 244 112H113Q111 87 83 87Q55 87 53 112H29Z'
    result = '<ellipse cx="178" cy="145" rx="157" ry="8" fill="url(#shadow)"/>'
    result += '<path d="M39 103H308V117H39Z" fill="#172334"/>'
    # Rear-mounted spare wheel, visible beyond the upright tailgate.
    result += '<rect x="305" y="65" width="21" height="44" rx="8" fill="url(#tyre)" stroke="#657183"/>'
    result += '<path d="M309 71H323M308 78H324M308 85H324M308 92H324M309 100H323" stroke="#09121e" stroke-width="2"/>'
    result += paint(body)
    result += window('M111 73L132 40H177V73Z')
    result += window('M187 40H239V73H187Z')
    result += window('M249 40H288L296 48V73H249Z')
    result += '''<path d="M145 42L124 70H135L155 42Z" fill="#e7f4ff" fill-opacity=".17"/>
      <path d="M180 38V105M243 38V105M298 47V105" fill="none" stroke="#132132" stroke-opacity=".65"/>
      <path d="M114 78H297M116 99H241" fill="none" stroke="#fff" stroke-opacity=".27"/>
      <path d="M119 76V105M115 106H244" fill="none" stroke="#142133" stroke-width="1.2"/>
      <path d="M158 82H170M222 82H234" stroke="url(#metal)" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M133 27V23H289V27M146 24V31M278 24V31" fill="none" stroke="#7e8c9c" stroke-width="2" stroke-linejoin="round"/>
      <path d="M30 70H100M31 75H101" stroke="#dbe7f1" stroke-opacity=".35"/>
      <path d="M25 80H39V94H25Z" fill="#172438" stroke="#899daf"/>
      <path d="M26 83H37V88H26Z" fill="#e5f6ff"/><path d="M27 91H37" stroke="#e9a44c" stroke-width="2"/>
      <path d="M299 78H306V97H299Z" fill="#ae2b3e"/><path d="M301 80V93" stroke="#ff9491" stroke-width="1.2"/>
      <path d="M21 106H52V117H23ZM304 106H321V117H304Z" fill="#223044" stroke="#6d7e91"/>
      <path d="M55 111Q57 88 83 88Q109 88 112 111M246 111Q248 88 274 88Q300 88 302 111" fill="none" stroke="#172233" stroke-width="7"/>
      <path d="M55 110Q58 86 83 86Q108 86 112 110M246 110Q249 86 274 86Q299 86 302 110" fill="none" stroke="#7d8b9b" stroke-opacity=".5" stroke-width="1"/>
      <path d="M116 113H241V118H116Z" fill="#28384b" stroke="#8796a5" stroke-width=".7"/>
      <path d="M124 114H234" stroke="#b5c0ca" stroke-width="1"/>
      <path d="M118 73L109 68H104L103 76H119Z" fill="currentColor" stroke="#243346"/>
    '''
    return result + offroad_wheel(83) + offroad_wheel(274)


def minivan():
    """One-volume people carrier: rounded nose, long roof, sliding rear door."""
    body = 'M23 117L25 98Q28 85 43 75L87 37Q103 24 128 24H278Q298 24 310 40L324 65Q331 78 331 94V124H306Q303 99 282 99Q260 99 257 124H104Q102 99 80 99Q58 99 56 124H29Q23 124 23 117Z'
    result = '<ellipse cx="178" cy="143" rx="159" ry="8" fill="url(#shadow)"/>'
    result += '<path d="M34 111H324V128H34Z" fill="#172233"/>' + paint(body)
    # Continuous glass band runs nearly from the nose to the upright rear.
    result += window('M46 77L93 39Q104 32 120 32H139V79H46Z')
    result += window('M148 32H226V79H148Z')
    result += window('M235 32H277Q289 33 297 44L313 73V79H235Z')
    result += '''<path d="M99 39L58 76H75L115 34Z" fill="#e7f4ff" fill-opacity=".18"/>
      <path d="M242 34L273 34L298 75H285Z" fill="#e7f4ff" fill-opacity=".06"/>
      <path d="M142 32V118M231 32V117M314 82V117" fill="none" stroke="#16263a" stroke-opacity=".6" stroke-width="1"/>
      <path d="M151 88H299" fill="none" stroke="#1a293d" stroke-width="3"/>
      <path d="M153 87H298" stroke="#cfdae5" stroke-opacity=".5" stroke-width=".8"/>
      <path d="M150 94V116H249" fill="none" stroke="#1a293d" stroke-opacity=".55"/>
      <path d="M117 85H129M156 84H169" stroke="url(#metal)" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M46 83H312M108 115H254" stroke="#e0edf7" stroke-opacity=".3" stroke-width="1"/>
      <path d="M26 97L43 87H61L51 97L26 102Z" fill="#d9eef8" stroke="#7c94a7" stroke-width=".6"/>
      <path d="M31 96L53 91" stroke="#fff" stroke-width="1.2"/>
      <path d="M318 67H325L329 82V103H320Z" fill="#b72e42" stroke="#6d2d3d" stroke-width=".5"/>
      <path d="M323 73L326 99" stroke="#ff9b94" stroke-width="1.4"/>
      <path d="M28 109H51M308 113H327" stroke="#182739" stroke-width="5"/>
      <path d="M108 123H251" stroke="#142233" stroke-width="3"/>
      <path d="M85 77L79 72H72L69 81H85Z" fill="currentColor" stroke="#243346"/>
      <path d="M73 75H80" stroke="#d6e4f0" stroke-opacity=".5"/>
    '''
    return result + wheel(80, y=122, radius=20) + wheel(282, y=122, radius=20)


def tractor(flatbed=False):
    result = '<ellipse cx="181" cy="143" rx="160" ry="8" fill="url(#shadow)"/>'
    result += '<path d="M46 109H323V128H46Z" fill="#142031" stroke="#506074"/>'
    if flatbed:
        cab = 'M33 119V70L64 43Q67 38 78 38H126Q133 38 135 48L143 116Z'
        result += paint('M145 72H328V107H145Z')
        result += '<path d="M146 103H328M145 76H329M193 76V102M242 76V102M289 76V102" stroke="#182536" stroke-opacity=".55"/>'
        result += '<path d="M147 75H329" stroke="#e2eaf3" stroke-opacity=".5" stroke-width="2"/>'
        result += paint(cab) + window('M42 71L72 45H118V76H42Z')
        result += '<path d="M125 44V114M51 81H121" stroke="#182636" stroke-opacity=".6" fill="none"/>'
        wheels = wheel(78, radius=22, heavy=True) + wheel(262, radius=22, heavy=True) + wheel(309, radius=22, heavy=True)
    else:
        cab = 'M46 119L45 72L53 43L75 27Q79 23 94 23H157L175 34L182 74V117H121Q119 95 96 95Q73 95 71 122H49Z'
        result += '<path d="M194 106H305V111H194Z" fill="url(#metal)"/><path d="M225 99H271L280 104H218Z" fill="#1e2939" stroke="#7b8a9b"/>'
        result += '<rect x="181" y="111" width="49" height="18" rx="5" fill="url(#metal)" stroke="#263447"/>'
        result += '<path d="M191 112V127M218 112V127" stroke="#4c5d72" stroke-width="2"/>'
        result += paint(cab) + window('M53 68L61 44L78 36H132V75H53Z')
        result += window('M139 37H155L170 48L175 74H139Z')
        result += '<path d="M65 46L56 65H68L89 37Z" fill="#edfaff" fill-opacity=".16"/>'
        result += '<path d="M133 34V99M139 80H174V112H126M76 29H154M148 24L163 30" fill="none" stroke="#132132" stroke-opacity=".75"/>'
        result += '<path d="M53 38Q90 25 157 31M137 34V78" fill="none" stroke="#e1edf7" stroke-opacity=".4" stroke-width=".7"/>'
        result += '<path d="M151 84H163" stroke="url(#metal)" stroke-width="2" stroke-linecap="round"/>'
        result += '<path d="M127 105H161V111H124M128 115H162V121H126" fill="url(#metal)" stroke="#364457"/>'
        result += '<path d="M253 116Q258 94 281 94Q304 94 309 116" fill="#172334" stroke="#6d7d90" stroke-width="1.1"/>'
        wheels = wheel(96, radius=23, heavy=True) + wheel(281, radius=23, heavy=True)
    result += '''<path d="M44 74L36 70V53H44" fill="none" stroke="#273548" stroke-width="2"/>
      <rect x="32" y="52" width="6" height="18" rx="2" fill="#1c293a" stroke="#74879a" stroke-width=".7"/>
      <path d="M47 81H65V103H46Z" fill="#182435" stroke="#40516a" stroke-width=".7"/>
      <path d="M48 85H63M48 90H63M48 95H63M48 100H63" stroke="#8191a3" stroke-opacity=".6"/>
      <path d="M46 107H64V114H46Z" fill="#d7eaf4" stroke="#728597"/>
      <path d="M48 110H61" stroke="#fff" stroke-width="1.4"/>
      <path d="M47 117H67M318 118H326" stroke="url(#metal)" stroke-width="4"/>
      <rect x="169" y="98" width="3" height="3" rx="1" fill="#e9ad45"/>
      <rect x="323" y="110" width="4" height="7" rx="1" fill="#d44d4b"/>
    '''
    return result + wheels


def trailer():
    result = '<ellipse cx="180" cy="143" rx="160" ry="8" fill="url(#shadow)"/>'
    result += '<path d="M37 108H325V120H37Z" fill="#192637" stroke="#6d7a8a" stroke-width=".8"/>'
    result += '<path d="M91 112V136H110V139H82V136H87V112M113 112V136" fill="#263344" stroke="#768390" stroke-width="1"/>'
    result += paint('M28 45H329V108H28Z')
    result += '<path d="M29 45H329M30 48H328M29 108H329M29 45V108M329 45V108" stroke="url(#metal)" stroke-width="2"/>'
    for x in range(39, 326, 14):
        result += f'<path d="M{x} 49V104" stroke="#000" stroke-opacity=".13"/><path d="M{x+1} 49V104" stroke="#fff" stroke-opacity=".1"/>'
    for x in range(37, 328, 24):
        result += f'<rect x="{x}" y="105" width="11" height="2" fill="#e9ebdf"/><rect x="{x+11}" y="105" width="10" height="2" fill="#bb343d"/><path d="M{x+1} 98V104" stroke="#c0c8d1" stroke-width="1.5"/>'
    result += '<path d="M126 121H207V134H126Z" fill="#243246" stroke="#708195"/><path d="M129 124H204M129 131H204" stroke="#a3afba" stroke-width="1.3"/>'
    result += '<path d="M216 117Q220 98 237 98H298Q316 100 320 117" fill="#182435" stroke="#637387"/>'
    result += '<rect x="324" y="110" width="5" height="12" rx="1" fill="#be3541"/><rect x="29" y="48" width="3" height="3" fill="#e8b855"/>'
    return result + ''.join(wheel(x, y=124, radius=16, heavy=True) for x in (237, 272, 307))


def build():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for kind in ('sedan', 'hatchback', 'wagon', 'suv', 'minivan', 'pickup', 'van', 'tractor_unit', 'flatbed', 'trailer'):
        renderers = {'tractor_unit': tractor, 'flatbed': lambda: tractor(True), 'trailer': trailer, 'suv': suv, 'minivan': minivan}
        artwork = renderers[kind]() if kind in renderers else passenger(kind)
        # Keep definitions inside the referenced group for external <use> in
        # Chromium, Firefox and WebKit. IDs are unique across body types too.
        content = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 200"><g id="body">{DEFS}{artwork}</g></svg>\n'
        for name in ('paint', 'glass', 'metal', 'tyre', 'shadow'):
            content = content.replace(f'id="{name}"', f'id="{kind}-{name}"').replace(f'url(#{name})', f'url(#{kind}-{name})')
        (OUTPUT / f'{kind}.svg').write_text(content, encoding='utf-8')


if __name__ == '__main__':
    build()
