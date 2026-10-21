# -*- coding: utf-8 -*-
"""
Leaguemotor voor Leberschuss Tonzent.

Een seizoen bestaat uit speeldagen. Teams schrijven zich in voor een seizoen en
staan dan meteen in het klassement — ook vóór hun eerste wedstrijd.

Op een speeldag ontstaan wedstrijden op twee manieren:

* **Uitdagen.** Team A daagt team B uit; B aanvaardt of weigert. Bij aanvaarden
  staat de wedstrijd meteen gepland.
* **De wachtrij.** Je zet je team in de rij en wacht op een tegenstander. Zodra
  er een tafel vrijkomt kijkt `koppel_wachtrij` naar iedereen die wacht en maakt
  de best mogelijke koppels.

Over dat koppelen: het gebeurt bewust *niet* zodra er twee teams staan te
wachten. Met twee teams heb je geen keuze — je krijgt de affiche die er toevallig
is, en op een rustige avond keert diezelfde affiche dan telkens terug. Daarom
wacht de rij tot er echt iets te kiezen valt (`POEL` teams), of tot iemand lang
genoeg gewacht heeft (`GEDULD` minuten) zodat een stille avond nooit stilvalt.
"""

import math
from datetime import date, datetime, timedelta

TIJDFORMAAT = "%Y-%m-%dT%H:%M"

# Vanaf zoveel wachtende teams valt er genoeg te kiezen om meteen te koppelen.
POEL = 4
# Zolang wacht een team maximaal op meer keuze; daarna koppelen we sowieso.
GEDULD_MINUTEN = 5

# Gewichten voor de koppeling. Een verschil in Aura telt als "punten"; de rest
# wordt daar tegen afgewogen.
STRAF_VANAVOND = 10000      # dezelfde affiche nog eens op dezelfde avond
STRAF_SEIZOEN = 300         # per eerdere ontmoeting dit seizoen
BONUS_MINDER_GESPEELD = 40  # per wedstrijd achterstand vanavond


# ------------------------------------------------------------ inschrijven --

def ingeschreven(db, seizoen_id):
    """De teams die zich voor dit seizoen ingeschreven hebben."""
    return db.execute("""
        SELECT t.*, st.moment
        FROM season_teams st JOIN teams t ON t.id = st.team_id
        WHERE st.season_id = ?
        ORDER BY t.name
    """, (seizoen_id,)).fetchall()


def is_ingeschreven(db, seizoen_id, team_id):
    return db.execute("SELECT 1 FROM season_teams WHERE season_id = ? AND team_id = ?",
                      (seizoen_id, team_id)).fetchone() is not None


def inschrijven(db, seizoen_id, team_id):
    """Schrijf een team in. Geeft (gelukt, boodschap)."""
    seizoen = db.execute("SELECT * FROM seasons WHERE id = ?", (seizoen_id,)).fetchone()
    if not seizoen:
        return False, "Dat seizoen bestaat niet."
    team = db.execute("SELECT * FROM teams WHERE id = ? AND status = 'actief'",
                      (team_id,)).fetchone()
    if not team:
        return False, "Dat team bestaat niet of is nog niet actief."
    if is_ingeschreven(db, seizoen_id, team_id):
        return False, f"“{team['name']}” is al ingeschreven."
    db.execute("INSERT INTO season_teams (season_id, team_id) VALUES (?, ?)",
               (seizoen_id, team_id))
    db.commit()
    return True, (f"“{team['name']}” doet mee aan {seizoen['name']} en staat nu in "
                  "het klassement.")


def uitschrijven(db, seizoen_id, team_id):
    """Schrijf een team uit — enkel zolang het nog niets gespeeld heeft."""
    gespeeld = db.execute("""
        SELECT COUNT(*) AS n FROM games g JOIN matchdays md ON md.id = g.matchday_id
        WHERE md.season_id = ? AND (g.team1_id = ? OR g.team2_id = ?)
    """, (seizoen_id, team_id, team_id)).fetchone()["n"]
    if gespeeld:
        return False, ("Dit team heeft al wedstrijden in dit seizoen. Uitschrijven zou "
                       "die uitslagen in de lucht laten hangen.")
    db.execute("DELETE FROM season_teams WHERE season_id = ? AND team_id = ?",
               (seizoen_id, team_id))
    db.commit()
    return True, "Team uitgeschreven."


