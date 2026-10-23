# -*- coding: utf-8 -*-
"""Databaselaag van Leberschuss Tonzent (SQLite) — schema v5 (liga + toernooien).

Nieuw in v5
-----------
* Spelers en teams hebben een **permanente Aura** (`players.elo`, `teams.elo`):
  die telt alles mee, zowel ligawedstrijden als toernooiwedstrijden.
* Daarnaast heeft elk **seizoen zijn eigen Aura** (`season_ratings`): bij de start
  van een seizoen begint iedereen opnieuw op 1000. Enkel ligawedstrijden tellen
  mee voor de seizoens-Aura.
* De site is opgesplitst in twee delen: **liga** (seizoenen, speeldagen) en
  **toernooi** (bracketfase + knockout), met dezelfde spelers en teams.
* Er is geen algemeen adminwachtwoord meer: elke speler heeft een **rol**
  (`players.role`) — gewone speler, organisator of eigenaar.
"""

import os
import random
import shutil
import sqlite3
from datetime import datetime

# Spelersnummers zijn 4 cijfers en worden willekeurig toegekend, zodat je aan het
# nummer niet kan zien wie zich als eerste registreerde.
SPELER_MIN, SPELER_MAX = 1000, 9999

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "shuss.db")

# Rollen: 'speler' (gewoon), 'admin' (organisator) en 'eigenaar' (hoofdorganisator,
# er is er altijd hoogstens één). Er is geen algemeen adminwachtwoord meer.
ROL_SPELER, ROL_ADMIN, ROL_EIGENAAR = "speler", "admin", "eigenaar"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,          -- echte naam, wordt niet gewijzigd
    nickname      TEXT NOT NULL DEFAULT '',      -- vrij aanpasbaar
    password_hash TEXT,
    role          TEXT NOT NULL DEFAULT 'speler',
    elo           REAL NOT NULL DEFAULT 1000,   -- permanente Aura (liga + toernooi)
    active        INTEGER NOT NULL DEFAULT 1,
    avatar        TEXT,                          -- oude profielfoto, niet meer getoond
    -- De kroonkurk (avatar) als JSON, zie dop.py. Leeg = de gewone basisdop.
    dop           TEXT,
    -- Tot drie bewaarde ontwerpen (JSON-lijst) om tussen te wisselen.
    dop_ontwerpen TEXT,
    -- Werd dit account aangemaakt door een ploegmaat die zich voor een toernooi
    -- inschreef? Dan mag de echte persoon het overnemen door zich te registreren
    -- met precies die naam (zie app.registreren). Accounts van de organisatie
    -- blijven achter het opeisvenster.
    aangemaakt_door INTEGER REFERENCES players(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Teams worden door spelers zelf gemaakt: speler 1 (maker) nodigt speler 2 uit.
-- Pas na aanvaarden wordt het team 'actief' en kan het wedstrijden spelen.
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    player1_id  INTEGER NOT NULL REFERENCES players(id),   -- wie het team oprichtte
    player2_id  INTEGER REFERENCES players(id),            -- leeg zolang onvolledig
    status      TEXT NOT NULL DEFAULT 'onvolledig'
                CHECK (status IN ('onvolledig', 'actief')),
    avatar      TEXT,
    elo         REAL NOT NULL DEFAULT 1000,     -- permanente Aura (liga + toernooi)
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Een onvolledig team (één speler) kan één openstaande uitnodiging hebben.
-- Aanvaardt de genodigde, dan wordt hij de tweede speler en is het team actief.
-- Weigert hij, dan verdwijnt enkel de uitnodiging: het team blijft bestaan.
CREATE TABLE IF NOT EXISTS team_invitations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    INTEGER NOT NULL UNIQUE REFERENCES teams(id) ON DELETE CASCADE,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------------------------------------------------------------- liga --

CREATE TABLE IF NOT EXISTS seasons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS matchdays (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    title     TEXT NOT NULL,
    date      TEXT NOT NULL,
    -- Hoeveel wedstrijden kunnen er die avond tegelijk lopen? De wachtrij
    -- koppelt nooit meer teams dan er tafels vrij zijn.
    tafels    INTEGER NOT NULL DEFAULT 2
);

-- Teams schrijven zich in voor een seizoen. Ze staan meteen in het klassement,
-- ook vóór hun eerste wedstrijd — anders lijkt het alsof ze niet meedoen.
CREATE TABLE IF NOT EXISTS season_teams (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    team_id   INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    moment    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (season_id, team_id)
);

-- "Uitdagen": het ene team vraagt, het andere aanvaardt of weigert.
CREATE TABLE IF NOT EXISTS challenges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matchday_id INTEGER NOT NULL REFERENCES matchdays(id) ON DELETE CASCADE,
    van_team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    naar_team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'aanvaard', 'geweigerd', 'ingetrokken')),
    game_id     INTEGER REFERENCES games(id) ON DELETE SET NULL,
    moment      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- De wachtrij van een speeldag: wie staat er klaar voor een wedstrijd?
