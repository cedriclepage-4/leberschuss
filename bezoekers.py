"""Bezoekersstatistieken: hoeveel volk er op de site komt, wanneer en vanwaar.

Enkel voor de eigenaar. Bewust zuinig met wat er bewaard wordt:

- Geen IP-adressen. Een bezoeker is een willekeurige code in een cookie
  ("bezoeker"). Zonder cookie (eerste paginabezoek, of cookies uit) is het een
  hash van IP + browser met een sleutel die elke dag verandert: die is de dag
  erna niet meer terug te rekenen.
- Geen adressen van pagina's: enkel het moment, de bezoeker, het account (als
  iemand ingelogd is) en vanwaar een bezoek binnenkwam.
- Losse paginabezoeken blijven RUW_BEWAREN_DAGEN dagen staan. Daarna blijven
  enkel de dagtotalen over (tabel bezoeken_dag).

Wat telt als een paginabezoek: een gewone GET van een HTML-pagina die de browser
zelf opent (geen achtergrondverzoeken zoals de tv die elke minuut ververst,
geen prefetch, geen bots, niets onder /admin).

Paginabezoeken worden per proces even opgespaard en in één keer weggeschreven,
zodat een volle zaal de database niet bij elke klik hoeft te vergrendelen.
"""

import hashlib
import hmac
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse

COOKIE = "bezoeker"
COOKIE_DUUR = 400 * 24 * 3600          # zo lang mag een browser een cookie houden
RUW_BEWAREN_DAGEN = 30
BEZOEKERS_BEWAREN_DAGEN = 400          # na zo lang weg = een nieuwe bezoeker
BEZOEK_PAUZE = timedelta(minutes=30)   # langer stil = een nieuw bezoek
ONLINE_VENSTER = timedelta(minutes=5)
SCHRIJF_NA_SECONDEN = 30
SCHRIJF_NA_AANTAL = 50

TIJD = "%Y-%m-%d %H:%M:%S"
DAGNAMEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]
MAANDEN = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt",
           "nov", "dec"]

DIRECT = "Rechtstreeks"

_BOT = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|preview|facebookexternalhit|whatsapp/|"
    r"telegram|discord|skype|curl|wget|python|httpx|go-http|java/|okhttp|"
    r"headless|lighthouse|pingdom|uptime|monitor|feedfetcher|scrapy",
    re.I)

