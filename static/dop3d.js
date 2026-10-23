/* De zwevende 3D-kroonkurk op een profiel (en in de editor).
 *
 * Een echte kroonkurk, getekend met WebGL (zonder bibliotheek):
 *   - een plat bovenvlak met het ontwerp erop (de afbeelding van de server);
 *   - een afgeronde schouder die overgaat in de geplooide rok, met de plooien
 *     van de gekozen rand (klassiek 21, fijn 36, golf, kartel of glad);
 *   - hol vanbinnen, met wanddikte: de binnenkant is koper, met in het midden
 *     de ronde voering met haar ingeperste ringen.
 * De rok heeft de randkleur en de afwerking (mat, glans, metaal, glitter).
 * Bij een team krijgt elke helft de kleur, rand en afwerking van zijn speler.
 *
 * Hij zweeft en wiegt vanzelf. Sleep hem in eender welke richting rond: hij
 * tuimelt mee en draait na het loslaten nog even verder, alsof je hem opgooit.
 * Een tik werpt hem op rond een willekeurige as. Zonder WebGL valt hij terug op de oude versie
 * (gestapelde lagen in CSS-3D).
 *
 * Gebruik: <div class="dop3d" data-dop="/dop/s/12.svg?v=…" data-rand="#c0c6cc"
 *               data-randvorm="klassiek" data-afwerking="metaal"></div>
 *          Bij een team staan er twee waarden met een komma: "links,rechts".
 *          var d = Dop3D(element);
 *          d.zet(adres, {rand: [...], randvorm: [...], afwerking: [...]});   (editor)
 */