CREATE TABLE IF NOT EXISTS wachtrij (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matchday_id INTEGER NOT NULL REFERENCES matchdays(id) ON DELETE CASCADE,
    team_id     INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    sinds       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (matchday_id, team_id)
);

-- Aura binnen één seizoen: iedereen start er op 1000.
CREATE TABLE IF NOT EXISTS season_ratings (
    season_id   INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('speler', 'team')),
    entity_id   INTEGER NOT NULL,
    elo         REAL NOT NULL,
    gespeeld    INTEGER NOT NULL DEFAULT 0,
    winst       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (season_id, entity_type, entity_id)
);

-- ----------------------------------------------------------- toernooi --

CREATE TABLE IF NOT EXISTS tournaments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    description    TEXT NOT NULL DEFAULT '',
    date           TEXT NOT NULL,                  -- speeldatum (JJJJ-MM-DD)
    start_tijd     TEXT NOT NULL DEFAULT '19:00',
    bracket_ronden INTEGER NOT NULL DEFAULT 4,     -- games per team in de bracketfase
    ko_teams       INTEGER NOT NULL DEFAULT 8,     -- hoeveel teams naar de knockout
    potten         INTEGER NOT NULL DEFAULT 4,
    slot_minuten   INTEGER NOT NULL DEFAULT 20,
    max_teams      INTEGER,                        -- leeg = geen maximum
    uitnodigingen_per_speler INTEGER NOT NULL DEFAULT 1,  -- teams per speler uit te nodigen (0 = geen limiet)
    status         TEXT NOT NULL DEFAULT 'opzet'
                   CHECK (status IN ('opzet', 'bracket', 'knockout', 'afgelopen')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS tournament_locations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    UNIQUE (tournament_id, name)
);

CREATE TABLE IF NOT EXISTS tournament_teams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    team_id       INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    pot           INTEGER,
    seed          INTEGER,
    start_elo     REAL,
    -- Kwam dit team niet opdagen? Dan tellen zijn wedstrijden niet mee en
    -- worden de resterende affiches opnieuw geloot (zie tournament.terugtrekken).
    teruggetrokken INTEGER NOT NULL DEFAULT 0,
    -- Wie schreef dit team in, en wanneer? Leeg = toegevoegd door de organisatie.
    -- De volgorde van inschrijven (id) bepaalt wie op de wachtlijst staat.
    ingeschreven_door INTEGER REFERENCES players(id) ON DELETE SET NULL,
    ingeschreven_op   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- Schreef iemand een nieuw team in met een bestaande speler? Dan krijgt die
    -- speler een melding ("dit klopt niet" maakt het ongedaan) tot hij ze bevestigt.
    te_bevestigen     INTEGER REFERENCES players(id) ON DELETE SET NULL,
    -- Via wiens uitnodigingslink schreef dit team zich in? Blijft het team
    -- ingeschreven tot de loting, dan krijgt die speler een coupon.
    uitgenodigd_door  INTEGER REFERENCES players(id) ON DELETE SET NULL,
    UNIQUE (tournament_id, team_id)
);

-- ---------------------------------------------------------- wedstrijden --

