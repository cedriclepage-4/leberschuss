# -*- coding: utf-8 -*-
"""
Kansberekening voor een toernooi: wie raakt in de knockout, wie haalt de
finale, wie wint de beker?

Zoals de voorspellingen bij een WK: we spelen het toernooi duizenden keren na
op de computer. Elke wedstrijd die nog open staat, krijgt een winnaar volgens
de Aura van beide kanten; daarna gaat alles door precies dezelfde regels als
het echte toernooi (de stand en de tiebreaks uit tournament.py, shootouts,
het knockoutschema met nummer 1 tegen de laagst geplaatste). Hoe vaak een team
in al die afloopjes de kwartfinale haalde, is zijn kans op de kwartfinale.

Wat al gespeeld is, ligt vast. Na elke uitslag worden kansen dus feiten: wie
uitgeschakeld is, staat op 0%, wie zeker door is op 100%.

**De sterkte van een kant** is dezelfde als in de Aura-berekening zelf
(elo.zijde_rating): de helft de Aura van het team, de helft het gemiddelde van
de twee spelers. Twee nieuwe teams van 1000 zijn dus géén 50-50 als er bij het
ene twee sterkhouders in zitten. De winstkans volgt de klassieke ELO-formule:
200 punten verschil is ongeveer 76% tegen 24%.

De sterktes blijven tijdens één doorrekening vast; ze schuiven pas mee als er
echt gespeeld wordt. Het resultaat wordt bewaard tot er een uitslag of een
rating verandert, zodat honderd gsm's en de beamer samen maar één keer laten
rekenen.
"""

import hashlib
import random
import threading
import time

import tournament as tm
from elo import KLEINE_FINALE, fase_factor, is_voorronde, ko_label, verwacht, zijde_rating

# Hoe lang we hoogstens rekenen, en hoeveel afloopjes minimaal en maximaal.
# Met 1000 afloopjes zit een kans van 50% er hooguit een paar procent naast;
# meestal halen we er binnen de tijd een veelvoud van.
REKENTIJD = 1.0          # seconden
MIN_AFLOOPJES = 1000
MAX_AFLOOPJES = 20000
# De voorspelling van vóór de aftrap mag nooit nog verschuiven (bv. na een
# herstart van de server), dus die rekent altijd exact zoveel afloopjes.
AFLOOPJES_VOORAF = 3000

_cache = {}              # {tid: (vingerafdruk, uitkomst)}
_cache_vooraf = {}       # idem, voor de voorspelling van vóór de aftrap
_slot = threading.Lock()


# ------------------------------------------------------------- sterkte --

def sterktes(db, tid):
    """De sterkte van elk team in dit toernooi, nu: {team_id: rating}."""
    uit = {}
    for x in tm.deelnemers(db, tid, ook_teruggetrokken=True):
        uit[x["id"]] = zijde_rating(x["elo"], [x["spelers_elo"]])
    return uit


def sterktes_vooraf(db, tid):
    """De sterkte van elk team zoals ze was bij de aftrap van dit toernooi.

    Uit de ratinggeschiedenis: de rating van elk team en elke speler net vóór
    zijn eerste wedstrijd in dit toernooi. Wie nog niet speelde, staat nog op
    zijn huidige rating — die is sindsdien niet veranderd door dit toernooi.
    """
    voor = {}
    for r in db.execute("""
        SELECT rh.entity_type, rh.entity_id, rh.elo_voor
        FROM rating_history rh JOIN games g ON g.id = rh.game_id
        WHERE g.tournament_id = ? AND rh.scope = 'permanent'
        ORDER BY g.played_at DESC, g.id DESC, rh.id DESC
    """, (tid,)):
        voor[(r["entity_type"], r["entity_id"])] = r["elo_voor"]   # de vroegste wint
    spelers = {r["id"]: r["elo"] for r in db.execute("SELECT id, elo FROM players")}
    uit = {}
    for x in tm.deelnemers(db, tid, ook_teruggetrokken=True):
        team = voor.get(("team", x["id"]), x["elo"])
        aura = [voor.get(("speler", pid), spelers.get(pid))
                for pid in (x["player1_id"], x["player2_id"]) if pid in spelers]
        uit[x["id"]] = zijde_rating(team, [sum(aura) / len(aura)] if aura else [team])
    return uit


