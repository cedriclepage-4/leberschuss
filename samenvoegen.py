# -*- coding: utf-8 -*-
"""Dubbele accounts en teams samenvoegen (voor de organisatie).

Wie zijn wachtwoord vergeet, maakt soms gewoon een tweede account aan, en met
dezelfde ploegmaat een tweede team ("Oop & Goop 2"). Zonder mailsysteem is er
geen "wachtwoord vergeten?"; de organisator lost het op door de twee samen te
voegen. Eén account (of team) blijft, het andere verdwijnt, en **alles** van
het verdwijnende verhuist mee: wedstrijden, inschrijvingen, meldingen,
statistieken, uitnodigingen, coupons, claimlogboek.

Werkwijze
---------
1. `plan_spelers` / `plan_teams` bekijken vooraf wat er zou gebeuren en wat het
   onmogelijk maakt (`bezwaren`). Dat toont de organisatiepagina als voorbeeld.
2. `spelers_samenvoegen` / `teams_samenvoegen` doen het echt. Ze committen
   niet: de oproeper herberekent daarna alle Aura (`herbereken_alles`) en
   commit pas dan, zodat alles in één keer lukt of niets.

Een speler samenvoegen maakt soms twee teams gelijk (Noah + Sam en Noah' + Sam
worden twee keer Noah + Sam). Die teams worden in dezelfde beurt mee
samengevoegd: het resultaat is precies één account en één team.

Wanneer kan het niet?
---------------------
* De twee zitten samen in één team, of hun teams speelden tegen elkaar: na het
  samenvoegen zou iemand tegen zichzelf gespeeld hebben.
* Beide staan in hetzelfde toernooi dat al geloot is: dan zou het schema niet
  meer kloppen. Vóór de loting is het geen probleem; de vroegste inschrijving
  blijft dan staan.
* Een team kan enkel samengaan met een team van (een deel van) dezelfde spelers.
"""

from database import ROL_ADMIN, ROL_EIGENAAR, ROL_SPELER, _SPELER_VERWIJZINGEN

ROL_RANG = {ROL_SPELER: 0, ROL_ADMIN: 1, ROL_EIGENAAR: 2}


class SamenvoegFout(Exception):
    """Samenvoegen kan niet; `bezwaren` zegt waarom."""

    def __init__(self, bezwaren):
        super().__init__("; ".join(bezwaren))
        self.bezwaren = list(bezwaren)


def _naam(speler):
    return (speler["nickname"] or "").strip() or speler["name"]


def _leden(team):
    return {p for p in (team["player1_id"], team["player2_id"]) if p is not None}


def _teams_van(db, speler_id):
    return db.execute("SELECT * FROM teams WHERE player1_id = ? OR player2_id = ? "
                      "ORDER BY id", (speler_id, speler_id)).fetchall()


def aantal_wedstrijden(db, team_ids):
    if not team_ids:
        return 0
    v = ",".join("?" * len(team_ids))
    return db.execute(f"SELECT COUNT(*) FROM games WHERE team1_id IN ({v}) "
                      f"OR team2_id IN ({v})", (*team_ids, *team_ids)).fetchone()[0]


def _onderling(db, a_ids, b_ids):
    """Wedstrijden waarin een team uit a tegen een team uit b speelde."""
    if not a_ids or not b_ids:
        return []
    va, vb = ",".join("?" * len(a_ids)), ",".join("?" * len(b_ids))
    return db.execute(f"""
        SELECT g.*, t1.name AS naam1, t2.name AS naam2 FROM games g
        JOIN teams t1 ON t1.id = g.team1_id JOIN teams t2 ON t2.id = g.team2_id
        WHERE (g.team1_id IN ({va}) AND g.team2_id IN ({vb}))
           OR (g.team1_id IN ({vb}) AND g.team2_id IN ({va}))
    """, (*a_ids, *b_ids, *b_ids, *a_ids)).fetchall()


def toernooien_van(db, team_id):
    return {r["id"]: r for r in db.execute("""
        SELECT tn.id, tn.name, tn.status, tt.id AS inschrijving
        FROM tournament_teams tt JOIN tournaments tn ON tn.id = tt.tournament_id
        WHERE tt.team_id = ?""", (team_id,))}