(function () {
  "use strict";

  var rustig = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ================================================================ vorm == */
  var N = 360;                 // stappen rond de dop
  var M = 30;                  // stappen langs het profiel (schouder → rok → lip)
  var DIK = 0.024;             // wanddikte
  var TOP_R = 0.80;            // straal van het platte bovenvlak
  var BEELD_R = 0.385;         // waar dat bovenvlak in de afbeelding valt (38,5 van 100)

  var PLOOIEN = {
    klassiek: { n: 21, amp: 0.060, soort: "golf" },
    fijn:     { n: 36, amp: 0.036, soort: "golf" },
    golf:     { n: 10, amp: 0.075, soort: "golf" },
    kartel:   { n: 24, amp: 0.062, soort: "zaag" },
    glad:     { n: 0,  amp: 0,     soort: "golf" }
  };

  function glad(a, b, x) { var t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); }

  /* Het profiel van de dop als (straal, diepte) voor s van 0 (rand van het
     bovenvlak) tot 1 (onderkant van de rok). De diepte loopt naar achter (-z). */
  function profiel(s) {
    var t;
    if (s < 0.3) {                                   // de afgeronde schouder
      t = s / 0.3;
      var a = (Math.PI / 2) * (1 - t);
      return [TOP_R + 0.10 * Math.cos(a), -0.10 + 0.10 * Math.sin(a)];
    }
    if (s < 0.88) {                                  // de rok, lichtjes uitlopend
      t = (s - 0.3) / 0.58;
      return [0.90 + 0.10 * t + 0.02 * t * t, -0.10 - 0.25 * t];
    }
    t = (s - 0.88) / 0.12;                           // de lip onderaan
    return [1.02 + 0.026 * t, -0.35 - 0.024 * t];
  }

  /* Hoeveel de rok op hoek h uitpuilt (-0,5 … 0,5) voor een randstijl. */
  function plooi(stijl, h) {
    var p = PLOOIEN[stijl] || PLOOIEN.klassiek;
    if (!p.n) return 0;
    if (p.soort === "zaag") {
      var f = (p.n * h / (2 * Math.PI)) % 1;
      return (1 - Math.abs(f * 2 - 1)) - 0.5;
    }
    return 0.5 - 0.5 * Math.cos(p.n * h);
  }

  /* Bouwt alle driehoeken. randstijlen = [links, rechts]. */
  function bouw(randstijlen) {
    var buiten = [], binnen = [];
    var i, j;
    function punt(i, j) {
      var h = i / N * 2 * Math.PI, s = j / (M - 1);
      var pr = profiel(s), x = Math.cos(h), y = Math.sin(h);
      var stijl = x < 0 ? randstijlen[0] : randstijlen[1];
      var p = PLOOIEN[stijl] || PLOOIEN.klassiek;
      var w = glad(0.30, 0.62, s);
      var g = plooi(stijl, h) - (p.n ? 0.5 : 0);
      var r = pr[0] + p.amp * g * w;
      var z = pr[1] + 0.018 * g * glad(0.75, 1, s);  // de onderrand golft mee
      return [r * x, r * y, z];
    }
    var P = [];
    for (i = 0; i < N; i++) { P.push([]); for (j = 0; j < M; j++) P[i].push(punt(i, j)); }
    // normalen uit de buren (rond loopt over)
    var Nm = [];
    for (i = 0; i < N; i++) {
      Nm.push([]);
      for (j = 0; j < M; j++) {
        var a = P[(i + 1) % N][j], b = P[(i + N - 1) % N][j];
        var c = P[i][Math.min(M - 1, j + 1)], d = P[i][Math.max(0, j - 1)];
        var dh = [a[0] - b[0], a[1] - b[1], a[2] - b[2]], ds = [c[0] - d[0], c[1] - d[1], c[2] - d[2]];
        var n = [dh[1] * ds[2] - dh[2] * ds[1], dh[2] * ds[0] - dh[0] * ds[2], dh[0] * ds[1] - dh[1] * ds[0]];
        var q = P[i][j];
        if (n[0] * q[0] + n[1] * q[1] + n[2] * 1.0 < 0) n = [-n[0], -n[1], -n[2]];
        var l = Math.hypot(n[0], n[1], n[2]) || 1;
        Nm[i].push([n[0] / l, n[1] / l, n[2] / l]);
      }
    }
    function rooster(pos, nor, uit, keer) {
      for (i = 0; i < N; i++) {
        var i2 = (i + 1) % N;
        for (j = 0; j < M - 1; j++) {
          var hoeken = [[i, j], [i2, j], [i2, j + 1], [i, j], [i2, j + 1], [i, j + 1]];
          hoeken.forEach(function (k) {
            var p = pos(k[0], k[1]), m = nor(k[0], k[1]);
            uit.push(p[0], p[1], p[2], keer * m[0], keer * m[1], keer * m[2], 0, 0);
          });
        }
      }
    }
    // buitenkant (schouder + rok)
    rooster(function (a, b) { return P[a][b]; }, function (a, b) { return Nm[a][b]; }, buiten, 1);
    // binnenkant: dezelfde vorm, een wanddikte naar binnen
    function binnenPunt(a, b) {
      var p = P[a][b], m = Nm[a][b];
      return [p[0] - m[0] * DIK, p[1] - m[1] * DIK, p[2] - m[2] * DIK];
    }
    rooster(binnenPunt, function (a, b) { return Nm[a][b]; }, binnen, -1);
    // de lip: buiten- en binnenrand onderaan verbinden
    var lip = [];
    for (i = 0; i < N; i++) {
      var i3 = (i + 1) % N;
      var o1 = P[i][M - 1], o2 = P[i3][M - 1], b1 = binnenPunt(i, M - 1), b2 = binnenPunt(i3, M - 1);
      [o1, o2, b2, o1, b2, b1].forEach(function (p) { lip.push(p[0], p[1], p[2], 0, 0, -1, 0, 0); });
    }
    // het platte bovenvlak (met het ontwerp) en de binnenkant ervan
    var boven = [], onder = [], voering = [];
    for (i = 0; i < N; i++) {
      var h1 = i / N * 2 * Math.PI, h2 = (i + 1) / N * 2 * Math.PI;
      var r0 = P[i][0], r1 = P[(i + 1) % N][0];
      [[0, 0], [r0[0], r0[1]], [r1[0], r1[1]]].forEach(function (p) {
        boven.push(p[0], p[1], 0, 0, 0, 1, 0.5 + p[0] / TOP_R * BEELD_R, 0.5 - p[1] / TOP_R * BEELD_R);
      });
      [[0, 0], [r1[0], r1[1]], [r0[0], r0[1]]].forEach(function (p) {
        onder.push(p[0] * 0.99, p[1] * 0.99, -DIK, 0, 0, -1, 0, 0);
      });
      var vr = 0.70;
      [[0, 0], [Math.cos(h2) * vr, Math.sin(h2) * vr], [Math.cos(h1) * vr, Math.sin(h1) * vr]].forEach(function (p) {
        voering.push(p[0], p[1], -DIK - 0.012, 0, 0, -1, 0, 0);
      });
    }
    return { boven: boven, buiten: buiten.concat(lip), binnen: binnen.concat(onder), voering: voering };
  }

  /* ============================================================ shaders == */
  var VS = [
    "attribute vec3 aPos; attribute vec3 aNor; attribute vec2 aUv;",
    "uniform mat4 uModel; uniform mat4 uProj; uniform mat3 uNorMat;",
    "varying vec3 vNor; varying vec3 vPos; varying vec3 vLok; varying vec2 vUv;",
    "void main() {",
    "  vec4 w = uModel * vec4(aPos, 1.0);",
    "  vPos = w.xyz; vNor = uNorMat * aNor; vLok = aPos; vUv = aUv;",
    "  gl_Position = uProj * w;",
    "}"
  ].join("\n");

  var FS = [
    "precision mediump float;",
    "uniform int uModus;              // 0 bovenvlak, 1 rok (verf), 2 koper binnen, 3 voering",
    "uniform sampler2D uBeeld;",
    "uniform vec3 uKleurL; uniform vec3 uKleurR;",
    "uniform vec3 uAfwL; uniform vec3 uAfwR;   // metaal, glans, glitter",
    "varying vec3 vNor; varying vec3 vPos; varying vec3 vLok; varying vec2 vUv;",
    "float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }",
    "vec3 kleur(vec3 basis, float metaal, float glans, vec3 N, vec3 V) {",
    "  vec3 L1 = normalize(vec3(-0.55, 0.75, 0.85));",
    "  vec3 L2 = normalize(vec3(0.75, -0.35, 0.55));",
    "  vec3 L3 = normalize(vec3(0.3, 0.6, -0.9));",
    "  float dif = max(dot(N, L1), 0.0) * 0.85 + max(dot(N, L2), 0.0) * 0.28 + max(dot(N, L3), 0.0) * 0.22;",
    "  float omg = 0.32 + 0.12 * N.y;",
    "  vec3 H = normalize(L1 + V);",
    "  float glim = pow(max(dot(N, H), 0.0), mix(14.0, 110.0, glans)) * mix(0.12, 1.0, glans);",
    "  vec3 H2 = normalize(L2 + V);",
    "  glim += pow(max(dot(N, H2), 0.0), mix(10.0, 60.0, glans)) * 0.25 * glans;",
    "  vec3 R = reflect(-V, N);",
    "  vec3 omgeving = mix(vec3(0.10, 0.11, 0.15), vec3(1.0), smoothstep(-0.25, 0.75, R.y));",
    "  omgeving += vec3(1.0) * 0.55 * pow(max(0.0, 1.0 - abs(R.x * 2.2 + 0.5)), 8.0);",
    "  vec3 diel = basis * (omg + dif) + vec3(glim);",
    "  vec3 met = basis * (0.18 + 0.30 * dif) + basis * omgeving * 0.85 + glim * mix(basis, vec3(1.0), 0.45);",
    "  return mix(diel, met, metaal);",
    "}",
    "void main() {",
    "  vec3 N = normalize(vNor);",
    "  if (!gl_FrontFacing) N = -N;",
    "  vec3 V = normalize(-vPos);",
    "  bool links = vLok.x < 0.0;",
    "  vec3 afw = links ? uAfwL : uAfwR;",
    "  vec3 c;",
    "  if (uModus == 0) {",
    "    vec3 tex = texture2D(uBeeld, vUv).rgb;",
    "    // Het ontwerp heeft zijn eigen glans al; enkel licht en een klein, scherp lichtpuntje.",
    "    vec3 L1 = normalize(vec3(-0.55, 0.75, 0.85));",
    "    float d0 = max(dot(N, L1), 0.0);",
    "    float sp = pow(max(dot(N, normalize(L1 + V)), 0.0), 260.0) * 0.30 * afw.y;",
    "    c = tex * (0.52 + 0.52 * d0) + vec3(sp);",
    "  } else if (uModus == 1) {",
    "    vec3 basis = links ? uKleurL : uKleurR;",
    "    c = kleur(basis, afw.x, afw.y, N, V);",
    "    if (afw.z > 0.5) {",
    "      float k = hash(floor(vLok.xy * 70.0 + vLok.z * 50.0));",
    "      c += vec3(step(0.93, k)) * 0.8;",
    "    }",
    "  } else if (uModus == 2) {",
    "    c = kleur(vec3(0.66, 0.38, 0.18), 0.9, 0.75, N, V) * 0.92;",
    "  } else {",
    "    float r = length(vLok.xy);",
    "    float ringen = 0.5 + 0.5 * sin(r * 62.0);",
    "    float groef = smoothstep(0.62, 0.66, r) * (1.0 - smoothstep(0.66, 0.70, r));",
    "    float midden = smoothstep(0.17, 0.14, r) * (1.0 - smoothstep(0.14, 0.11, r));",
    "    vec3 basis = vec3(0.58, 0.38, 0.22) * (0.82 + 0.22 * ringen) * (1.0 - 0.45 * groef - 0.3 * midden);",
    "    c = kleur(basis, 0.55, 0.45, N, V) * 0.85;",
    "  }",
    "  gl_FragColor = vec4(clamp(c, 0.0, 1.0), 1.0);",
    "}"
  ].join("\n");

  /* ========================================================== matrices == */
  function perspectief(fov, asp, n, f) {
    var t = 1 / Math.tan(fov / 2), nf = 1 / (n - f);
    return new Float32Array([t / asp, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) * nf, -1, 0, 0, 2 * f * n * nf, 0]);
  }
  function maal(a, b) {
    var o = new Float32Array(16);
    for (var c = 0; c < 4; c++) for (var r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  }
  function draaiX(h) { var c = Math.cos(h), s = Math.sin(h); return new Float32Array([1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]); }
  function draaiY(h) { var c = Math.cos(h), s = Math.sin(h); return new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]); }
  function schuif(x, y, z) { return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]); }
  function normaalMat(m) { return new Float32Array([m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]]); }

  function hexNaarRgb(h) {
    h = (h || "#c0c6cc").replace("#", "");
    return [parseInt(h.substr(0, 2), 16) / 255, parseInt(h.substr(2, 2), 16) / 255, parseInt(h.substr(4, 2), 16) / 255];
  }
  var AFWERKING = { mat: [0.0, 0.12, 0], glans: [0.0, 0.9, 0], metaal: [0.88, 0.8, 0], glitter: [0.35, 0.75, 1] };
  function lijst(v) { return (v || "").split(",").filter(Boolean); }
  function paar(l, standaard) { return [l[0] || standaard, l[1] || l[0] || standaard]; }

  /* =========================================================== WebGL-dop == */
  function maakGL(houder, doek, gl) {
    function shader(soort, bron) {
      var s = gl.createShader(soort);
      gl.shaderSource(s, bron); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    }
    var prog = gl.createProgram();
    gl.attachShader(prog, shader(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    var loc = {};
    ["aPos", "aNor", "aUv"].forEach(function (n) { loc[n] = gl.getAttribLocation(prog, n); });
    ["uModel", "uProj", "uNorMat", "uModus", "uBeeld", "uKleurL", "uKleurR", "uAfwL", "uAfwR"].forEach(function (n) {
      loc[n] = gl.getUniformLocation(prog, n);
    });
    gl.enable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.clearColor(0, 0, 0, 0);

    var delen = {}, huidigeVorm = "";
    function laadVorm(stijlen) {
      var sleutel = stijlen.join(",");
      if (sleutel === huidigeVorm) return;
      huidigeVorm = sleutel;
      var g = bouw(stijlen);
      Object.keys(g).forEach(function (k) {
        if (delen[k]) gl.deleteBuffer(delen[k].buf);
        var buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g[k]), gl.STATIC_DRAW);
        delen[k] = { buf: buf, n: g[k].length / 8 };
      });
    }

    var beeld = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, beeld);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([190, 195, 200, 255]));
    var tekenDoek = document.createElement("canvas");
    tekenDoek.width = tekenDoek.height = 512;
    var laadTeller = 0;
    function laadBeeld(adres) {
      var nr = ++laadTeller, img = new Image();
      img.onload = function () {
        if (nr !== laadTeller) return;              // intussen al een nieuwer ontwerp
        var c = tekenDoek.getContext("2d");
        c.clearRect(0, 0, 512, 512);
        c.drawImage(img, 0, 0, 512, 512);
        gl.bindTexture(gl.TEXTURE_2D, beeld);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, tekenDoek);
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        nodig = true;
      };
      img.src = adres;
    }

    var stijl = {};
    function zet(adres, o) {
      o = o || {};
      stijl.rand = paar(o.rand || [], "#c0c6cc").map(hexNaarRgb);
      stijl.afw = paar(o.afwerking || [], "metaal").map(function (a) { return AFWERKING[a] || AFWERKING.metaal; });
      laadVorm(paar(o.randvorm || [], "klassiek"));
      if (adres) laadBeeld(adres);
      nodig = true;
    }

    var nodig = true;
    function maat() {
      var r = doek.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      var b = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
      if (doek.width !== b || doek.height !== h) { doek.width = b; doek.height = h; }
      gl.viewport(0, 0, b, h);
    }

    function teken(draaiing, zweef) {
      maat();
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      var proj = perspectief(30 * Math.PI / 180, doek.width / doek.height, 0.1, 20);
      var model = maal(schuif(0, zweef, -5.9), draaiing);
      gl.uniformMatrix4fv(loc.uProj, false, proj);
      gl.uniformMatrix4fv(loc.uModel, false, model);
      gl.uniformMatrix3fv(loc.uNorMat, false, normaalMat(model));
      gl.uniform3fv(loc.uKleurL, stijl.rand[0]); gl.uniform3fv(loc.uKleurR, stijl.rand[1]);
      gl.uniform3fv(loc.uAfwL, stijl.afw[0]); gl.uniform3fv(loc.uAfwR, stijl.afw[1]);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, beeld); gl.uniform1i(loc.uBeeld, 0);
      [["boven", 0], ["buiten", 1], ["binnen", 2], ["voering", 3]].forEach(function (d) {
        var deel = delen[d[0]];
        if (!deel) return;
        gl.bindBuffer(gl.ARRAY_BUFFER, deel.buf);
        gl.enableVertexAttribArray(loc.aPos); gl.vertexAttribPointer(loc.aPos, 3, gl.FLOAT, false, 32, 0);
        gl.enableVertexAttribArray(loc.aNor); gl.vertexAttribPointer(loc.aNor, 3, gl.FLOAT, false, 32, 12);
        gl.enableVertexAttribArray(loc.aUv); gl.vertexAttribPointer(loc.aUv, 2, gl.FLOAT, false, 32, 24);
        gl.uniform1i(loc.uModus, d[1]);
        gl.drawArrays(gl.TRIANGLES, 0, deel.n);
      });
    }
    return { zet: zet, teken: teken };
  }

  /* ============================================= oude versie (zonder GL) == */
  function maakLagen(draai) {
    var beelden = [];
    var achter = document.createElement("div");
    achter.className = "dop3d-laag dop3d-achter";
    var achterBeeld = document.createElement("img");
    achterBeeld.alt = ""; achterBeeld.draggable = false;
    achter.appendChild(achterBeeld);
    achter.appendChild(document.createElement("span"));
    draai.appendChild(achter);
    beelden.push(achterBeeld);
    for (var i = 0; i < 9; i++) {
      var laag = document.createElement("img");
      laag.className = "dop3d-laag dop3d-zij";
      laag.alt = ""; laag.draggable = false;
      laag.style.setProperty("--i", i);
      draai.appendChild(laag);
      beelden.push(laag);
    }
    var boven = document.createElement("img");
    boven.className = "dop3d-laag dop3d-boven";
    boven.alt = ""; boven.draggable = false;
    draai.appendChild(boven);
    beelden.push(boven);
    return {
      zet: function (adres) { if (adres) beelden.forEach(function (b) { b.src = adres; }); },
      teken: function (m, zweef) {
        // CSS heeft de y-as omlaag: spiegelen in y (en z blijft naar de kijker).
        var c = [m[0], -m[1], m[2], 0, -m[4], m[5], -m[6], 0, m[8], -m[9], m[10], 0, 0, 0, 0, 1];
        draai.style.transform = "translateY(" + (-zweef * 40).toFixed(2) + "px) matrix3d(" +
          c.map(function (x) { return x.toFixed(5); }).join(",") + ")";
      }
    };
  }

  /* ============================================================= geheel == */
  function maak(houder) {
    if (houder._dop3d) return houder._dop3d;
    houder.innerHTML = "";
    var schaduw = document.createElement("div");
    schaduw.className = "dop3d-schaduw";
    houder.appendChild(schaduw);

    var weergave = null;
    var doek = document.createElement("canvas");
    doek.className = "dop3d-doek";
    houder.appendChild(doek);
    try {
      var gl = doek.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: true }) ||
               doek.getContext("experimental-webgl");
      if (gl) weergave = maakGL(houder, doek, gl);
    } catch (fout) { weergave = null; }
    if (!weergave) {
      houder.removeChild(doek);
      var draai = document.createElement("div");
      draai.className = "dop3d-draai";
      houder.appendChild(draai);
      weergave = maakLagen(draai);
    } else {
      houder.classList.add("met-gl");
    }

    function zet(adres, o) { weergave.zet(adres, o); }
    zet(houder.getAttribute("data-dop"), {
      rand: lijst(houder.getAttribute("data-rand")),
      randvorm: lijst(houder.getAttribute("data-randvorm")),
      afwerking: lijst(houder.getAttribute("data-afwerking"))
    });

    /* ------------------------------------------------- beweging --
       De stand van de dop is een quaternion: hij kan in elke richting draaien.
       Slepen geeft hem een draaisnelheid rond de as loodrecht op je veeg (veeg
       je schuin, dan tuimelt hij schuin). Laat je los, dan tuimelt hij verder
       en bolt hij uit; wordt hij traag, dan draait hij zacht terug tot de
       bovenkant weer naar je kijkt. Daarbovenop zweeft en wiegt hij vanzelf. */
    var stand = [0, 0, 0, 1];                  // quaternion [x, y, z, w]
    var omega = [0, 0, 0];                     // draaisnelheid (rad/s) in beeldruimte
    var sleept = false, vorigeX = 0, vorigeY = 0, vorigeT = 0, versleept = 0;
    var vorige = 0, zichtbaar = true, loopt = false;

    function qMaal(a, b) {
      return [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
              a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
              a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
              a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
    }
    function qNorm(q) { var l = Math.hypot(q[0], q[1], q[2], q[3]) || 1; return [q[0] / l, q[1] / l, q[2] / l, q[3] / l]; }
    function qAs(as, hoek) {
      var l = Math.hypot(as[0], as[1], as[2]);
      if (l < 1e-9 || !hoek) return [0, 0, 0, 1];
      var s = Math.sin(hoek / 2) / l;
      return [as[0] * s, as[1] * s, as[2] * s, Math.cos(hoek / 2)];
    }
    /* Draai over een kleine hoek richting de rust (identiteit), langs de kortste weg. */
    function naarRust(q, f) {
      var w = q[3] < 0 ? -1 : 1;
      return qNorm([q[0] * (1 - f), q[1] * (1 - f), q[2] * (1 - f), q[3] * (1 - f) + w * f]);
    }
    function qMatrix(q) {
      var x = q[0], y = q[1], z = q[2], w = q[3];
      return new Float32Array([
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
        0, 0, 0, 1]);
    }

    function frame(t) {
      if (!zichtbaar || document.hidden) { loopt = false; return; }
      var dt = vorige ? Math.min(0.05, (t - vorige) / 1000) : 0;
      vorige = t;
      if (!sleept && dt) {
        var snel = Math.hypot(omega[0], omega[1], omega[2]);
        stand = qNorm(qMaal(qAs(omega, snel * dt), stand));
        var rem = Math.pow(0.18, dt);              // uitbollen
        omega = [omega[0] * rem, omega[1] * rem, omega[2] * rem];
        // traag geworden? dan zacht terug naar de rustpositie
        if (snel < 2.2) stand = naarRust(stand, Math.min(1, dt * 3.2 * (1 - snel / 2.2)));
      }
      var wieg = rustig ? 0 : Math.sin(t / 2600) * 24;
      var kantel = rustig ? 14 : 16 + Math.sin(t / 3300) * 7;
      var zweef = rustig ? 0 : Math.sin(t / 1700) * 0.05;
      var basis = maal(draaiX(-kantel * Math.PI / 180), draaiY(wieg * Math.PI / 180));
      weergave.teken(maal(basis, qMatrix(stand)), -zweef);
      var s = 1 - zweef * 1.2;
      schaduw.style.transform = "scale(" + s.toFixed(3) + ")";
      schaduw.style.opacity = (0.55 - zweef * 2).toFixed(3);
      requestAnimationFrame(frame);
    }
    function start() {
      if (loopt) return;
      loopt = true; vorige = 0;
      requestAnimationFrame(frame);
    }

    houder.addEventListener("pointerdown", function (e) {
      sleept = true; versleept = 0;
      vorigeX = e.clientX; vorigeY = e.clientY; vorigeT = performance.now();
      omega = [0, 0, 0];
      try { houder.setPointerCapture(e.pointerId); } catch (x) {}
      start();
    });
    houder.addEventListener("pointermove", function (e) {
      if (!sleept) return;
      var nu = performance.now(), dx = e.clientX - vorigeX, dy = e.clientY - vorigeY;
      var afstand = Math.hypot(dx, dy);
      versleept += afstand;
      if (afstand > 0) {
        // Veeg naar rechts = draaien rond de verticale as, naar onder = rond
        // de horizontale; schuin = ergens daartussen.
        var hoek = afstand / (houder.clientWidth || 200) * 3.2;
        var as = [dy / afstand, dx / afstand, 0];
        stand = qNorm(qMaal(qAs(as, hoek), stand));
        var dtt = Math.max(0.008, (nu - vorigeT) / 1000), f = hoek / dtt;
        omega = [omega[0] * 0.5 + as[0] * f * 0.5, omega[1] * 0.5 + as[1] * f * 0.5, 0];
      }
      vorigeX = e.clientX; vorigeY = e.clientY; vorigeT = nu;
    });
    function los() {
      if (!sleept) return;
      sleept = false;
      if (performance.now() - vorigeT > 90) omega = [0, 0, 0];   // stilgehouden voor het loslaten
      // Een tik zonder slepen: opgeworpen rond een willekeurige as.
      if (versleept < 6) {
        var h = Math.random() * 2 * Math.PI;
        omega = [Math.cos(h) * 16, Math.sin(h) * 16, (Math.random() - 0.5) * 4];
      }
      var snel = Math.hypot(omega[0], omega[1], omega[2]), max = 30;
      if (snel > max) omega = omega.map(function (x) { return x * max / snel; });
    }
    houder.addEventListener("pointerup", los);
    houder.addEventListener("pointercancel", los);

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (items) {
        zichtbaar = items[0].isIntersecting;
        if (zichtbaar) start();
      }).observe(houder);
    }
    document.addEventListener("visibilitychange", function () { if (!document.hidden) start(); });
    start();

    houder._dop3d = { zet: zet, _zetStand: function (q) { stand = qNorm(q); omega = [0, 0, 0]; } };
    return houder._dop3d;
  }

  window.Dop3D = maak;
  document.querySelectorAll(".dop3d[data-dop]").forEach(function (el) { maak(el); });
})();
