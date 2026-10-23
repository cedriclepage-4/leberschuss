"""Controleert dat dop.py (server) en static/dop.js (editor) exact dezelfde
kroonkurken tekenen. Draai met: python3 test_dop.py  (heeft node nodig)."""
import json
import os
import random
import shutil
import subprocess
import sys

import dop

HIER = os.path.dirname(os.path.abspath(__file__))


def ontwerpen(aantal):
    rnd = random.Random(42)
    uit = [dop.BASIS, {}, {"kleur": "onzin", "nummer": 500}, "geen json", None]
    for _ in range(aantal):
        o = {k: rnd.choice(v) for k, v in dop.TOEGELATEN.items()}
        o["nummer"] = rnd.randint(0, 99)
        uit.append(o)
    return uit


def main():
    if not shutil.which("node"):
        print("node ontbreekt: test overgeslagen")
        return 0
    lijst = ontwerpen(3000)
    paren = [(lijst[i], lijst[-1 - i]) for i in range(300)]
    script = """
const Dop = require(process.argv[1]);
const inv = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const uit = { enkel: inv.enkel.map(o => Dop.svg(o)), team: inv.team.map(p => Dop.teamSvg(p[0], p[1])),
              norm: inv.enkel.map(o => Dop.normaliseer(o)) };
process.stdout.write(JSON.stringify(uit));
"""
    js = subprocess.run(["node", "-e", script, os.path.join(HIER, "static", "dop.js")],
                        input=json.dumps({"enkel": lijst, "team": paren}),
                        capture_output=True, text=True, check=True)
    r = json.loads(js.stdout)
    fout = 0
    for i, o in enumerate(lijst):
        if dop.svg(o) != r["enkel"][i]:
            fout += 1
            if fout <= 3:
                a, b = dop.svg(o), r["enkel"][i]
                k = next(j for j in range(min(len(a), len(b))) if a[j] != b[j]) if a != b else 0
                print("VERSCHIL bij", o, "\n  py:", a[k - 60:k + 60], "\n  js:", b[k - 60:k + 60])
        if dop.normaliseer(o) != r["norm"][i]:
            fout += 1
            print("NORMALISEER verschilt:", o, dop.normaliseer(o), r["norm"][i])
    for i, (a, b) in enumerate(paren):
        if dop.team_svg(a, b) != r["team"][i]:
            fout += 1
    print(f"{len(lijst)} doppen en {len(paren)} teamdoppen vergeleken: {fout} verschillen")
    return 1 if fout else 0


if __name__ == "__main__":
    sys.exit(main())