# Externe sites, herkend aan hun domein.
_SITES = [
    ("whatsapp", "WhatsApp"), ("wa.me", "WhatsApp"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"), ("fb.com", "Facebook"), ("messenger", "Facebook"),
    ("tiktok", "TikTok"), ("snapchat", "Snapchat"),
    ("google", "Google"), ("bing", "Bing"), ("duckduckgo", "DuckDuckGo"),
    ("ecosia", "Ecosia"), ("yahoo", "Yahoo"),
    ("t.co", "X / Twitter"), ("twitter", "X / Twitter"), ("x.com", "X / Twitter"),
    ("linkedin", "LinkedIn"), ("reddit", "Reddit"),
]
# Apps met een ingebouwde browser die geen afzender meesturen, maar zich wel
# verraden in de user-agent.
_APPS = [
    ("Instagram", "Instagram"), ("FBAN", "Facebook"), ("FBAV", "Facebook"),
    ("FB_IAB", "Facebook"), ("musical_ly", "TikTok"), ("TikTok", "TikTok"),
    ("Snapchat", "Snapchat"), ("WhatsApp", "WhatsApp"),
]

_COOKIE_VORM = re.compile(r"^[0-9a-f]{20}$")


# ---------------------------------------------------------------- herkennen --

def is_bot(ua):
    return not ua or bool(_BOT.search(ua))


def _zonder_www(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def bron_van(referrer, ua, eigen_host):
    """Vanwaar dit paginabezoek komt; None als het een klik binnen de site is."""
    host = _zonder_www(urlparse(referrer or "").hostname)
    if host and host == _zonder_www(eigen_host):
        return None
    if host:
        for stuk, naam in _SITES:
            if stuk in host:
                return naam
        return host[:60]
    for stuk, naam in _APPS:
        if stuk in (ua or ""):
            return naam
    return DIRECT


def bezoeker_code(cookie, ip, ua, sleutel, nu):
    """De code van deze bezoeker en of die nog als cookie gezet moet worden."""
    if cookie and _COOKIE_VORM.match(cookie):
        return cookie, False
    dag = hmac.new(sleutel.encode(), f"bezoeker:{nu:%Y-%m-%d}".encode(),
                   hashlib.sha256).digest()
    code = hmac.new(dag, f"{ip}|{ua}".encode(), hashlib.sha256).hexdigest()[:20]
    return code, True


def telt_mee(request, response):
    """Is dit een paginabezoek dat meetelt in de statistieken?"""
    if request.method != "GET" or response.status_code != 200:
        return False
    if response.mimetype != "text/html":
        return False
    if request.path.startswith(("/admin", "/static")):
        return False
    # Enkel wat de browser zelf als pagina opent, geen fetch() op de achtergrond.
    bestemming = request.headers.get("Sec-Fetch-Dest")
    if bestemming and bestemming != "document":
        return False
    if "prefetch" in (request.headers.get("Sec-Purpose", "")
                      + request.headers.get("Purpose", "")).lower():
        return False
    return not is_bot(request.headers.get("User-Agent", ""))


# ---------------------------------------------------------------- noteren --

_slot = threading.Lock()
_wachtrij = []
_laatst_geschreven = time.time()
_laatste_onderhoud = 0.0


def noteer(moment, bezoeker, speler_id, bron):
    with _slot:
        _wachtrij.append((moment.strftime(TIJD), bezoeker, speler_id, bron))


def moet_schrijven():
    return bool(_wachtrij) and (len(_wachtrij) >= SCHRIJF_NA_AANTAL
                                or time.time() - _laatst_geschreven >= SCHRIJF_NA_SECONDEN)


def schrijf_weg(db):
    """Alle opgespaarde paginabezoeken naar de database."""
    global _wachtrij, _laatst_geschreven, _laatste_onderhoud
    with _slot:
        rijen, _wachtrij = _wachtrij, []
        _laatst_geschreven = time.time()
    if rijen:
        db.executemany("INSERT INTO bezoeken (moment, bezoeker, speler_id, bron) "
                       "VALUES (?, ?, ?, ?)", rijen)
        eerst = {}
        laatst = {}
        for moment, bezoeker, _speler, _bron in rijen:
            eerst[bezoeker] = min(eerst.get(bezoeker, moment), moment)
            laatst[bezoeker] = max(laatst.get(bezoeker, moment), moment)
        db.executemany("""
            INSERT INTO bezoekers (id, eerst, laatst) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET laatst = MAX(laatst, excluded.laatst)
        """, [(b, eerst[b], laatst[b]) for b in eerst])
    if time.time() - _laatste_onderhoud >= 3600:
        _laatste_onderhoud = time.time()
        onderhoud(db, datetime.now())
    db.commit()


def _dagtotaal(db, dag):
    """De totalen van één dag uit de losse paginabezoeken; None als er geen zijn."""
    # Een dag tellen met de avond ervoor erbij, zodat een bezoek dat om
    # middernacht doorloopt niet als nieuw bezoek meetelt.
    rijen = _rijen(db, f"{dag} 00:00:00", f"{dag} 23:59:59",
                   vanaf_context=_tijd(_dt(f"{dag} 00:00:00") - BEZOEK_PAUZE))
    dag_rijen = [r for r in rijen if r[0][:10] == dag]
    if not dag_rijen:
        return None
    nieuw = db.execute(
        "SELECT COUNT(*) FROM bezoekers WHERE substr(eerst, 1, 10) = ?",
        (dag,)).fetchone()[0]
    return {"datum": dag, "paginas": len(dag_rijen),
            "bezoekers": len({r[1] for r in dag_rijen}),
            "bezoeken": sum(1 for b in _bezoeken(rijen) if b["start"][:10] == dag),
            "ingelogd": len({r[1] for r in dag_rijen if r[2]}), "nieuw": nieuw}


def onderhoud(db, nu):
    """Afgelopen dagen samenvatten en oude losse paginabezoeken opruimen."""
    vandaag = nu.strftime("%Y-%m-%d")
    klaar = {r[0] for r in db.execute("SELECT datum FROM bezoeken_dag")}
    dagen = [r[0] for r in db.execute(
        "SELECT DISTINCT substr(moment, 1, 10) FROM bezoeken WHERE moment < ?",
        (vandaag,))]
    for dag in dagen:
        if dag in klaar:
            continue
        totaal = _dagtotaal(db, dag)
        if not totaal:
            continue
        db.execute("""
            INSERT OR REPLACE INTO bezoeken_dag
                (datum, paginas, bezoekers, bezoeken, ingelogd, nieuw)
            VALUES (:datum, :paginas, :bezoekers, :bezoeken, :ingelogd, :nieuw)""",
            totaal)
    grens = _tijd(nu - timedelta(days=RUW_BEWAREN_DAGEN + 1))
    db.execute("DELETE FROM bezoeken WHERE moment < ?", (grens,))
    db.execute("DELETE FROM bezoekers WHERE laatst < ?",
               (_tijd(nu - timedelta(days=BEZOEKERS_BEWAREN_DAGEN)),))


# ---------------------------------------------------------------- tellen --

def _tijd(dt):
    return dt.strftime(TIJD)


def _dt(tekst):
    return datetime.strptime(tekst, TIJD)


def _rijen(db, van, tot, vanaf_context=None):
    return [tuple(r) for r in db.execute(
        "SELECT moment, bezoeker, speler_id, bron FROM bezoeken "
        "WHERE moment >= ? AND moment <= ? ORDER BY moment",
        (vanaf_context or van, tot))]


def _bezoeken(rijen):
    """Paginabezoeken bundelen tot bezoeken: per bezoeker, met hoogstens een
    half uur stilte ertussen. Elk bezoek krijgt de bron van zijn eerste pagina."""
    per_bezoeker = defaultdict(list)
    for r in rijen:
        per_bezoeker[r[1]].append(r)
    uit = []
    for bezoeker, lijst in per_bezoeker.items():
        huidig = None
        vorige = None
        for moment, _b, speler_id, bron in lijst:
            t = _dt(moment)
            if huidig is None or t - vorige > BEZOEK_PAUZE:
                huidig = {"bezoeker": bezoeker, "start": moment, "bron": bron or DIRECT,
                          "speler_id": None, "paginas": 0}
                uit.append(huidig)
            huidig["paginas"] += 1
            huidig["eind"] = moment
            if speler_id and not huidig["speler_id"]:
                huidig["speler_id"] = speler_id
            vorige = t
    return uit


def _schaal(waarden):
    hoogste = max(waarden) if waarden else 0
    return hoogste or 1


def overzicht(db, nu):
    """Alles wat de bezoekerspagina toont."""
    begin30 = nu - timedelta(days=RUW_BEWAREN_DAGEN)
    rijen = _rijen(db, _tijd(begin30), _tijd(nu),
                   vanaf_context=_tijd(begin30 - BEZOEK_PAUZE))
    bezoeken_alle = _bezoeken(rijen)
    rijen_alle = rijen
    rijen = [r for r in rijen if r[0] >= _tijd(begin30)]
    bezoeken = [b for b in bezoeken_alle if b["start"] >= _tijd(begin30)]

    # Nu online
    grens = _tijd(nu - ONLINE_VENSTER)
    recent = [r for r in rijen if r[0] >= grens]
    nu_ingelogd = {r[2] for r in recent if r[2]}
    spelers_online = []
    for sid in nu_ingelogd:
        # Op meerdere toestellen tegelijk: het bezoek dat het laatst iets deed.
        laatste = max(_sessies(rijen_alle, sid), key=lambda x: x["eind"])
        spelers_online.append({"speler_id": sid, "sinds": laatste["start"],
                               "laatst": laatste["eind"],
                               "minuten": _minuten(laatste["start"], _tijd(nu))})
    spelers_online.sort(key=lambda s: s["sinds"])
    online = {"bezoekers": len({r[1] for r in recent}),
              "ingelogd": len(nu_ingelogd), "spelers": spelers_online}

    # Kerncijfers per periode
    perioden = []
    for label, dagen in (("Laatste 24 uur", 1), ("Laatste 7 dagen", 7),
                         ("Laatste 30 dagen", 30)):
        van = _tijd(nu - timedelta(days=dagen))
        deel = [r for r in rijen if r[0] >= van]
        perioden.append({
            "label": label,
            "bezoekers": len({r[1] for r in deel}),
            "bezoeken": sum(1 for b in bezoeken if b["start"] >= van),
            "paginas": len(deel),
        })

    grafieken = {
        "24u": _grafiek_uren(rijen, nu),
        "7d": _grafiek_dagen(rijen, nu, 7),
        "30d": _grafiek_dagen(rijen, nu, 30),
    }

    # Drukke momenten: bezoeken en pagina's per weekdag en uur (30 dagen)
    raster = [[0] * 24 for _ in range(7)]
    raster_p = [[0] * 24 for _ in range(7)]
    for b in bezoeken:
        t = _dt(b["start"])
        raster[t.weekday()][t.hour] += 1
    for r in rijen:
        t = _dt(r[0])
        raster_p[t.weekday()][t.hour] += 1
    hoogste = _schaal([w for rij in raster for w in rij])
    hoogste_p = _schaal([w for rij in raster_p for w in rij])
    drukte = [{"dag": DAGNAMEN[d],
               "uren": [{"uur": u, "aantal": raster[d][u],
                         "sterkte": raster[d][u] / hoogste,
                         "paginas": raster_p[d][u],
                         "sterkte_p": raster_p[d][u] / hoogste_p} for u in range(24)]}
              for d in range(7)]

    # Vanwaar
    tellen = Counter(b["bron"] for b in bezoeken)
    totaal = sum(tellen.values()) or 1
    bronnen = [{"naam": naam, "aantal": n, "pct": round(100 * n / totaal)}
               for naam, n in tellen.most_common(10)]
    rest = sum(tellen.values()) - sum(b["aantal"] for b in bronnen)
    if rest:
        bronnen.append({"naam": "Andere", "aantal": rest, "pct": round(100 * rest / totaal)})

    # Wie: ingelogd of niet, nieuw of terugkerend (30 dagen)
    alle_bezoekers = {r[1] for r in rijen}
    met_account = {r[1] for r in rijen if r[2]}
    nieuw = 0
    if alle_bezoekers:
        nieuw = db.execute(
            "SELECT COUNT(*) FROM bezoekers WHERE eerst >= ?",
            (_tijd(begin30),)).fetchone()[0]
        nieuw = min(nieuw, len(alle_bezoekers))
    sinds = db.execute("SELECT MIN(eerst) FROM bezoekers").fetchone()[0]
    wie = {
        "totaal": len(alle_bezoekers),
        "ingelogd": len(met_account),
        "anoniem": len(alle_bezoekers) - len(met_account),
        "nieuw": nieuw,
        "terug": len(alle_bezoekers) - nieuw,
        # Terugkerend kan pas na een maand echt iets zeggen.
        "nog_jong": not sinds or _dt(sinds) > begin30,
    }

    # Meest actieve accounts (30 dagen)
    per_speler = defaultdict(lambda: {"dagen": set(), "bezoeken": 0, "laatst": ""})
    for r in rijen:
        if r[2]:
            s = per_speler[r[2]]
            s["dagen"].add(r[0][:10])
            s["laatst"] = max(s["laatst"], r[0])
    for b in bezoeken:
        if b["speler_id"]:
            per_speler[b["speler_id"]]["bezoeken"] += 1
    actief = sorted(
        ({"speler_id": sid, "dagen": len(s["dagen"]), "bezoeken": s["bezoeken"],
          "laatst": s["laatst"],
          "minuten": sum(x["minuten"] for x in _sessies(rijen_alle, sid))}
         for sid, s in per_speler.items()),
        key=lambda s: (-s["dagen"], -s["bezoeken"], s["laatst"]))

    # Per maand, uit de dagtotalen (die blijven voor altijd). Dagen die nog niet
    # samengevat zijn (vandaag, of gisteren zolang het onderhoud nog niet
    # gedraaid heeft) worden hier meteen uit de losse paginabezoeken geteld,
    # zodat de maand niet achterloopt op de tegels bovenaan.
    dagen = {r["datum"]: dict(r) for r in db.execute(
        "SELECT datum, paginas, bezoekers, bezoeken FROM bezoeken_dag")}
    for (dag,) in db.execute(
            "SELECT DISTINCT substr(moment, 1, 10) FROM bezoeken WHERE moment <= ?",
            (_tijd(nu),)).fetchall():
        if dag not in dagen:
            totaal = _dagtotaal(db, dag)
            if totaal:
                dagen[dag] = totaal
    per_maand = defaultdict(list)
    for d in dagen.values():
        per_maand[d["datum"][:7]].append(d)
    maanden = []
    for maand in sorted(per_maand, reverse=True):
        lijst = per_maand[maand]
        jaar, mnd = maand.split("-")
        maanden.append({
            "maand": maand, "naam": f"{MAANDEN[int(mnd) - 1]} {jaar}",
            "paginas": sum(d["paginas"] for d in lijst),
            "bezoeken": sum(d["bezoeken"] for d in lijst),
            "per_dag": round(sum(d["bezoekers"] for d in lijst) / len(lijst), 1),
            "drukste": max(d["bezoekers"] for d in lijst),
            "dagen": len(lijst),
        })

    return {"online": online, "perioden": perioden, "grafieken": grafieken,
            "drukte": drukte, "bronnen": bronnen, "wie": wie, "actief": actief,
            "maanden": maanden, "sinds": sinds}


def _minuten(van, tot):
    return max(0, round((_dt(tot) - _dt(van)).total_seconds() / 60))


def _sessies(rijen, speler_id):
    """De momenten waarop één speler ingelogd op de site zat: zijn paginabezoeken
    per toestel gebundeld, met hoogstens een half uur stilte ertussen. De duur
    loopt van de eerste tot de laatste geopende pagina (wie daarna nog even
    blijft kijken, telt dus niet mee)."""
    eigen = [r for r in rijen if r[2] == speler_id]
    sessies = _bezoeken(eigen)
    for s in sessies:
        s["minuten"] = _minuten(s["start"], s["eind"])
    sessies.sort(key=lambda s: s["start"])
    return sessies


def historiek(db, speler_id, nu):
    """Wanneer één speler online was, over de laatste RUW_BEWAREN_DAGEN dagen."""
    begin = nu - timedelta(days=RUW_BEWAREN_DAGEN)
    rijen = [tuple(r) for r in db.execute(
        "SELECT moment, bezoeker, speler_id, bron FROM bezoeken "
        "WHERE speler_id = ? AND moment >= ? AND moment <= ? ORDER BY moment",
        (speler_id, _tijd(begin - BEZOEK_PAUZE), _tijd(nu)))]
    sessies = [s for s in _sessies(rijen, speler_id) if s["eind"] >= _tijd(begin)]
    online_grens = _tijd(nu - ONLINE_VENSTER)

    # Per dag: aantal keer en totale tijd, voor de balkjes en de lijst.
    per_dag = defaultdict(list)
    for s in sessies:
        per_dag[s["start"][:10]].append(s)
    dagen = []
    for i in range(RUW_BEWAREN_DAGEN - 1, -1, -1):
        dag = nu - timedelta(days=i)
        sleutel = dag.strftime("%Y-%m-%d")
        lijst = per_dag.get(sleutel, [])
        dagen.append({
            "datum": sleutel,
            "naam": f"{DAGNAMEN[dag.weekday()]} {dag.day} {MAANDEN[dag.month - 1]}",
            "label": f"{dag.day}/{dag.month}", "toon_label": i % 5 == 0,
            "sessies": [{**s, "van": s["start"][11:16], "tot": s["eind"][11:16],
                         "nu": s["eind"] >= online_grens} for s in lijst],
            "minuten": sum(s["minuten"] for s in lijst),
        })
    hoogste = _schaal([d["minuten"] or len(d["sessies"]) for d in dagen])
    for d in dagen:
        # Een kort bezoek (0 minuten) krijgt toch een klein balkje.
        d["hoogte"] = (round(100 * max(d["minuten"], 1) / max(hoogste, 1), 1)
                       if d["sessies"] else 0)

    # Op welke uren die speler meestal langskomt.
    uren = [0] * 24
    for s in sessies:
        uren[_dt(s["start"]).hour] += 1
    hoogste_uur = _schaal(uren)
    return {
        "nu_online": bool(sessies) and sessies[-1]["eind"] >= online_grens,
        "laatst": sessies[-1]["eind"] if sessies else None,
        "aantal": len(sessies),
        "dagen_actief": len(per_dag),
        "minuten": sum(s["minuten"] for s in sessies),
        "paginas": sum(s["paginas"] for s in sessies),
        "dagen": dagen,
        "lijst": [d for d in reversed(dagen) if d["sessies"]],
        "uren": [{"uur": u, "aantal": n, "sterkte": n / hoogste_uur}
                 for u, n in enumerate(uren)],
    }


def _kolommen(emmers):
    """Hoogte van elke balk, zowel voor bezoekers als voor pagina's."""
    hoogste = _schaal([e["bezoekers"] for e in emmers])
    hoogste_p = _schaal([e["paginas"] for e in emmers])
    for e in emmers:
        e["hoogte"] = round(100 * e["bezoekers"] / hoogste, 1)
        e["hoogte_p"] = round(100 * e["paginas"] / hoogste_p, 1)
    return emmers


def _grafiek_uren(rijen, nu):
    eind = nu.replace(minute=0, second=0, microsecond=0)
    emmers = []
    for i in range(23, -1, -1):
        van = eind - timedelta(hours=i)
        sleutel = van.strftime("%Y-%m-%d %H")
        deel = [r for r in rijen if r[0][:13] == sleutel]
        emmers.append({"label": f"{van:%H}u", "tip": f"{van:%H}:00–{van:%H}:59",
                       "bezoekers": len({r[1] for r in deel}), "paginas": len(deel),
                       "toon_label": van.hour % 3 == 0})
    return _kolommen(emmers)


def _grafiek_dagen(rijen, nu, aantal):
    emmers = []
    for i in range(aantal - 1, -1, -1):
        dag = nu - timedelta(days=i)
        sleutel = dag.strftime("%Y-%m-%d")
        deel = [r for r in rijen if r[0][:10] == sleutel]
        naam = f"{DAGNAMEN[dag.weekday()]} {dag.day} {MAANDEN[dag.month - 1]}"
        emmers.append({"label": (DAGNAMEN[dag.weekday()] if aantal <= 7
                                 else f"{dag.day}/{dag.month}"),
                       "tip": naam,
                       "bezoekers": len({r[1] for r in deel}), "paginas": len(deel),
                       "toon_label": aantal <= 7 or i % 5 == 0})
    return _kolommen(emmers)