# --------------------------------------------------------------- uitdagen --

def _speeldag(db, speeldag_id):
    return db.execute("SELECT * FROM matchdays WHERE id = ?", (speeldag_id,)).fetchone()


def bezig(db, speeldag_id):
    """Teams die op dit moment een wedstrijd hebben openstaan op deze speeldag."""
    rijen = db.execute("""
        SELECT team1_id, team2_id FROM games
        WHERE matchday_id = ? AND status = 'gepland'
    """, (speeldag_id,)).fetchall()
    return {t for r in rijen for t in (r["team1_id"], r["team2_id"])}


def uitdagingen(db, speeldag_id, team_id=None):
    """Alle openstaande uitdagingen van een speeldag."""
    sql = """SELECT c.*, v.name AS van_naam, n.name AS naar_naam
             FROM challenges c
             JOIN teams v ON v.id = c.van_team_id
             JOIN teams n ON n.id = c.naar_team_id
             WHERE c.matchday_id = ? AND c.status = 'open'"""
    args = [speeldag_id]
    if team_id is not None:
        sql += " AND (c.van_team_id = ? OR c.naar_team_id = ?)"
        args += [team_id, team_id]
    return db.execute(sql + " ORDER BY c.moment", args).fetchall()


def uitdagen(db, speeldag_id, van_team_id, naar_team_id):
    """Daag een ander team uit voor een wedstrijd op deze speeldag."""
    sd = _speeldag(db, speeldag_id)
    if not sd:
        return False, "Die speeldag bestaat niet."
    if van_team_id == naar_team_id:
        return False, "Je kan jezelf niet uitdagen."
    namen = {r["id"]: r["name"] for r in db.execute(
        "SELECT id, name FROM teams WHERE id IN (?, ?)", (van_team_id, naar_team_id))}
    if len(namen) != 2:
        return False, "Een van beide teams bestaat niet."
    for kant in (van_team_id, naar_team_id):
        if not is_ingeschreven(db, sd["season_id"], kant):
            return False, (f"“{namen[kant]}” is nog niet ingeschreven voor dit seizoen.")
    if _delen_speler(db, van_team_id, naar_team_id):
        return False, "Deze teams delen een speler en kunnen niet tegen elkaar spelen."
    aan_de_gang = bezig(db, speeldag_id)
    for kant in (van_team_id, naar_team_id):
        if kant in aan_de_gang:
            return False, f"“{namen[kant]}” heeft al een wedstrijd openstaan."
    bestaat = db.execute("""
        SELECT 1 FROM challenges WHERE matchday_id = ? AND status = 'open'
          AND ((van_team_id = ? AND naar_team_id = ?)
            OR (van_team_id = ? AND naar_team_id = ?))
    """, (speeldag_id, van_team_id, naar_team_id,
          naar_team_id, van_team_id)).fetchone()
    if bestaat:
        return False, "Er staat al een uitdaging open tussen deze twee teams."
    db.execute("INSERT INTO challenges (matchday_id, van_team_id, naar_team_id) "
               "VALUES (?, ?, ?)", (speeldag_id, van_team_id, naar_team_id))
    db.commit()
    return True, f"“{namen[naar_team_id]}” is uitgedaagd."


def _delen_speler(db, a, b):
    rijen = db.execute("SELECT id, player1_id, player2_id FROM teams WHERE id IN (?, ?)",
                       (a, b)).fetchall()
    if len(rijen) != 2:
        return False
    spelers = [{r["player1_id"], r["player2_id"]} for r in rijen]
    return bool(spelers[0] & spelers[1])


