# Leberschuss — instructions for Claude

Flask + SQLite website for the Leberschuss league and tournaments (jeugdhuis Tonzent).
UI, copy and most identifiers are in Dutch; keep it that way. Read `README.md` and `DEPLOY.md` for context.

## Working rules

- Changes to tournament rules, rating math, database migrations or auth need extra care: think them through before editing, and run the relevant tests afterwards.
- Before changing `database.py` or any migration, make a backup (`maak_backup` in `app.py`, or copy `shuss.db`).
- Never commit `.secret_key` or `shuss.db`.
- Relevant tests: `test_tiebreak.py`, `test_toernooi.py`, `test_league.py`, `test_terugtrekken.py`, `test_dop.py`, `test_samenvoegen.py`.
- Report test results as they are, including failures.
- Never edit `static/jsQR.js` (vendored library).