def winstkans(sterkte, a, b):
    """Kans dat team a wint van team b."""
    return verwacht(sterkte.get(a, 1000.0), sterkte.get(b, 1000.0))


def _ronde_label(grootte):
    if grootte == 1:
        return "Kampioen"
    if is_voorronde(grootte):
        return "Knockout"          # bv. 12: in de knockout, al dan niet via de voorronde
    return ko_label(grootte)


def _vingerafdruk(db, tid, sterkte):
    t = tm.toernooi(db, tid)
    h = hashlib.sha1(tm._uitslagen_afdruk(db, tid).encode())
    h.update(f"{t['status']}:{t['ko_teams']}".encode())
    for team_id in sorted(sterkte):
        h.update(f"{team_id}:{sterkte[team_id]:.4f};".encode())
    return h.hexdigest()


# --------------------------------------------------------- doorrekenen --

def bereken(db, tid):
    """De kansen van elk team, of None als er (nog) niets te berekenen valt.

    Geeft een dict:
      rondes      [{"grootte": 8, "label": "Kwartfinale"}, ..., {"grootte": 1,
                   "label": "Kampioen"}] — de eerste is "in de knockout"
      kansen      {team_id: {grootte: kans}}
      ranglijst   team_ids, meest waarschijnlijke kampioen eerst
      afloopjes   hoeveel keer het toernooi nagespeeld is
      sterkte     {team_id: rating}
    """
    t = tm.toernooi(db, tid)
    if not t or t["status"] == "opzet" or not tm.geldig_ko_aantal(t["ko_teams"]):
        return None
    sterkte = sterktes(db, tid)
    afdruk = _vingerafdruk(db, tid, sterkte)
    bewaard = _cache.get(tid)
    if bewaard and bewaard[0] == afdruk:
        return bewaard[1]
    with _slot:
        bewaard = _cache.get(tid)            # misschien rekende iemand anders net
        if bewaard and bewaard[0] == afdruk:
            return bewaard[1]
        uitkomst = _reken(db, t, sterkte, random.Random(afdruk))
        _cache[tid] = (afdruk, uitkomst)
        return uitkomst


def bereken_vooraf(db, tid):
    """De voorspelling zoals de motor ze vóór de eerste wedstrijd zou maken.

    Niets daarvan wordt bewaard: we rekenen ze achteraf opnieuw uit, met de
    ratings van bij de aftrap (sterktes_vooraf) en het hele programma alsof er
    nog niets gespeeld is. Zo werkt het ook voor toernooien die al lang voorbij
    zijn. Hetzelfde formaat als `bereken`; None vóór de loting.
    """
    t = tm.toernooi(db, tid)
    if not t or t["status"] == "opzet" or not tm.geldig_ko_aantal(t["ko_teams"]):
        return None
    sterkte = sterktes_vooraf(db, tid)
    h = hashlib.sha1(f"vooraf:{t['ko_teams']}".encode())
    for g in tm.games_van(db, tid, "bracket"):
        h.update(f"{g['id']}:{g['team1_id']}:{g['team2_id']};".encode())
    for team_id in sorted(sterkte):
        h.update(f"{team_id}:{sterkte[team_id]:.4f};".encode())
    afdruk = h.hexdigest()
    bewaard = _cache_vooraf.get(tid)
    if bewaard and bewaard[0] == afdruk:
        return bewaard[1]
    with _slot:
        bewaard = _cache_vooraf.get(tid)
        if bewaard and bewaard[0] == afdruk:
            return bewaard[1]
        uitkomst = _reken(db, t, sterkte, random.Random(afdruk), vooraf=True)
        _cache_vooraf[tid] = (afdruk, uitkomst)
        return uitkomst