def beantwoord_uitdaging(db, uitdaging_id, aanvaard, nu=None):
    """Aanvaard of weiger een uitdaging. Aanvaarden plant meteen de wedstrijd."""
    c = db.execute("SELECT * FROM challenges WHERE id = ?", (uitdaging_id,)).fetchone()
    if not c or c["status"] != "open":
        return False, "Deze uitdaging staat niet meer open."
    if not aanvaard:
        db.execute("UPDATE challenges SET status = 'geweigerd' WHERE id = ?",
                   (uitdaging_id,))
        db.commit()
        return True, "Uitdaging geweigerd."

    aan_de_gang = bezig(db, c["matchday_id"])
    if c["van_team_id"] in aan_de_gang or c["naar_team_id"] in aan_de_gang:
        return False, "Een van beide teams speelt al een wedstrijd."
    game_id = _maak_wedstrijd(db, c["matchday_id"], c["van_team_id"],
                              c["naar_team_id"], nu)
    db.execute("UPDATE challenges SET status = 'aanvaard', game_id = ? WHERE id = ?",
               (game_id, uitdaging_id))
    # Beide teams staan nu te spelen: uit de rij en andere uitdagingen vervallen.
    _uit_de_rij(db, c["matchday_id"], (c["van_team_id"], c["naar_team_id"]))
    db.commit()
    return True, "Uitdaging aanvaard, de wedstrijd staat gepland."


def _maak_wedstrijd(db, speeldag_id, team1, team2, nu=None):
    moment = (nu or datetime.now()).strftime(TIJDFORMAAT)
    cur = db.execute("INSERT INTO games (team1_id, team2_id, matchday_id, fase, "
                     "scheduled_at) VALUES (?, ?, ?, 'liga', ?)",
                     (team1, team2, speeldag_id, moment))
    return cur.lastrowid


def _uit_de_rij(db, speeldag_id, teams):
    for t in teams:
        db.execute("DELETE FROM wachtrij WHERE matchday_id = ? AND team_id = ?",
                   (speeldag_id, t))
        db.execute("""UPDATE challenges SET status = 'ingetrokken'
                      WHERE matchday_id = ? AND status = 'open'
                        AND (van_team_id = ? OR naar_team_id = ?)""",
                   (speeldag_id, t, t))


# -------------------------------------------------------------- wachtrij --

def wachtrij(db, speeldag_id):
    """Wie staat er te wachten, oudste eerst."""
    return db.execute("""
        SELECT w.*, t.name AS naam, t.elo
        FROM wachtrij w JOIN teams t ON t.id = w.team_id
        WHERE w.matchday_id = ? ORDER BY w.sinds, w.id
    """, (speeldag_id,)).fetchall()


def aansluiten(db, speeldag_id, team_id, nu=None):
    """Zet een team in de wachtrij."""
    sd = _speeldag(db, speeldag_id)
    if not sd:
        return False, "Die speeldag bestaat niet."
    if not is_ingeschreven(db, sd["season_id"], team_id):
        return False, "Schrijf je eerst in voor dit seizoen."
    if team_id in bezig(db, speeldag_id):
        return False, "Je hebt al een wedstrijd openstaan."
    if db.execute("SELECT 1 FROM wachtrij WHERE matchday_id = ? AND team_id = ?",
                  (speeldag_id, team_id)).fetchone():
        return False, "Je staat al in de wachtrij."
    db.execute("INSERT INTO wachtrij (matchday_id, team_id, sinds) VALUES (?, ?, ?)",
               (speeldag_id, team_id, (nu or datetime.now()).strftime(TIJDFORMAAT)))
    db.commit()
    return True, "Je staat in de wachtrij."


def verlaten(db, speeldag_id, team_id):
    db.execute("DELETE FROM wachtrij WHERE matchday_id = ? AND team_id = ?",
               (speeldag_id, team_id))
    db.commit()
    return True, "Je staat niet meer in de wachtrij."