# ------------------------------------------------------------------ teams --

def plan_teams(db, bron_id, doel_id, speler_map=None):
    """Wat gebeurt er als team `bron` opgaat in team `doel`?

    `speler_map` ({oud: nieuw}) laat toe dit te bekijken alsof een
    spelerssamenvoeging al gebeurd is (zie `plan_spelers`).
    """
    speler_map = speler_map or {}
    bron = db.execute("SELECT * FROM teams WHERE id = ?", (bron_id,)).fetchone()
    doel = db.execute("SELECT * FROM teams WHERE id = ?", (doel_id,)).fetchone()
    bezwaren, opmerkingen = [], []
    if not bron or not doel:
        return {"bron": bron, "doel": doel, "bezwaren": ["Team niet gevonden."],
                "opmerkingen": [], "wedstrijden": 0, "toernooien": []}
    if bron_id == doel_id:
        bezwaren.append("Kies twee verschillende teams.")

    leden_bron = {speler_map.get(p, p) for p in _leden(bron)}
    leden_doel = {speler_map.get(p, p) for p in _leden(doel)}
    if not leden_bron <= leden_doel:
        bezwaren.append(f"“{bron['name']}” en “{doel['name']}” hebben niet dezelfde "
                        "spelers. Enkel teams van dezelfde twee spelers kunnen samengaan.")

    for g in _onderling(db, [bron_id], [doel_id]):
        bezwaren.append(f"“{g['naam1']}” en “{g['naam2']}” speelden tegen elkaar "
                        f"(wedstrijd #{g['id']}). Samen zouden ze tegen zichzelf "
                        "gespeeld hebben; verwijder die wedstrijd eerst.")

    tn_bron, tn_doel = toernooien_van(db, bron_id), toernooien_van(db, doel_id)
    for tid in tn_bron.keys() & tn_doel.keys():
        t = tn_bron[tid]
        if t["status"] == "opzet":
            opmerkingen.append(f"Beide staan ingeschreven voor “{t['name']}”: de "
                               "vroegste inschrijving blijft staan.")
        else:
            bezwaren.append(f"Beide teams spelen mee in “{t['name']}” (al geloot). "
                            "Trek één van beide eerst terug of verwijder het toernooi.")

    alle = sorted({**tn_doel, **tn_bron}.values(), key=lambda t: t["id"])
    return {"bron": bron, "doel": doel, "bezwaren": bezwaren,
            "opmerkingen": opmerkingen,
            "wedstrijden": aantal_wedstrijden(db, [bron_id]),
            "toernooien": [t["name"] for t in alle]}