def _reken(db, t, sterkte, rng, vooraf=False):
    tid, cut = t["id"], t["ko_teams"]
    # Bij een knockout met voorronde (bv. 12) eerst "in de knockout" (12), dan
    # de eerste volle ronde (8), 4, 2, 1.
    grootten = [cut] if not tm.is_macht_van_twee(cut) else []
    g = tm.voorronde_info(cut)[2]
    while g >= 1:
        grootten.append(g)
        g //= 2
    teller = {}                                   # {team_id: {grootte: aantal}}

    def tel(team_id, grootte):
        if team_id is not None:
            per = teller.setdefault(team_id, {})
            per[grootte] = per.get(grootte, 0) + 1

    def speel(a, b):
        """Winnaar van een verzonnen wedstrijd. Een leeg vak verliest altijd."""
        if a is None or b is None:
            return a if b is None else b
        return a if rng.random() < winstkans(sterkte, a, b) else b

    if t["status"] in ("knockout", "afgelopen") and not vooraf:
        afloopje = _knockout_afloopje(db, tid, speel, tel)
        deelnemers = set(_knockout_teams(db, tid)) | {x["id"] for x in tm.deelnemers(db, tid)}
    else:
        afloopje = _bracket_afloopje(db, t, sterkte, speel, tel, rng, vooraf)
        deelnemers = {x["id"] for x in tm.deelnemers(db, tid)}

    # Niets meer te spelen? Dan is één afloopje het hele verhaal.
    open_games = db.execute("SELECT COUNT(*) FROM games WHERE tournament_id = ? AND "
                            "status = 'gepland' AND NOT (fase = 'knockout' AND ronde = ?)",
                            (tid, KLEINE_FINALE)).fetchone()[0]
    maximum = AFLOOPJES_VOORAF if vooraf else (MAX_AFLOOPJES if open_games else 1)

    n = 0
    begin = time.perf_counter()
    while n < maximum:
        afloopje()
        n += 1
        if (not vooraf and n >= MIN_AFLOOPJES and n % 100 == 0
                and time.perf_counter() - begin > REKENTIJD):
            break

    kansen = {team_id: {gr: teller.get(team_id, {}).get(gr, 0) / n for gr in grootten}
              for team_id in deelnemers}
    ranglijst = sorted(kansen, key=lambda x: tuple(-kansen[x][gr] for gr in reversed(grootten))
                       + (-sterkte.get(x, 0),))
    return {"rondes": [{"grootte": gr, "label": _ronde_label(gr)} for gr in grootten],
            "kansen": kansen, "ranglijst": ranglijst, "afloopjes": n,
            "sterkte": sterkte}


def _knockout_teams(db, tid):
    for g in tm.games_van(db, tid, "knockout"):
        for kant in (g["team1_id"], g["team2_id"]):
            if kant is not None and g["ronde"] != KLEINE_FINALE:
                yield kant