-- Eén tabel voor álle wedstrijden: liga én toernooi. Zo delen beide delen van
-- de site dezelfde spelers, teams, meldingen en statistieken.
CREATE TABLE IF NOT EXISTS games (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team1_id         INTEGER REFERENCES teams(id),
    team2_id         INTEGER REFERENCES teams(id),
    matchday_id      INTEGER REFERENCES matchdays(id) ON DELETE SET NULL,
    tournament_id    INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
    fase             TEXT NOT NULL DEFAULT 'liga'
                     CHECK (fase IN ('liga', 'bracket', 'shootout', 'knockout')),
    ronde            INTEGER,   -- bracket: rondenummer; knockout: aantal teams (16, 8, 4, 2)
    positie          INTEGER,   -- plaats binnen de knockoutronde (bracketweergave)
    volgende_game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
    volgende_slot    INTEGER,   -- 1 of 2: in welk vak de winnaar terechtkomt
    location_id      INTEGER REFERENCES tournament_locations(id) ON DELETE SET NULL,
    scheduled_at     TEXT NOT NULL,
    played_at        TEXT,
    status           TEXT NOT NULL DEFAULT 'gepland'
                     CHECK (status IN ('gepland', 'gespeeld')),
    winner_team_id   INTEGER REFERENCES teams(id),
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Types statistieken die organisatoren zelf kunnen aanmaken (bv. "saves").
CREATE TABLE IF NOT EXISTS stat_types (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    unit       TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Waarden van die statistieken, per wedstrijd en per speler (geen invloed op Aura).
CREATE TABLE IF NOT EXISTS game_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id      INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    player_id    INTEGER NOT NULL REFERENCES players(id),
    stat_type_id INTEGER NOT NULL REFERENCES stat_types(id) ON DELETE CASCADE,
    value        REAL NOT NULL,
    UNIQUE (game_id, player_id, stat_type_id)
);

-- Meldingen van spelers over de uitslag (één per team per wedstrijd).
CREATE TABLE IF NOT EXISTS game_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    player_id      INTEGER NOT NULL REFERENCES players(id),
    winner_team_id INTEGER NOT NULL REFERENCES teams(id),
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (game_id, team_id)
);

-- Historiek van elke Aura-wijziging. scope = 'permanent' (alles) of 'seizoen'.
CREATE TABLE IF NOT EXISTS rating_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('speler', 'team')),
    entity_id   INTEGER NOT NULL,
    elo_voor    REAL NOT NULL,
    elo_na      REAL NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'permanent'
                CHECK (scope IN ('permanent', 'seizoen')),
    season_id   INTEGER
);

-- Zelf te definiëren rangen, bv. "Zilver" van 900 tot 1000 Aura.
CREATE TABLE IF NOT EXISTS ranks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    min_elo REAL NOT NULL,
    max_elo REAL NOT NULL,
    color   TEXT NOT NULL DEFAULT '#64748b'
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Logboek van opgeëiste accounts. De organisator maakt spelers aan zonder
-- wachtwoord; de speler zelf kiest er later één ("claimen"). Elke claim wordt
-- hier bijgehouden, zodat een vergissing zichtbaar is en teruggedraaid kan
-- worden. De naam wordt mee bewaard: ook na het verwijderen van een speler
-- blijft leesbaar wat er gebeurd is.
-- Ingeloste coupons (2 gratis pintjes). Elke coupon hoort bij één uitgenodigd
-- team (vriend_team_id) en is van de speler wiens link dat team gebruikte.
-- Of een coupon bestaat en geldig is, wordt telkens berekend uit de
-- inschrijvingen; hier staat enkel welke al aan de bar ingelost werden.
CREATE TABLE IF NOT EXISTS coupon_inlossingen (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id  INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    vriend_team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    speler_id      INTEGER REFERENCES players(id) ON DELETE SET NULL,
    ingelost_op    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    ingelost_door  INTEGER REFERENCES players(id) ON DELETE SET NULL,
    UNIQUE (tournament_id, vriend_team_id)
);

CREATE TABLE IF NOT EXISTS claim_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id  INTEGER,
    naam       TEXT NOT NULL,
    soort      TEXT NOT NULL DEFAULT 'claim'
               CHECK (soort IN ('claim', 'vrijgegeven')),
    ip         TEXT,
    moment     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_games_toernooi ON games(tournament_id);