def teams_samenvoegen(db, bron_id, doel_id, naam=None, speler_map=None):
    """Voeg team `bron` samen in team `doel`. Commit niet (zie de module-uitleg).

    `naam`: de naam die het samengevoegde team krijgt (een van de twee of een
    nieuwe); leeg = de naam van `doel`. Geeft de profielfoto's terug die niet
    meer gebruikt worden, om na het committen op te ruimen.
    """
    plan = plan_teams(db, bron_id, doel_id, speler_map)
    if plan["bezwaren"]:
        raise SamenvoegFout(plan["bezwaren"])
    bron, doel = plan["bron"], plan["doel"]
    naam = " ".join((naam or "").split()) or doel["name"]
    if len(naam) > 40:
        raise SamenvoegFout(["Hou de teamnaam onder de 40 tekens."])
    if naam not in (bron["name"], doel["name"]) and db.execute(
            "SELECT 1 FROM teams WHERE name = ?", (naam,)).fetchone():
        raise SamenvoegFout([f"Er bestaat al een team met de naam “{naam}”."])

    # Toernooien: staan ze er allebei (enkel vóór de loting mogelijk, zie het
    # plan), dan blijft de vroegste inschrijving: die bepaalt de wachtlijst.
    tn_bron, tn_doel = toernooien_van(db, bron_id), toernooien_van(db, doel_id)
    for tid in tn_bron.keys() & tn_doel.keys():
        vroegst, laatst = sorted((tn_bron[tid]["inschrijving"], tn_doel[tid]["inschrijving"]))
        db.execute("DELETE FROM coupon_inlossingen WHERE tournament_id = ? AND vriend_team_id = ?",
                   (tid, bron_id if laatst == tn_bron[tid]["inschrijving"] else doel_id))
        db.execute("DELETE FROM tournament_teams WHERE id = ?", (laatst,))
    db.execute("UPDATE tournament_teams SET team_id = ? WHERE team_id = ?", (doel_id, bron_id))
    db.execute("UPDATE OR IGNORE coupon_inlossingen SET vriend_team_id = ? "
               "WHERE vriend_team_id = ?", (doel_id, bron_id))
    db.execute("DELETE FROM coupon_inlossingen WHERE vriend_team_id = ?", (bron_id,))

    # Wedstrijden en wat eraan hangt. Onderlinge wedstrijden zijn uitgesloten,
    # dus een team speelt nooit tegen zichzelf en meldingen botsen niet.
    for kolom in ("team1_id", "team2_id", "winner_team_id"):
        db.execute(f"UPDATE games SET {kolom} = ? WHERE {kolom} = ?", (doel_id, bron_id))
    for kolom in ("team_id", "winner_team_id"):
        db.execute(f"UPDATE game_reports SET {kolom} = ? WHERE {kolom} = ?", (doel_id, bron_id))

    # League: inschrijvingen per seizoen en de wachtrij (dubbels vallen weg),
    # uitdagingen tussen de twee zelf hebben geen zin meer.
    for tabel in ("season_teams", "wachtrij"):
        db.execute(f"UPDATE OR IGNORE {tabel} SET team_id = ? WHERE team_id = ?",
                   (doel_id, bron_id))
        db.execute(f"DELETE FROM {tabel} WHERE team_id = ?", (bron_id,))
    db.execute("DELETE FROM challenges WHERE (van_team_id = ? AND naar_team_id = ?) "
               "OR (van_team_id = ? AND naar_team_id = ?)", (bron_id, doel_id, doel_id, bron_id))
    for kolom in ("van_team_id", "naar_team_id"):
        db.execute(f"UPDATE challenges SET {kolom} = ? WHERE {kolom} = ?", (doel_id, bron_id))

    # Ratings worden toch volledig herberekend; enkel opruimen.
    db.execute("DELETE FROM rating_history WHERE entity_type = 'team' AND entity_id = ?", (bron_id,))
    db.execute("DELETE FROM season_ratings WHERE entity_type = 'team' AND entity_id = ?", (bron_id,))
    db.execute("DELETE FROM team_invitations WHERE team_id = ?", (bron_id,))

    # Eerst het oude team weg: de naam is uniek en mag misschien overgenomen worden.
    db.execute("DELETE FROM teams WHERE id = ?", (bron_id,))
    db.execute("""
        UPDATE teams SET name = ?,
               description = CASE WHEN description = '' THEN ? ELSE description END,
               avatar = COALESCE(avatar, ?),
               created_at = MIN(created_at, ?)
        WHERE id = ?""", (naam, bron["description"], bron["avatar"], bron["created_at"], doel_id))
    _schoon_inschrijvingen(db)

    weg = [bron["avatar"]] if bron["avatar"] and doel["avatar"] else []
    return {"naam": naam, "weg_te_gooien": weg}


def _schoon_inschrijvingen(db):
    """Na het verhuizen: niemand bevestigt zijn eigen inschrijving, en niemand
    verdient een coupon met een uitnodiging aan zijn eigen team."""
    db.execute("UPDATE tournament_teams SET te_bevestigen = NULL "
               "WHERE te_bevestigen = ingeschreven_door")
    db.execute("""
        UPDATE tournament_teams SET uitgenodigd_door = NULL
        WHERE uitgenodigd_door IN (SELECT player1_id FROM teams WHERE id = team_id
                                   UNION SELECT player2_id FROM teams WHERE id = team_id)""")


# ---------------------------------------------------------------- spelers --

def _uitnodigingen(db):
    """{team_id: genodigde speler} voor alle openstaande uitnodigingen."""
    return {r["team_id"]: r["player_id"] for r in db.execute(
        "SELECT team_id, player_id FROM team_invitations")}


