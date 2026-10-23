"""Kroonkurken (de "dop" van een speler): de server-kant.

LET OP: dit is een letterlijke vertaling van static/dop.js. De server tekent
hiermee de afbeeldingen die je overal op de site ziet; de editor in de browser
gebruikt dop.js. Pas je het ene aan, pas dan ook het andere aan: test_dop.py
controleert dat beide exact dezelfde SVG maken.
"""
import json
import math

# ------------------------------------------------------------------ keuzes --
KLEUREN = [
    ("wit", "#f8fafc", "Wit"), ("creme", "#f1e4c3", "Crème"), ("zilver", "#c0c6cc", "Zilver"),
    ("goud", "#d4a72c", "Goud"), ("brons", "#b06d34", "Brons"), ("geel", "#facc15", "Geel"),
    ("oranje", "#f97316", "Oranje"), ("rood", "#dc2626", "Rood"), ("bordeaux", "#7f1d1d", "Bordeaux"),
    ("roze", "#ec4899", "Roze"), ("paars", "#7c3aed", "Paars"), ("blauw", "#1d4ed8", "Blauw"),
    ("hemel", "#38bdf8", "Hemelsblauw"), ("turkoois", "#14b8a6", "Turkoois"), ("groen", "#16a34a", "Groen"),
    ("olijf", "#65702c", "Olijf"), ("bruin", "#6b4226", "Bruin"), ("zwart", "#18181b", "Zwart"),
]
RANDEN = [("klassiek", "Klassiek"), ("fijn", "Fijn"), ("golf", "Golf"), ("kartel", "Kartel"), ("glad", "Glad")]
PATRONEN = [("geen", "Geen"), ("strepen", "Strepen"), ("ringen", "Ringen"), ("stralen", "Stralen"),
            ("stippen", "Stippen"), ("ruit", "Dambord"), ("half", "Half"), ("band", "Band"), ("kruis", "Kruis"),
            ("golven", "Golven")]
EMBLEMEN = [("geen", "Geen"), ("ster", "Ster"), ("hart", "Hart"), ("bliksem", "Bliksem"),
            ("kroon", "Kroon"), ("vis", "Vis"), ("klaver", "Klaver"), ("vlam", "Vlam"), ("diamant", "Diamant"),
            ("maan", "Maan"), ("pint", "Pint"), ("nummer", "Nummer")]
AFWERKINGEN = [("mat", "Mat"), ("glans", "Glans"), ("metaal", "Metaal"), ("glitter", "Glitter")]

# Iedereen begint met deze: een gewone, zilveren kroonkurk.
BASIS = {"kleur": "zilver", "rand": "klassiek", "randkleur": "zelfde", "patroon": "geen",
         "patroonkleur": "wit", "embleem": "geen", "embleemkleur": "zwart", "nummer": 7, "afwerking": "metaal"}


def _sleutels(lijst):
    return [x[0] for x in lijst]


TOEGELATEN = {
    "kleur": _sleutels(KLEUREN), "rand": _sleutels(RANDEN), "randkleur": ["zelfde"] + _sleutels(KLEUREN),
    "patroon": _sleutels(PATRONEN), "patroonkleur": _sleutels(KLEUREN), "embleem": _sleutels(EMBLEMEN),
    "embleemkleur": _sleutels(KLEUREN), "afwerking": _sleutels(AFWERKINGEN),
}
HEX = {k[0]: k[1] for k in KLEUREN}


def normaliseer(o):
    """Alles wat niet klopt, wordt de basiswaarde. Zo kan een kapot of oud
    ontwerp nooit een fout geven. Aanvaardt een dict of een JSON-tekst."""
    if isinstance(o, str):
        try:
            o = json.loads(o)
        except ValueError:
            o = None
    if not isinstance(o, dict):
        o = {}
    uit = {}
    for k in BASIS:
        if k == "nummer":
            try:
                getal = int(o.get("nummer"))
            except (TypeError, ValueError):
                getal = -1
            uit["nummer"] = getal if 0 <= getal <= 99 else BASIS["nummer"]
        else:
            uit[k] = o.get(k) if o.get(k) in TOEGELATEN[k] else BASIS[k]
    return uit