CREATE INDEX IF NOT EXISTS idx_games_speeldag ON games(matchday_id);
CREATE INDEX IF NOT EXISTS idx_rh_game ON rating_history(game_id, scope);
"""

STANDAARD_INSTELLINGEN = {
    "k_speler": "32",
    "k_team": "32",
    # Staat het leaguegedeelte (klassement, wedstrijden, seizoenen) open voor de
    # spelers? Uit betekent: enkel het toernooigedeelte. Organisatoren zien de
    # leaguepagina's dan nog wel. Om te zetten via Organisatie → Instellingen.
    "league_actief": "0",
    # Mogen spelers een account zonder wachtwoord opeisen? Zet dit enkel open
    # terwijl je erbij bent (bv. tijdens het toernooi zelf): wie het venster
    # openzet, vertrouwt erop dat de mensen in de zaal elkaars account niet
    # nemen. Elke claim komt in claim_log te staan.
    "claim_open": "0",
}

# Nieuwe games-tabel (voor de migratie van een v4-database).
GAMES_V5 = """
CREATE TABLE games_nieuw (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team1_id         INTEGER REFERENCES teams(id),
    team2_id         INTEGER REFERENCES teams(id),
    matchday_id      INTEGER REFERENCES matchdays(id) ON DELETE SET NULL,
    tournament_id    INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
    fase             TEXT NOT NULL DEFAULT 'liga'
                     CHECK (fase IN ('liga', 'bracket', 'shootout', 'knockout')),
    ronde            INTEGER,
    positie          INTEGER,
    volgende_game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
    volgende_slot    INTEGER,
    location_id      INTEGER REFERENCES tournament_locations(id) ON DELETE SET NULL,
    scheduled_at     TEXT NOT NULL,
    played_at        TEXT,
    status           TEXT NOT NULL DEFAULT 'gepland'
                     CHECK (status IN ('gepland', 'gespeeld')),
    winner_team_id   INTEGER REFERENCES teams(id),
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def verbind(pad: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(pad)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: lezers en schrijvers blokkeren elkaar niet meer. Zonder dit staat
    # iedereen die het klassement bekijkt te wachten zodra er een uitslag
    # binnenkomt — merkbaar wanneer een hele zaal tegelijk ververst.
    conn.execute("PRAGMA journal_mode = WAL")
    # Bij zoveel volk kan een schrijfbeurt even moeten wachten op een andere;
    # 15 seconden geduld is ruim genoeg en voorkomt "database is locked".
    conn.execute("PRAGMA busy_timeout = 15000")
    # NORMAL i.p.v. FULL: veel minder wachten op de schijf. Bij een stroompanne
    # kan je in het slechtste geval de allerlaatste uitslag kwijt zijn — die
    # typ je dan gewoon opnieuw in.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _kolommen(conn, tabel):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({tabel})")}


def _is_oud_schema(pad: str) -> bool:
    """Waar als er een database van vóór v4 staat (teams zonder naamkolom)."""
    try:
        conn = sqlite3.connect(pad)
        kolommen = {r[1] for r in conn.execute("PRAGMA table_info(teams)")}
        conn.close()
        return bool(kolommen) and "name" not in kolommen
    except sqlite3.Error:
        return False


def _backup(pad: str) -> str:
    kopie = os.path.join(os.path.dirname(pad),
                         f"shuss_backup_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(pad, kopie)
    return kopie


def _voeg_kolom_toe(conn, tabel, kolom, definitie):
    """Voeg een kolom toe aan een bestaande tabel, ook bij een gelijktijdige start.

    Gunicorn start zijn werkprocessen los van elkaar op en elk daarvan draait
    init_db(). Ze kunnen dus alle drie tegelijk vaststellen dat de kolom nog
    ontbreekt: eentje voegt ze toe, de andere twee botsen op "duplicate column
    name" en zouden zonder deze afscherming meteen crashen — met een site die
    na een herstart niet meer opkomt tot gevolg. De fout betekent hier enkel
    dat een collega ons voor was, dus daar mag rustig overheen gestapt worden.
    """
    if kolom in _kolommen(conn, tabel):
        return
    try:
        conn.execute(f"ALTER TABLE {tabel} ADD COLUMN {kolom} {definitie}")
        conn.commit()
    except sqlite3.OperationalError as fout:
        conn.rollback()
        if "duplicate column" not in str(fout).lower():
            raise


def _migreer_league(conn):
    """Tafels per speeldag; de nieuwe tabellen maakt SCHEMA zelf al aan."""
    if _kolommen(conn, "matchdays"):
        _voeg_kolom_toe(conn, "matchdays", "tafels", "INTEGER NOT NULL DEFAULT 2")


def _migreer_terugtrekken(conn):
    """Voeg de kolom toe die bijhoudt of een team zich teruggetrokken heeft."""
    if _kolommen(conn, "tournament_teams"):
        _voeg_kolom_toe(conn, "tournament_teams", "teruggetrokken",
                        "INTEGER NOT NULL DEFAULT 0")


def _migreer_rollen(conn):
    """Voeg de rolkolom toe aan een bestaande spelerstabel.

    Bestaande spelers worden gewone spelers: wie de organisatie doet, wordt
    daarna aangeduid met `python app.py --eigenaar <naam of nummer>`. Zo kan
    niemand zomaar organisator worden door een oude database te gebruiken.
    """
    if _kolommen(conn, "players"):
        _voeg_kolom_toe(conn, "players", "role", "TEXT NOT NULL DEFAULT 'speler'")


def _migreer_speelrondes(conn):
    """Eenmalig: speelrondes hernummeren naar "de zoveelste wedstrijd van het team".

    Vroeger was een speelronde een tijdslot. Met minder tafels dan wedstrijden
    speelde een team zo zijn eerste wedstrijd soms in "speelronde 2". Nu krijgt
    elke wedstrijd het volgnummer dat ze voor de teams heeft (in de volgorde van
    de kalender). Bij twee teams met een verschillend aantal gespeelde
    wedstrijden telt het hoogste. Gebeurt één keer; daarna onthoudt de
    instelling 'speelrondes_per_team' dat het gedaan is.
    """
    if not _kolommen(conn, "games") or not _kolommen(conn, "settings"):
        return
    if conn.execute("SELECT 1 FROM settings WHERE key = 'speelrondes_per_team'").fetchone():
        return
    for (tid,) in conn.execute("SELECT DISTINCT tournament_id FROM games WHERE "
                               "tournament_id IS NOT NULL AND fase = 'bracket'").fetchall():
        teller = {}
        for gid, a, b in conn.execute(
                "SELECT id, team1_id, team2_id FROM games WHERE tournament_id = ? AND "
                "fase = 'bracket' ORDER BY scheduled_at, id", (tid,)).fetchall():
            nummer = max(teller.get(a, 0), teller.get(b, 0)) + 1
            teller[a] = teller[b] = nummer
            conn.execute("UPDATE games SET ronde = ? WHERE id = ?", (nummer, gid))
    conn.execute("INSERT INTO settings (key, value) VALUES ('speelrondes_per_team', '1')")
    conn.commit()


def _migreer_inschrijvingen(conn):
    """Kolommen voor de inschrijfpagina van toernooien."""
    if _kolommen(conn, "tournaments"):
        _voeg_kolom_toe(conn, "tournaments", "max_teams", "INTEGER")
        # Hoeveel teams mag één speler uitnodigen (en zo coupons verdienen)? 0 = geen limiet.
        _voeg_kolom_toe(conn, "tournaments", "uitnodigingen_per_speler", "INTEGER NOT NULL DEFAULT 1")
        # Eerst stond de standaard op 2; toernooien in opbouw gaan mee naar 1.
        al_gedaan = (not _kolommen(conn, "settings") or conn.execute(
            "SELECT 1 FROM settings WHERE key = 'uitnodigingen_standaard_1'").fetchone())
        if not al_gedaan:
            conn.execute("UPDATE tournaments SET uitnodigingen_per_speler = 1 "
                         "WHERE uitnodigingen_per_speler = 2 AND status = 'opzet'")
            conn.execute("INSERT INTO settings (key, value) VALUES ('uitnodigingen_standaard_1', '1')")
            conn.commit()
    if _kolommen(conn, "tournament_teams"):
        _voeg_kolom_toe(conn, "tournament_teams", "ingeschreven_door",
                        "INTEGER REFERENCES players(id) ON DELETE SET NULL")
        # Een vaste standaardwaarde: SQLite laat bij ADD COLUMN geen functie toe.
        _voeg_kolom_toe(conn, "tournament_teams", "ingeschreven_op",
                        "TEXT NOT NULL DEFAULT ''")
        _voeg_kolom_toe(conn, "tournament_teams", "te_bevestigen",
                        "INTEGER REFERENCES players(id) ON DELETE SET NULL")
        _voeg_kolom_toe(conn, "tournament_teams", "uitgenodigd_door",
                        "INTEGER REFERENCES players(id) ON DELETE SET NULL")
    if _kolommen(conn, "players"):
        _voeg_kolom_toe(conn, "players", "aangemaakt_door",
                        "INTEGER REFERENCES players(id) ON DELETE SET NULL")
        # De kroonkurk die de profielfoto vervangt. Iedereen start leeg = basisdop.
        _voeg_kolom_toe(conn, "players", "dop", "TEXT")
        _voeg_kolom_toe(conn, "players", "dop_ontwerpen", "TEXT")


def _migreer_coupons_per_team(conn):
    """Eerdere versies van de coupons hingen aan een speler of aan een team; nu
    hoort elke coupon bij het uitgenodigde team. Een al ingeloste coupon gaat
    naar het eerste team dat via die speler of dat team binnenkwam."""
    kolommen = _kolommen(conn, "coupon_inlossingen")
    if not kolommen or "vriend_team_id" in kolommen:
        return
    conn.execute("ALTER TABLE coupon_inlossingen RENAME TO coupon_inlossingen_oud")
    conn.executescript(SCHEMA)
    if "team_id" in kolommen:
        eigenaars = "o.team_id IN (SELECT tt2.team_id FROM tournament_teams tt2 JOIN teams t2 ON t2.id = tt2.team_id " \
                    "WHERE tt2.tournament_id = o.tournament_id AND v.uitgenodigd_door IN (t2.player1_id, t2.player2_id))"
    else:
        eigenaars = "v.uitgenodigd_door = o.player_id"
    for o in conn.execute("SELECT * FROM coupon_inlossingen_oud").fetchall():
        vriend = conn.execute(f"""
            SELECT v.team_id, v.uitgenodigd_door FROM tournament_teams v,
                   (SELECT ? AS tournament_id, ? AS team_id, ? AS player_id) o
            WHERE v.tournament_id = o.tournament_id AND {eigenaars}
            ORDER BY v.id LIMIT 1""",
            (o["tournament_id"], o["team_id"] if "team_id" in kolommen else None,
             o["player_id"] if "player_id" in kolommen else None)).fetchone()
        if vriend:
            conn.execute("INSERT OR IGNORE INTO coupon_inlossingen (tournament_id, vriend_team_id, "
                         "speler_id, ingelost_op, ingelost_door) VALUES (?, ?, ?, ?, ?)",
                         (o["tournament_id"], vriend["team_id"], vriend["uitgenodigd_door"],
                          o["ingelost_op"], o["ingelost_door"]))
    conn.execute("DROP TABLE coupon_inlossingen_oud")
    conn.commit()


def _migreer_onvolledige_teams(conn, pad):
    """Teams met één speler mogelijk maken (status 'onvolledig').

    Vroeger stond de genodigde meteen als tweede speler in het team, met status
    'in_afwachting', en een weigering wiste het hele team. Nu is de tweede
    speler leeg tot de uitnodiging aanvaard wordt, en staat die uitnodiging in
    een eigen tabel. Een openstaande uitnodiging van vroeger wordt zo'n team
    met één speler en dezelfde uitnodiging.

    SQLite kan "NOT NULL" niet van een kolom halen: de tabel wordt herbouwd,
    met dezelfde id's. Tijdens het herbouwen staan de verwijzingen tussen de
    tabellen uit, anders zou het weggooien van de oude tabel via ON DELETE
    CASCADE alle wedstrijden en inschrijvingen van die teams meenemen.
    """
    rij = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                       "AND name = 'teams'").fetchone()
    if rij is None or "in_afwachting" not in rij[0]:
        return                              # nieuwe database of al bijgewerkt
    if os.path.exists(pad):
        print(f"Teams bijwerken (onvolledige teams) — back-up: {os.path.basename(_backup(pad))}")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute("""
            CREATE TABLE teams_nieuw (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                player1_id  INTEGER NOT NULL REFERENCES players(id),
                player2_id  INTEGER REFERENCES players(id),
                status      TEXT NOT NULL DEFAULT 'onvolledig'
                            CHECK (status IN ('onvolledig', 'actief')),
                avatar      TEXT,
                elo         REAL NOT NULL DEFAULT 1000,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )""")
        conn.execute("""
            INSERT INTO teams_nieuw (id, name, description, player1_id, player2_id,
                                     status, avatar, elo, created_at)
            SELECT id, name, description, player1_id,
                   CASE WHEN status = 'in_afwachting' THEN NULL ELSE player2_id END,
                   CASE WHEN status = 'in_afwachting' THEN 'onvolledig' ELSE status END,
                   avatar, elo, created_at
            FROM teams""")
        conn.execute("""
            INSERT OR IGNORE INTO team_invitations (team_id, player_id, created_at)
            SELECT id, player2_id, created_at FROM teams WHERE status = 'in_afwachting'
        """)
        conn.execute("DROP TABLE teams")
        conn.execute("ALTER TABLE teams_nieuw RENAME TO teams")
        fouten = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fouten:
            raise RuntimeError(f"verwijzingen kloppen niet meer: {fouten[:5]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migreer(conn, pad):
    """Werk een bestaande v4-database bij naar v5 (met back-up vooraf)."""
    games_kolommen = _kolommen(conn, "games")
    rh_kolommen = _kolommen(conn, "rating_history")
    nodig = ("tournament_id" not in games_kolommen and games_kolommen) or \
            ("scope" not in rh_kolommen and rh_kolommen)
    if not nodig:
        return

    if os.path.exists(pad):
        print(f"Database bijwerken naar v5 — back-up: {os.path.basename(_backup(pad))}")

    if rh_kolommen and "scope" not in rh_kolommen:
        conn.execute("ALTER TABLE rating_history ADD COLUMN scope TEXT NOT NULL "
                     "DEFAULT 'permanent'")
        conn.execute("ALTER TABLE rating_history ADD COLUMN season_id INTEGER")

    if games_kolommen and "tournament_id" not in games_kolommen:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(GAMES_V5)
        conn.execute("""
            INSERT INTO games_nieuw (id, team1_id, team2_id, matchday_id, fase,
                                     scheduled_at, played_at, status, winner_team_id,
                                     created_at)
            SELECT id, team1_id, team2_id, matchday_id, 'liga',
                   scheduled_at, played_at, status, winner_team_id, created_at
            FROM games
        """)
        conn.execute("DROP TABLE games")
        conn.execute("ALTER TABLE games_nieuw RENAME TO games")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


# Alle plaatsen waar een speler-id in voorkomt (buiten players zelf).
_SPELER_VERWIJZINGEN = [
    ("teams", "player1_id", None),
    ("teams", "player2_id", None),
    ("game_stats", "player_id", None),
    ("game_reports", "player_id", None),
    ("rating_history", "entity_id", "entity_type = 'speler'"),
    ("season_ratings", "entity_id", "entity_type = 'speler'"),
    ("team_invitations", "player_id", None),
    ("tournament_teams", "ingeschreven_door", None),
    ("tournament_teams", "te_bevestigen", None),
    ("tournament_teams", "uitgenodigd_door", None),
    ("coupon_inlossingen", "ingelost_door", None),
    ("coupon_inlossingen", "speler_id", None),
    ("players", "aangemaakt_door", None),
]


def vrij_spelernummer(conn) -> int:
    """Een ongebruikt willekeurig spelersnummer van 4 cijfers."""
    bezet = {r[0] for r in conn.execute("SELECT id FROM players")}
    vrij = [n for n in range(SPELER_MIN, SPELER_MAX + 1) if n not in bezet]
    if not vrij:
        raise RuntimeError("Alle spelersnummers zijn op.")
    return random.choice(vrij)


def _hernummer_spelers(conn, pad):
    """Geef bestaande spelers een willekeurig nummer van 4 cijfers.

    De oude, oplopende id's verraadden de volgorde van registratie. Alle
    verwijzingen (teams, statistieken, meldingen, ratinghistoriek) verhuizen mee.
    Er wordt in twee stappen gewerkt — eerst alles naar een hoog tijdelijk nummer —
    zodat een nieuw nummer nooit botst met een bestaand nummer.
    """
    ids = [r[0] for r in conn.execute("SELECT id FROM players ORDER BY id")]
    if not ids or min(ids) >= SPELER_MIN:
        return
    if os.path.exists(pad):
        print(f"Spelersnummers vernieuwen — back-up: {os.path.basename(_backup(pad))}")

    beschikbaar = [n for n in range(SPELER_MIN, SPELER_MAX + 1) if n not in set(ids)]
    random.shuffle(beschikbaar)
    nieuw = {oud: beschikbaar[i] for i, oud in enumerate(ids)}
    tijdelijk = 1_000_000

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(f"UPDATE players SET id = id + {tijdelijk}")
    for tabel, kolom, extra in _SPELER_VERWIJZINGEN:
        waar = f" WHERE {extra}" if extra else ""
        conn.execute(f"UPDATE {tabel} SET {kolom} = {kolom} + {tijdelijk}{waar}")

    for oud, nw in nieuw.items():
        conn.execute("UPDATE players SET id = ? WHERE id = ?", (nw, oud + tijdelijk))
        for tabel, kolom, extra in _SPELER_VERWIJZINGEN:
            waar = f" AND {extra}" if extra else ""
            conn.execute(f"UPDATE {tabel} SET {kolom} = ? WHERE {kolom} = ?{waar}",
                         (nw, oud + tijdelijk))

    conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'players'",
                 (max(nieuw.values()),))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    print(f"{len(nieuw)} speler(s) kregen een nieuw nummer.")


def init_db(pad: str = DB_PATH) -> None:
    """Maak alle tabellen en standaardinstellingen aan (idempotent).

    Een database van vóór v4 wordt opzijgezet als back-up en vervangen door een
    verse database. Een v4-database wordt ter plaatse bijgewerkt naar v5:
    de bestaande Aura wordt de permanente Aura en alle wedstrijden krijgen de
    fase 'liga'.
    """
    if os.path.exists(pad) and _is_oud_schema(pad):
        backup = os.path.join(os.path.dirname(pad),
                              f"shuss_oud_{datetime.now():%Y%m%d_%H%M%S}.db")
        os.rename(pad, backup)
        print(f"Oude database opzijgezet als {os.path.basename(backup)}; "
              "er is een nieuwe, lege database aangemaakt.")

    conn = verbind(pad)
    _migreer(conn, pad)
    _migreer_rollen(conn)
    _migreer_inschrijvingen(conn)
    conn.executescript(SCHEMA)
    _migreer_coupons_per_team(conn)
    _migreer_onvolledige_teams(conn, pad)
    _migreer_terugtrekken(conn)
    _migreer_league(conn)
    _migreer_speelrondes(conn)
    _hernummer_spelers(conn, pad)
    for sleutel, waarde in STANDAARD_INSTELLINGEN.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                     (sleutel, waarde))
    # Het oude algemene adminwachtwoord is vervangen door rollen per speler.
    conn.execute("DELETE FROM settings WHERE key = 'admin_hash'")
    # Regels in het claimlogboek van spelers die intussen gewist zijn: die
    # verwijzen naar een profiel dat niet meer bestaat. (Opruimen van vroeger;
    # bij het verwijderen van een speler gaan ze nu meteen mee.)
    conn.execute("""
        DELETE FROM claim_log
        WHERE player_id IS NOT NULL
          AND player_id NOT IN (SELECT id FROM players)
    """)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database klaar: {DB_PATH}")