def _dubbele_teams(teams, speler_map, uitnodigingen):
    """Teams die na het samenvoegen van spelers precies dezelfde spelers hebben.

    Een onvolledig team telt zijn openstaande uitnodiging mee: wie zijn
    wachtwoord vergat, maakt een nieuw team en nodigt dezelfde maat uit, maar
    die maat aanvaardt niet altijd (zeker niet als hij nooit inlogt).

    Geeft [(doel_team, [bron_teams])]. Een volledig team blijft altijd boven
    een onvolledig; daarna het team van het blijvende account; daarna het oudste.
    """
    groepen = {}
    for t in teams:
        leden = {speler_map.get(p, p) for p in _leden(t)}
        if t["player2_id"] is None and t["id"] in uitnodigingen:
            leden.add(speler_map.get(uitnodigingen[t["id"]], uitnodigingen[t["id"]]))
        groepen.setdefault(tuple(sorted(leden)), []).append(t)
    uit = []
    for groep in groepen.values():
        if len(groep) < 2:
            continue
        doel_spelers = set(speler_map.values())
        doel = min(groep, key=lambda t: (t["player2_id"] is None,
                                         not (_leden(t) & doel_spelers), t["id"]))
        uit.append((doel, [t for t in groep if t["id"] != doel["id"]]))
    return uit


def plan_spelers(db, bron_id, doel_id):
    """Wat gebeurt er als account `bron` opgaat in account `doel`?"""
    bron = db.execute("SELECT * FROM players WHERE id = ?", (bron_id,)).fetchone()
    doel = db.execute("SELECT * FROM players WHERE id = ?", (doel_id,)).fetchone()
    leeg = {"bron": bron, "doel": doel, "bezwaren": [], "opmerkingen": [],
            "team_samenvoegingen": [], "teams_bron": [], "wedstrijden": 0}
    if not bron or not doel:
        leeg["bezwaren"] = ["Speler niet gevonden."]
        return leeg
    if bron_id == doel_id:
        leeg["bezwaren"] = ["Kies twee verschillende spelers."]
        return leeg

    bezwaren, opmerkingen = [], []
    if bron["role"] == ROL_EIGENAAR:
        bezwaren.append("Het account van de eigenaar kan niet verdwijnen. Kies het "
                        "andere account als het account dat verdwijnt.")

    teams_bron, teams_doel = _teams_van(db, bron_id), _teams_van(db, doel_id)
    ids_bron = [t["id"] for t in teams_bron]
    ids_doel = [t["id"] for t in teams_doel]
    for t in teams_bron:
        if t["id"] in ids_doel:
            bezwaren.append(f"{_naam(bron)} en {_naam(doel)} zitten samen in team "
                            f"“{t['name']}”. Verwijder of herschik dat team eerst.")
    for g in _onderling(db, ids_bron, ids_doel):
        bezwaren.append(f"“{g['naam1']}” speelde tegen “{g['naam2']}” (wedstrijd "
                        f"#{g['id']}): na het samenvoegen zou {_naam(doel)} tegen "
                        "zichzelf gespeeld hebben. Verwijder die wedstrijd eerst.")

    speler_map = {bron_id: doel_id}
    alle_teams = {t["id"]: t for t in teams_bron + teams_doel}
    samen = _dubbele_teams(alle_teams.values(), speler_map, _uitnodigingen(db))
    team_plannen = []
    for t_doel, bronnen in samen:
        for t_bron in bronnen:
            p = plan_teams(db, t_bron["id"], t_doel["id"], speler_map)
            # De onderlinge wedstrijden staan hierboven al.
            bezwaren += [b for b in p["bezwaren"] if "tegen elkaar" not in b]
            opmerkingen += p["opmerkingen"]
            team_plannen.append(p)

    # Een speler kan niet in twee verschillende teams in hetzelfde toernooi zitten.
    samengaand = {t["id"] for p in team_plannen for t in (p["bron"], p["doel"])}
    per_toernooi = {}
    for t in alle_teams.values():
        if t["id"] in samengaand:
            continue
        for tid, tn in toernooien_van(db, t["id"]).items():
            per_toernooi.setdefault(tid, (tn, []))[1].append(t)
    for tn, teams in per_toernooi.values():
        eigenaars = {bron_id if t["id"] in ids_bron else doel_id for t in teams}
        if len(eigenaars) < 2:
            continue
        namen = " en ".join(f"“{t['name']}”" for t in teams)
        if tn["status"] == "afgelopen":
            opmerkingen.append(f"In “{tn['name']}” speelde deze persoon met twee "
                               f"verschillende teams ({namen}). Dat blijft zo in de historiek.")
        else:
            bezwaren.append(f"{namen} staan allebei in “{tn['name']}”, met een andere "
                            "ploegmaat. Eén speler kan niet in twee teams van hetzelfde "
                            "toernooi zitten: schrijf er eerst één uit.")

    return {"bron": bron, "doel": doel, "bezwaren": bezwaren, "opmerkingen": opmerkingen,
            "team_samenvoegingen": team_plannen, "teams_bron": teams_bron,
            "wedstrijden": aantal_wedstrijden(db, ids_bron)}