def als_tekst(o):
    """Een ontwerp zoals het in de database komt (vaste volgorde)."""
    return json.dumps(normaliseer(o), separators=(",", ":"), sort_keys=True)


# ----------------------------------------------------------------- hulpjes --
def n(x):
    """Getallen afronden zoals dop.js (dezelfde letters, tot op de komma)."""
    r = math.floor(x * 100 + 0.5) / 100
    if r == 0:
        return "0"
    if r == int(r):
        return str(int(r))
    return repr(r)


def _hex2(v):
    return format(max(0, min(255, math.floor(v + 0.5))), "02x")


def meng(a, b, t):
    """Twee kleuren mengen: t = 0 geeft a, t = 1 geeft b."""
    uit = "#"
    for i in (1, 3, 5):
        x, y = int(a[i:i + 2], 16), int(b[i:i + 2], 16)
        uit += _hex2(x + (y - x) * t)
    return uit


def helder(c):
    r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    return (r * 299 + g * 587 + b * 114) / 1000 > 150


# ---------------------------------------------------------------- de rand --
def rand_pad(stijl):
    R = 48.5
    d = ""
    if stijl == "glad":
        return "M2 50a48 48 0 1 0 96 0a48 48 0 1 0-96 0z"
    if stijl == "kartel":
        t, a = 24, 3.4
        for i in range(t * 2):
            hk = i * math.pi / t
            r = R if i % 2 == 0 else R - a
            d += ("L" if i else "M") + n(50 + r * math.cos(hk)) + " " + n(50 + r * math.sin(hk))
        return d + "z"
    tanden = 36 if stijl == "fijn" else 10 if stijl == "golf" else 21
    amp = 1.8 if stijl == "fijn" else 3.2 if stijl == "golf" else 3
    stap = 12 if stijl == "golf" else 6
    aantal = tanden * stap
    for j in range(aantal):
        h = j * 2 * math.pi / aantal
        rr = R - amp * (1 - math.cos(tanden * h)) / 2
        d += ("L" if j else "M") + n(50 + rr * math.cos(h)) + " " + n(50 + rr * math.sin(h))
    return d + "z"


# --------------------------------------------------------------- patronen --
def patroon(soort, kleur):
    s = ""
    if soort == "strepen":
        s = '<g transform="rotate(-35 50 50)">'
        for i in range(-10, 110, 11):
            s += f'<rect x="{i}" y="-10" width="5" height="120"/>'
        return f'<g fill="{kleur}">' + s + "</g></g>"
    if soort == "ringen":
        return (f'<g fill="none" stroke="{kleur}" stroke-width="3.6">'
                '<circle cx="50" cy="50" r="31"/><circle cx="50" cy="50" r="23"/><circle cx="50" cy="50" r="15"/></g>')
    if soort == "stralen":
        d = ""
        for i in range(12):
            a1 = (i * 30 - 7.5) * math.pi / 180
            a2 = (i * 30 + 7.5) * math.pi / 180
            d += ("M50 50L" + n(50 + 45 * math.cos(a1)) + " " + n(50 + 45 * math.sin(a1)) +
                  "L" + n(50 + 45 * math.cos(a2)) + " " + n(50 + 45 * math.sin(a2)) + "z")
        return f'<path fill="{kleur}" d="{d}"/>'
    if soort == "stippen":
        for j in range(10):
            y = 8 + j * 9.2
            for i in range(11):
                x = 5 + i * 10 + (j % 2) * 5
                if (x - 50) * (x - 50) + (y - 50) * (y - 50) < 41 * 41:
                    s += f'<circle cx="{n(x)}" cy="{n(y)}" r="2.4"/>'
        return f'<g fill="{kleur}">' + s + "</g>"
    if soort == "ruit":
        for j in range(11):
            for i in range(11):
                if (i + j) % 2 == 0:
                    s += f'<rect x="{i * 10 - 5}" y="{j * 10 - 5}" width="10" height="10"/>'
        return f'<g fill="{kleur}" transform="rotate(45 50 50)">' + s + "</g>"
    if soort == "half":
        return f'<rect fill="{kleur}" x="50" y="-20" width="70" height="140" transform="rotate(35 50 50)"/>'
    if soort == "band":
        return (f'<g fill="{kleur}" transform="rotate(-20 50 50)"><rect x="-10" y="41" width="120" height="18"/>'
                '<rect x="-10" y="35" width="120" height="2.6"/><rect x="-10" y="62.4" width="120" height="2.6"/></g>')
    if soort == "kruis":
        return (f'<g fill="{kleur}"><rect x="43" y="-5" width="14" height="110"/>'
                '<rect x="-5" y="43" width="110" height="14"/></g>')
    if soort == "golven":
        for j in range(4):
            yy = 23 + j * 18
            s += f"M0 {yy}"
            for _ in range(5):
                s += "q5 -5 10 0t10 0"
        return f'<path fill="none" stroke="{kleur}" stroke-width="3.4" stroke-linecap="round" d="{s}"/>'
    return ""