def _knockout_afloopje(db, tid, speel, tel):
    """Eén afloopje van een lopende knockout, op het echte schema."""
    games = [g for g in tm.games_van(db, tid, "knockout") if g["ronde"] != KLEINE_FINALE]
    games.sort(key=lambda g: (-g["ronde"], g["positie"]))
    # Enkel de vakken van de eerste ronde zijn vast; de rest vult het afloopje
    # zelf in met zijn eigen winnaars (bij gespeelde wedstrijden de echte).
    # Een vak waar geen wedstrijd in uitmondt, is vast: de eerste ronde, en de
    # plaatsen van wie vrijgeloot is voor de voorronde. Die vrijgelote teams
    # zitten ook "in de knockout" (de eerste grootte, bv. 12).
    eerste = max((g["ronde"] for g in games), default=0)
    gevoed = {(g["volgende_game_id"], g["volgende_slot"]) for g in games
              if g["volgende_game_id"]}

    def afloopje():
        vak = {}
        for g in games:
            kanten = []
            for slot in (1, 2):
                if (g["id"], slot) in gevoed:
                    kanten.append(vak.get((g["id"], slot)))
                else:
                    kanten.append(g[f"team{slot}_id"])
                    if g["ronde"] != eerste:
                        tel(kanten[-1], eerste)          # vrijgeloot
            a, b = kanten
            tel(a, g["ronde"])
            tel(b, g["ronde"])
            if g["status"] == "gespeeld" and g["winner_team_id"]:
                w = g["winner_team_id"]
            else:
                w = speel(a, b)
            if g["volgende_game_id"]:
                vak[(g["volgende_game_id"], g["volgende_slot"])] = w
            elif g["ronde"] == 2:
                tel(w, 1)
    return afloopje


def _bracket_afloopje(db, t, sterkte, speel, tel, rng, vooraf=False):
    """Eén afloopje vanuit de bracketfase: rest van de bracket, eventuele
    shootouts en daarna de hele knockout. Met `vooraf` alsof er nog niets
    gespeeld is."""
    tid, cut = t["id"], t["ko_teams"]
    teams = {x["id"]: x for x in tm.deelnemers(db, tid)}
    bracket = [dict(g) for g in tm.games_van(db, tid, "bracket")]
    shootouts = [dict(g) for g in tm.games_van(db, tid, "shootout")]
    if vooraf:
        bracket = [dict(g, status="gepland", winner_team_id=None) for g in bracket]
        shootouts = []
    vast_b = [g for g in bracket if g["status"] == "gespeeld"]
    open_b = [g for g in bracket if g["status"] != "gespeeld"]
    vast_s = [g for g in shootouts if g["status"] == "gespeeld"]
    open_s = [g for g in shootouts if g["status"] != "gespeeld"
              and g["team1_id"] and g["team2_id"]]
    basis_kracht = {} if vooraf else tm.elo_na_bracketfase(db, tid)
    # De toernooikracht (de Aura die je vanavond won of verloor) is een van de
    # laatste tiebreaks. Voor verzonnen wedstrijden schatten we hem met dezelfde
    # formule als de echte Aura van een team, aan het bracketgewicht.
    k_rij = db.execute("SELECT value FROM settings WHERE key = 'k_team'").fetchone()
    k_team = float(k_rij["value"]) if k_rij else 32.0
    k = k_team * fase_factor("bracket")
    volgorde = tm.seed_volgorde(tm.schema_grootte(cut))

    def afloopje():
        kracht = dict(basis_kracht)
        gespeeld = list(vast_b)
        for g in open_b:
            a, b = g["team1_id"], g["team2_id"]
            w = speel(a, b)
            gespeeld.append(dict(g, status="gespeeld", winner_team_id=w))
            if a in teams and b in teams:
                p = winstkans(sterkte, a, b)
                s = 1.0 if w == a else 0.0
                kracht[a] = kracht.get(a, 0.0) + k * (s - p)
                kracht[b] = kracht.get(b, 0.0) - k * (s - p)
        so = list(vast_s) + [dict(g, status="gespeeld",
                                  winner_team_id=speel(g["team1_id"], g["team2_id"]))
                             for g in open_s]
        geordend, beslissend = tm.stand_uit(tid, cut, teams, gespeeld, so, kracht)
        # Gelijk rond de streep? Dan één ronde shootouts, net als in het echt.
        for _ in range(3):
            if not beslissend:
                break
            for groep in beslissend:
                for a, b in tm._shootout_paren(groep, rng):
                    so.append({"id": None, "team1_id": a, "team2_id": b,
                               "status": "gespeeld", "winner_team_id": speel(a, b),
                               "fase": "shootout"})
            geordend, beslissend = tm.stand_uit(tid, cut, teams, gespeeld, so, kracht)

        geplaatst = [r["team_id"] for r in geordend[:cut]]
        geplaatst += [None] * (len(volgorde) - len(geplaatst))
        # Plaatsen voorbij de cut zijn leeg: wie daartegen "speelt", is
        # vrijgeloot (speel() laat een leeg vak altijd verliezen).
        veld = [geplaatst[s - 1] for s in volgorde]
        grootte = len(volgorde)
        while len(veld) > 1:
            for x in veld:
                tel(x, cut if grootte == len(volgorde) else grootte)
            veld = [speel(veld[i], veld[i + 1]) for i in range(0, len(veld), 2)]
            grootte //= 2
        tel(veld[0], 1)
    return afloopje