def spelers_samenvoegen(db, bron_id, doel_id, wachtwoord_van_bron=True, naam=None,
                        teamnamen=None):
    """Voeg account `bron` samen in account `doel`. Commit niet.

    Het nummer en de historiek van `doel` blijven, die van `bron` komen erbij.
    `wachtwoord_van_bron`: het wachtwoord van `bron` wordt het wachtwoord van
    het samengevoegde account. Dat is meestal wat je wil: wie een tweede
    account maakte, deed dat omdat hij het eerste wachtwoord vergeten was, en
    kent net dat nieuwe. Heeft `bron` geen wachtwoord, dan blijft dat van
    `doel`. `naam`: de echte naam (= login) van het samengevoegde account, een
    van de twee; leeg = die van `doel`. Bijnaam en kroonkurk komen van `doel`,
    tenzij die leeg zijn. `teamnamen` ({doel_team_id: naam}) kiest de naam van
    teams die mee samengaan.
    """
    plan = plan_spelers(db, bron_id, doel_id)
    if plan["bezwaren"]:
        raise SamenvoegFout(plan["bezwaren"])
    bron, doel = plan["bron"], plan["doel"]
    teamnamen = teamnamen or {}
    naam = naam or doel["name"]
    if naam not in (bron["name"], doel["name"]):
        raise SamenvoegFout(["Kies de naam van een van beide accounts."])

    # Uitnodigingen die na het samenvoegen naar je eigen team zouden gaan.
    db.execute("""
        DELETE FROM team_invitations WHERE player_id = ? AND team_id IN
            (SELECT id FROM teams WHERE player1_id = ? OR player2_id = ?)""",
               (bron_id, doel_id, doel_id))
    db.execute("""
        DELETE FROM team_invitations WHERE player_id = ? AND team_id IN
            (SELECT id FROM teams WHERE player1_id = ? OR player2_id = ?)""",
               (doel_id, bron_id, bron_id))
    # Ratings worden achteraf volledig herberekend.
    db.execute("DELETE FROM rating_history WHERE entity_type = 'speler' AND entity_id = ?", (bron_id,))
    db.execute("DELETE FROM season_ratings WHERE entity_type = 'speler' AND entity_id = ?", (bron_id,))
    if doel["aangemaakt_door"] == bron_id:
        db.execute("UPDATE players SET aangemaakt_door = NULL WHERE id = ?", (doel_id,))

    for tabel, kolom, extra in _SPELER_VERWIJZINGEN:
        waar = f" AND {extra}" if extra else ""
        db.execute(f"UPDATE {tabel} SET {kolom} = ? WHERE {kolom} = ?{waar}",
                   (doel_id, bron_id))
    db.execute("UPDATE claim_log SET player_id = ? WHERE player_id = ?", (doel_id, bron_id))

    # Teams die nu dezelfde twee spelers hebben, gaan mee samen.
    weg = []
    namen = []
    for p in plan["team_samenvoegingen"]:
        r = teams_samenvoegen(db, p["bron"]["id"], p["doel"]["id"],
                              teamnamen.get(p["doel"]["id"]))
        weg += r["weg_te_gooien"]
        namen.append(r["naam"])

    # Het account zelf. Eerst het oude weg: de naam is uniek.
    rol = max(bron["role"], doel["role"], key=lambda r: ROL_RANG.get(r, 0))
    if wachtwoord_van_bron and bron["password_hash"]:
        wachtwoord = bron["password_hash"]
    else:
        wachtwoord = doel["password_hash"] or bron["password_hash"]
    db.execute("DELETE FROM players WHERE id = ?", (bron_id,))
    db.execute("""
        UPDATE players SET name = ?, password_hash = ?, role = ?,
               nickname = CASE WHEN nickname = '' THEN ? ELSE nickname END,
               dop = COALESCE(dop, ?), dop_ontwerpen = COALESCE(dop_ontwerpen, ?),
               avatar = COALESCE(avatar, ?),
               active = MAX(active, ?), created_at = MIN(created_at, ?)
        WHERE id = ?""",
               (naam, wachtwoord, rol, bron["nickname"], bron["dop"], bron["dop_ontwerpen"],
                bron["avatar"], bron["active"], bron["created_at"], doel_id))
    _schoon_inschrijvingen(db)

    if bron["avatar"] and doel["avatar"]:
        weg.append(bron["avatar"])
    return {"naam": naam, "teams": namen, "weg_te_gooien": weg}