# --------------------------------------------------------------- emblemen --
VORMEN = {
    "hart": '<path d="M12 21s-7.5-4.6-9.6-9.3C.9 8.3 3 4.5 6.6 4.5c2.2 0 3.6 1.3 5.4 3.4 1.8-2.1 3.2-3.4 5.4-3.4 3.6 0 5.7 3.8 4.2 7.2C19.5 16.4 12 21 12 21z"/>',
    "bliksem": '<path d="M14 1.5 4 13.5h6.5L8.5 22.5 20 9.5h-6.5L16 1.5z"/>',
    "kroon": '<path d="M2.5 7.5 7.5 12 12 4l4.5 8 5-4.5-2 12h-15z"/><path d="M4.5 21h15v1.8h-15z"/>',
    "klaver": '<circle cx="12" cy="7.4" r="4.3"/><circle cx="16.6" cy="12" r="4.3"/><circle cx="12" cy="16.6" r="4.3"/><circle cx="7.4" cy="12" r="4.3"/>',
    "vlam": '<path d="M12 1.5c1.2 4.2 6.5 6.4 6.5 12.5a6.5 6.5 0 0 1-13 0c0-3.4 2-5.6 3.2-7.6.2 2.2 1.2 3.4 2.4 3.6-.4-3.2-.6-5.6.9-8.5z"/>',
    "diamant": '<path d="M6.5 3h11l4.5 6.2L12 21.5 2 9.2z"/>',
    "maan": '<path d="M15.5 2.5a9.5 9.5 0 1 0 6 16.4A8 8 0 1 1 15.5 2.5z"/>',
    "pint": '<path d="M4.5 4h11.5v16a1.5 1.5 0 0 1-1.5 1.5h-8.5a1.5 1.5 0 0 1-1.5-1.5z"/><path fill="none" stroke="currentColor" stroke-width="2.2" d="M16 8.2h2.4a1.9 1.9 0 0 1 1.9 1.9v4.4a1.9 1.9 0 0 1-1.9 1.9H16"/>',
}


def _ster():
    d = ""
    for i in range(10):
        h = (-90 + i * 36) * math.pi / 180
        r = 11.5 if i % 2 == 0 else 4.8
        d += ("L" if i else "M") + n(12 + r * math.cos(h)) + " " + n(12.6 + r * math.sin(h))
    return f'<path d="{d}z"/>'