def _ontmoetingen(db, speeldag_id, seizoen_id):
    """Hoe vaak speelde elk duo al tegen elkaar: vanavond, en dit seizoen."""
    vanavond, seizoen = {}, {}
    for r in db.execute("""
        SELECT g.team1_id, g.team2_id, g.matchday_id
        FROM games g JOIN matchdays md ON md.id = g.matchday_id
        WHERE md.season_id = ?
    """, (seizoen_id,)):
        paar = frozenset((r["team1_id"], r["team2_id"]))
        seizoen[paar] = seizoen.get(paar, 0) + 1
        if r["matchday_id"] == speeldag_id:
            vanavond[paar] = vanavond.get(paar, 0) + 1
    return vanavond, seizoen


def _gespeeld_vanavond(db, speeldag_id):
    tellen = {}
    for r in db.execute("SELECT team1_id, team2_id FROM games WHERE matchday_id = ?",
                        (speeldag_id,)):
        for t in (r["team1_id"], r["team2_id"]):
            tellen[t] = tellen.get(t, 0) + 1
    return tellen


def _kost(a, b, elo, vanavond, seizoen, gespeeld, meeste):
    """Hoe graag zetten we deze twee tegenover elkaar? Lager is beter."""
    paar = frozenset((a, b))
    kost = abs(elo[a] - elo[b])
    kost += STRAF_VANAVOND * vanavond.get(paar, 0)
    kost += STRAF_SEIZOEN * seizoen.get(paar, 0)
    # Wie vanavond achterloopt, krijgt voorrang.
    achterstand = meeste - max(gespeeld.get(a, 0), gespeeld.get(b, 0))
    kost -= BONUS_MINDER_GESPEELD * achterstand
    return kost


def koppel_wachtrij(db, speeldag_id, nu=None):
    """Maak wedstrijden uit de wachtrij. Geeft een lijst meldingen terug.

    Draait telkens wanneer er iets verandert (iemand sluit aan, een uitslag komt
    binnen, de pagina wordt geladen). Er wordt pas gekoppeld als er een tafel
    vrij is én er echt iets te kiezen valt — anders wachten we nog even.
    """
    sd = _speeldag(db, speeldag_id)
    if not sd:
        return []
    nu = nu or datetime.now()

    lopend = db.execute("SELECT COUNT(*) AS n FROM games WHERE matchday_id = ? "
                        "AND status = 'gepland'", (speeldag_id,)).fetchone()["n"]
    vrij = max(0, (sd["tafels"] or 0) - lopend)
    if vrij <= 0:
        return []

    rij = [r for r in wachtrij(db, speeldag_id)]
    aan_de_gang = bezig(db, speeldag_id)
    rij = [r for r in rij if r["team_id"] not in aan_de_gang]
    if len(rij) < 2:
        return []

    # Genoeg keuze, of wacht er al iemand lang genoeg?
    def wachtminuten(r):
        try:
            return (nu - datetime.strptime(r["sinds"], TIJDFORMAAT)).total_seconds() / 60
        except (ValueError, TypeError):
            return GEDULD_MINUTEN
    langst = max(wachtminuten(r) for r in rij)
    if len(rij) < POEL and langst < GEDULD_MINUTEN:
        return []

    elo = {r["team_id"]: r["elo"] for r in rij}
    vanavond, seizoen = _ontmoetingen(db, speeldag_id, sd["season_id"])
    gespeeld = _gespeeld_vanavond(db, speeldag_id)
    meeste = max(gespeeld.values(), default=0)

    beschikbaar = [r["team_id"] for r in rij]        # al gesorteerd op wachttijd
    meldingen = []
    namen = {r["team_id"]: r["naam"] for r in rij}
    for _ in range(vrij):
        if len(beschikbaar) < 2:
            break
        a = beschikbaar[0]                            # wie het langst wacht
        kandidaten = [x for x in beschikbaar[1:] if not _delen_speler(db, a, x)]
        if not kandidaten:
            break
        b = min(kandidaten,
                key=lambda x: (_kost(a, x, elo, vanavond, seizoen, gespeeld, meeste), x))
        game_id = _maak_wedstrijd(db, speeldag_id, a, b, nu)
        _uit_de_rij(db, speeldag_id, (a, b))
        vanavond[frozenset((a, b))] = vanavond.get(frozenset((a, b)), 0) + 1
        gespeeld[a] = gespeeld.get(a, 0) + 1
        gespeeld[b] = gespeeld.get(b, 0) + 1
        beschikbaar.remove(a)
        beschikbaar.remove(b)
        meldingen.append(f"Uit de wachtrij: {namen[a]} tegen {namen[b]}.")
    db.commit()
    return meldingen