# ------------------------------------------------------------ weergave --

def team_kansen(uitkomst, team_id):
    """De kansen van één team als lijst [{"label", "grootte", "kans"}], of None."""
    if not uitkomst or team_id not in uitkomst["kansen"]:
        return None
    per = uitkomst["kansen"][team_id]
    return [{"label": r["label"], "grootte": r["grootte"], "kans": per[r["grootte"]]}
            for r in uitkomst["rondes"]]


def volgende_stap(uitkomst, team_id):
    """De eerstvolgende ronde die voor dit team nog niet vastligt, of None.

    In de bracketfase is dat de knockout zelf; zit je in de halve finale, dan is
    het de finale. Ben je uitgeschakeld of kampioen, dan is er geen volgende stap.
    """
    rij = team_kansen(uitkomst, team_id)
    if not rij:
        return None
    for r in rij:
        if r["kans"] < 1.0:
            return r if r["kans"] > 0.0 else None
    return None


def eindfase(uitkomst, team_id):
    """Hoe ver een team geraakt(e): het label van de verste ronde die het
    zeker haalde, of "Bracketfase" als het niet in de knockout raakte."""
    rij = team_kansen(uitkomst, team_id) or []
    gehaald = [r for r in rij if r["kans"] >= 1.0]
    return gehaald[-1] if gehaald else {"label": "Bracketfase", "grootte": None, "kans": 1.0}


def voorspellingen(uitkomst):
    """Het ingevulde schema dat de motor zou voorspellen: {team_id: fase}.

    Zoals een kenner zijn bracket invult: één kampioen, één verliezende
    finalist, twee halvefinalisten, ... en de rest blijft in de bracketfase.
    Daarvoor rangschikken we de teams op hoe ver ze gemiddeld geraken (de som
    van hun kansen op elke ronde), bij gelijkheid op hun titelkans.

    Per team apart "de verste ronde met meer dan 50% kans" nemen klopt niet:
    dan zitten er soms veertien teams in een kwartfinale met acht plaatsen.
    """
    if not uitkomst:
        return {}
    rondes = uitkomst["rondes"]
    volgorde = sorted(uitkomst["kansen"], key=lambda x: (
        -sum(uitkomst["kansen"][x][r["grootte"]] for r in rondes),
        -uitkomst["kansen"][x][1], -uitkomst["sterkte"].get(x, 0), x))
    uit = {}
    for plaats, team_id in enumerate(volgorde, start=1):
        # De verste ronde met minstens zoveel plaatsen als dit team zijn rang.
        haalbaar = [r for r in rondes if r["grootte"] >= plaats]
        fase = haalbaar[-1] if haalbaar else None
        uit[team_id] = ({"label": fase["label"], "grootte": fase["grootte"]} if fase
                        else {"label": "Bracketfase", "grootte": None})
        uit[team_id]["plaats"] = plaats
    return uit


def diepte(fase):
    """Hoe ver een fase komt, om te vergelijken: Bracketfase = 0, ... kampioen."""
    return 0 if fase["grootte"] is None else 1000 // fase["grootte"]