def embleem(o, cx, cy, schaal, basiskleur):
    if o["embleem"] == "geen":
        return ""
    kleur = HEX[o["embleemkleur"]]
    rand = meng(kleur, "#000000", 0.62) if helder(kleur) else meng(kleur, "#ffffff", 0.6)
    if o["embleem"] == "nummer":
        grootte = 30 * schaal / 1.55
        return (f'<text x="{n(cx)}" y="{n(cy + grootte * 0.36)}" text-anchor="middle" font-size="{n(grootte)}'
                f'" font-family="Arial Black,Arial,Helvetica,sans-serif" font-weight="900" fill="{kleur}'
                f'" stroke="{rand}" stroke-width="{n(2.6 * schaal / 1.55)}" stroke-linejoin="round" paint-order="stroke">'
                f'{o["nummer"]}</text>')
    if o["embleem"] == "ster":
        binnen = _ster()
    elif o["embleem"] == "vis":
        binnen = ('<g transform="translate(-9.3 -7.9) scale(.4)"><path d="M24 50C33 39 52 36.5 62 43c3.5 2.2 5.5 4.8 5.5 7s-2 4.8-5.5 7C52 63.5 33 61 24 50z"/>'
                  '<path d="M61 50 76.5 36.5c3-2.3 4.5-1.3 3.3 1.7L73.2 50l6.6 11.8c1.2 3-.3 4-3.3 1.7z"/>'
                  f'<circle cx="36.5" cy="48.2" r="4.6" fill="{basiskleur}" stroke="none"/></g>')
    else:
        binnen = VORMEN[o["embleem"]]
    return (f'<g transform="translate({n(cx - 12 * schaal)} {n(cy - 12 * schaal)}) scale({n(schaal)}'
            f')" color="{kleur}" fill="{kleur}" stroke="{rand}" stroke-width="{n(2.4 / schaal)}'
            '" stroke-linejoin="round" paint-order="stroke">' + binnen + "</g>")


def _glinsters():
    z = 12345
    uit = []

    def volgende():
        nonlocal z
        z = (z * 16807) % 2147483647
        return z / 2147483647

    for _ in range(46):
        x = 12 + volgende() * 76
        y = 12 + volgende() * 76
        r = 0.35 + volgende() * 0.6
        uit.append((x, y, r))
    return uit


GLINSTERS = _glinsters()


# ---------------------------------------------------------------- tekenen --
def _onderdelen(o, p, emb):
    kleur = HEX[o["kleur"]]
    rk = kleur if o["randkleur"] == "zelfde" else HEX[o["randkleur"]]
    defs = (f'<radialGradient id="{p}r"><stop offset=".78" stop-color="{meng(rk, "#ffffff", 0.18)}'
            f'"/><stop offset=".9" stop-color="{rk}"/><stop offset="1" stop-color="{meng(rk, "#000000", 0.38)}"/></radialGradient>'
            f'<radialGradient id="{p}s" cx=".42" cy=".38" r=".62"><stop offset=".55" stop-color="#000" stop-opacity="0"/>'
            '<stop offset="1" stop-color="#000" stop-opacity=".22"/></radialGradient>'
            f'<clipPath id="{p}c"><circle cx="50" cy="50" r="38.5"/></clipPath>')
    rand = rand_pad(o["rand"])
    uit = (f'<path d="{rand}" fill="{meng(rk, "#000000", 0.2)}"/>'
           f'<path d="{rand}" fill="url(#{p}r)"/>')

    uit += f'<g clip-path="url(#{p}c)"><circle cx="50" cy="50" r="38.5" fill="{kleur}"/>'
    if o["patroon"] != "geen":
        uit += patroon(o["patroon"], HEX[o["patroonkleur"]])
    uit += emb
    uit += f'<circle cx="50" cy="50" r="38.5" fill="url(#{p}s)"/>'

    if o["afwerking"] == "glans":
        uit += ('<ellipse cx="38" cy="33" rx="23" ry="13" transform="rotate(-32 38 33)" fill="#fff" opacity=".26"/>'
                '<circle cx="29" cy="28" r="3.2" fill="#fff" opacity=".55"/>')
    elif o["afwerking"] == "metaal":
        defs += (f'<linearGradient id="{p}m" x1="0" y1="0" x2="1" y2="1">'
                 '<stop offset="0" stop-color="#fff" stop-opacity=".45"/><stop offset=".3" stop-color="#fff" stop-opacity="0"/>'
                 '<stop offset=".52" stop-color="#fff" stop-opacity=".3"/><stop offset=".7" stop-color="#000" stop-opacity=".12"/>'
                 '<stop offset="1" stop-color="#fff" stop-opacity=".2"/></linearGradient>')
        uit += f'<circle cx="50" cy="50" r="38.5" fill="url(#{p}m)"/>'
    elif o["afwerking"] == "glitter":
        for g in GLINSTERS:
            uit += f'<circle cx="{n(g[0])}" cy="{n(g[1])}" r="{n(g[2])}" fill="#fff" opacity=".75"/>'
    uit += "</g>"
    uit += (f'<circle cx="50" cy="50" r="38.5" fill="none" stroke="{meng(rk, "#000000", 0.32)}" stroke-width="1.6"/>'
            f'<circle cx="50" cy="50" r="37.2" fill="none" stroke="{meng(kleur, "#ffffff", 0.3)}" stroke-width=".6" opacity=".7"/>')
    if o["afwerking"] == "metaal":
        uit += f'<path d="{rand}" fill="url(#{p}m)" opacity=".7"/>'
    return defs, uit