def speeldag_vandaag(db, vandaag=None):
    """De speeldag van vandaag, of anders de eerstvolgende binnen een week.

    Zo staat de knop naar "vanavond" op de thuispagina en op de seizoenpagina,
    in plaats van drie klikken diep.
    """
    vandaag = vandaag or date.today().isoformat()
    sd = db.execute("""
        SELECT md.*, s.name AS seizoen, s.id AS seizoen_id
        FROM matchdays md JOIN seasons s ON s.id = md.season_id
        WHERE md.date = ? ORDER BY md.id LIMIT 1
    """, (vandaag,)).fetchone()
    if sd:
        return sd, True
    sd = db.execute("""
        SELECT md.*, s.name AS seizoen, s.id AS seizoen_id
        FROM matchdays md JOIN seasons s ON s.id = md.season_id
        WHERE md.date > ? AND md.date <= date(?, '+7 day')
        ORDER BY md.date, md.id LIMIT 1
    """, (vandaag, vandaag)).fetchone()
    return sd, False


def mijn_speeldagstatus(db, speeldag_id, seizoen_id, teams):
    """Wat kan deze speler nú doen op deze speeldag?

    Geeft (klaar_om_te_zoeken, zoekt_al, speelt_al, niet_ingeschreven) terug —
    telkens de lijst teams in die toestand.
    """
    aangemeld = {t["id"] for t in ingeschreven(db, seizoen_id)}
    in_rij = {r["team_id"] for r in wachtrij(db, speeldag_id)}
    aan_de_gang = bezig(db, speeldag_id)
    klaar, zoekt, speelt, buiten = [], [], [], []
    for t in teams:
        if t["id"] not in aangemeld:
            buiten.append(t)
        elif t["id"] in aan_de_gang:
            speelt.append(t)
        elif t["id"] in in_rij:
            zoekt.append(t)
        else:
            klaar.append(t)
    return klaar, zoekt, speelt, buiten


def wachtrij_status(db, speeldag_id, nu=None):
    """Wat kan de bezoeker verwachten? Voor de uitleg op het scherm."""
    sd = _speeldag(db, speeldag_id)
    if not sd:
        return None
    nu = nu or datetime.now()
    rij = wachtrij(db, speeldag_id)
    lopend = db.execute("SELECT COUNT(*) AS n FROM games WHERE matchday_id = ? "
                        "AND status = 'gepland'", (speeldag_id,)).fetchone()["n"]
    vrij = max(0, (sd["tafels"] or 0) - lopend)
    reden = None
    if not rij:
        reden = "Er staat nog niemand in de wachtrij."
    elif len(rij) < 2:
        reden = "Er is nog maar één team dat wacht op een tegenstander."
    elif vrij <= 0:
        reden = ("Alle tafels zijn bezet. Zodra er een wedstrijd afgelopen is, "
                 "worden de volgende koppels gemaakt.")
    elif len(rij) < POEL:
        reden = (f"Nog even wachten op meer keuze: vanaf {POEL} wachtende teams "
                 f"koppelen we meteen, en anders ten laatste na {GEDULD_MINUTEN} "
                 "minuten.")
    return {"rij": rij, "tafels": sd["tafels"] or 0, "lopend": lopend,
            "vrij": vrij, "reden": reden}