# ------------------------------------------------------------ suggesties --

def _naam_vervat(a, b):
    """Staat de ene naam volledig in de andere ("Noah" in "Noah Van den Broeck")?"""
    ta, tb = set((a or "").lower().split()), set((b or "").lower().split())
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def mogelijke_dubbels(db, lijkt_op):
    """Paren accounts die waarschijnlijk dezelfde persoon zijn.

    `lijkt_op(a, b)` vergelijkt twee namen (zie `app.naam_lijkt_op`). Een
    gedeelde ploegmaat maakt het vermoeden sterker en zet het paar vooraan.
    """
    spelers = db.execute("SELECT * FROM players ORDER BY created_at, id").fetchall()
    # Ploegmaten, ook wie enkel uitgenodigd is (en nog niet aanvaardde).
    maten = {}
    for a, b in db.execute("""
            SELECT player1_id, player2_id FROM teams WHERE player2_id IS NOT NULL
            UNION SELECT t.player1_id, i.player_id FROM team_invitations i
                  JOIN teams t ON t.id = i.team_id"""):
        maten.setdefault(a, set()).add(b)
        maten.setdefault(b, set()).add(a)
    paren = []
    for i, a in enumerate(spelers):
        for b in spelers[i + 1:]:
            namen_a = {a["name"], (a["nickname"] or "").strip()} - {""}
            namen_b = {b["name"], (b["nickname"] or "").strip()} - {""}
            gedeeld = maten.get(a["id"], set()) & maten.get(b["id"], set())
            # "Noah" en "Noah Van den Broeck" lijken op zich niet genoeg op
            # elkaar; met dezelfde ploegmaat erbij wel. (Twee Nicks met een
            # andere achternaam en een gedeelde maat blijven wel apart.)
            vervat = gedeeld and _naam_vervat(a["name"], b["name"])
            if not vervat and not any(lijkt_op(x, y) for x in namen_a for y in namen_b):
                continue
            # Het oudste account blijft standaard: daar zit de meeste historiek.
            paren.append({"doel": a, "bron": b, "gedeelde_maat": bool(gedeeld)})
    paren.sort(key=lambda p: not p["gedeelde_maat"])
    return paren


def mogelijke_dubbele_teams(db, lijkt_op):
    """Teams met dezelfde spelers, of een gedeelde speler en een gelijkaardige naam."""
    teams = db.execute("SELECT * FROM teams ORDER BY id").fetchall()
    paren = []
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            gedeeld = _leden(a) & _leden(b)
            if not gedeeld:
                continue
            zelfde = _leden(a) == _leden(b) or _leden(b) <= _leden(a)
            if zelfde or lijkt_op(a["name"], b["name"]):
                paren.append({"doel": a, "bron": b, "zelfde_spelers": zelfde})
    return paren