KOP = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'


def svg(ontwerp, p="d"):
    """De dop van één speler."""
    o = normaliseer(ontwerp)
    defs, inhoud = _onderdelen(o, p, embleem(o, 50, 50, 1.55, HEX[o["kleur"]]))
    return KOP + "<defs>" + defs + "</defs>" + inhoud + "</svg>"


def team_svg(o1, o2, p="t"):
    """Een teamdop: links de helft van speler 1, rechts die van speler 2."""
    a, b = normaliseer(o1), normaliseer(o2)
    d1 = _onderdelen(a, p + "a", embleem(a, 30, 50, 0.92, HEX[a["kleur"]]))
    d2 = _onderdelen(b, p + "b", embleem(b, 70, 50, 0.92, HEX[b["kleur"]]))
    return (KOP + "<defs>" + d1[0] + d2[0] +
            f'<clipPath id="{p}l"><rect x="0" y="0" width="50" height="100"/></clipPath>'
            f'<clipPath id="{p}h"><rect x="50" y="0" width="50" height="100"/></clipPath></defs>'
            f'<g clip-path="url(#{p}l)">' + d1[1] + "</g>"
            f'<g clip-path="url(#{p}h)">' + d2[1] + "</g>"
            '<path d="M50 .8V99.2" stroke="#0b0b0b" stroke-width="2.4"/>'
            '<path d="M50 12V88" stroke="#fff" stroke-opacity=".35" stroke-width=".6"/></svg>')


def randkleur_hex(ontwerp):
    """De kleur van de rand (voor de dikte van de 3D-dop)."""
    o = normaliseer(ontwerp)
    return HEX[o["kleur"]] if o["randkleur"] == "zelfde" else HEX[o["randkleur"]]


MAX_BEWAARD = 3


def bewaarde_lijst(tekst):
    """De bewaarde ontwerpen van een speler: hoogstens drie, elk genormaliseerd."""
    try:
        lijst = json.loads(tekst) if isinstance(tekst, str) else tekst
    except ValueError:
        lijst = None
    if not isinstance(lijst, list):
        return []
    return [normaliseer(o) for o in lijst[:MAX_BEWAARD] if isinstance(o, dict)]


def zelfde(a, b):
    """Zien twee ontwerpen er hetzelfde uit? Keuzes die niet getoond worden
    (de kleur van een patroon dat er niet is, het nummer zonder nummer-embleem)
    tellen niet mee. Zelfde regel als zelfde() in dop.js."""
    a, b = normaliseer(a), normaliseer(b)
    for k in BASIS:
        if k == "patroonkleur" and a["patroon"] == "geen":
            continue
        if k == "embleemkleur" and a["embleem"] == "geen":
            continue
        if k == "nummer" and a["embleem"] != "nummer":
            continue
        if a[k] != b[k]:
            return False
    return True
