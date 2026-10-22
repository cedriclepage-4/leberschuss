/* Rook zoals uit de rookmachine in Tonzent. Gedeeld door de thuispagina en
 * het beamerscherm.
 *
 * De grafische kaart rekent voor elk puntje uit hoe dicht de rook daar is,
 * met "fractale ruis": golfjes van verschillende groottes over elkaar, die
 * zichzelf ook nog eens vervormen. Zo krijg je grote wolken met fijne krullen
 * erin, die voortdurend van vorm veranderen terwijl ze wegdrijven. Rond het
 * "licht" (bv. het logo) kleurt de rook amber.
 *
 * Daarbovenop wat discolicht, rustig en ingetogen: drie "moving heads" aan het
 * plafond die traag heen en weer zwaaien en langzaam van kleur wisselen. Net
 * als in het echt zie je hun bundels vooral waar ze door de rook gaan. Onderaan
 * gloeit een zachte kleurwas die traag pulseert. Uitzetten: { disco: false }.
 *
 * rook.lichtbeat(x, y, kleur, kracht): één korte, dramatische lichtslag, zoals
 * een lichttechnieker die op de knop drukt. De drie bundels zwaaien naar dat
 * punt (0..1, y omhoog), de zaal licht even op in die kleur en alles zakt
 * daarna rustig weg. Geen stroboscoop: één zachte aanzet, dan uitdoven.
 * Volgen er meer slagen kort na elkaar, dan glijden de bundels van doel naar doel.
 *
 * Gebruik:
 *   var rook = Rook(canvas, { sterkte: 1 });
 *   if (rook) { rook.maat(lichtX, lichtY); ... rook.teken(tijdInMs); }
 * Zonder WebGL geeft Rook() null terug: dan is er gewoon geen rook.
 */
window.Rook = function (doek, opties) {
  "use strict";
  opties = opties || {};
  var SCHAAL = .5;                 // rook is wazig: halve resolutie is genoeg
  var gl = null, prog = null;

  var HOEK_SHADER = "attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }";
  var ROOK_SHADER = [
    "precision mediump float;",
    "uniform vec2 res; uniform float t; uniform vec2 licht; uniform float sterkte; uniform float disco;",
    "uniform float flits; uniform float focus; uniform vec2 doel; uniform vec3 flitskleur;",
    /* een verzadigde kleur uit de kleurencirkel, wat zachter gemaakt */
    "vec3 tint(float h) { vec3 k = clamp(abs(mod(h * 6.0 + vec3(0.0, 4.0, 2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0); return mix(k, vec3(1.0), 0.18); }",
    /* één moving head: een lichtbundel vanaf het plafond, die traag zwaait */
    "float bundel(vec2 q, vec2 bron, float hoek) {",
    "  vec2 richting = vec2(sin(hoek), -cos(hoek));",
    "  vec2 v = q - bron;",
    "  float langs = dot(v, richting);",
    "  float dwars = abs(v.x * richting.y - v.y * richting.x);",
    "  float breedte = 0.02 + langs * 0.09;",
    "  return smoothstep(breedte, breedte * 0.15, dwars) * smoothstep(0.0, 0.12, langs) * exp(-langs * 0.75);",
    "}",
    "float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }",
    "float ruis(vec2 p) {",
    "  vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);",
    "  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),",
    "             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);",
    "}",
    "float fbm(vec2 p) {",
    "  float v = 0.0, a = 0.5;",
    "  mat2 draai = mat2(0.8, 0.6, -0.6, 0.8);",
    "  for (int k = 0; k < 6; k++) { v += a * ruis(p); p = draai * p * 2.03 + 11.7; a *= 0.5; }",
    "  return v;",
    "}",
    "void main() {",
    "  vec2 uv = gl_FragCoord.xy / res;",                       // 0..1, y omhoog
    "  vec2 p = vec2(uv.x * res.x / res.y, uv.y) * 2.6;",
    "  vec2 wind = vec2(t * 0.16, t * 0.035);",
    /* de rook vervormt zichzelf: dat geeft de krullen */
    "  vec2 q = vec2(fbm(p - wind), fbm(p + vec2(5.2, 1.3) - wind * 0.8));",
    "  vec2 r = vec2(fbm(p + 3.2 * q + vec2(1.7, 9.2) + t * 0.09), fbm(p + 3.2 * q + vec2(8.3, 2.8) - t * 0.07));",
    "  float d = fbm(p * 1.1 + 2.6 * r - wind * 1.4);",
    /* grote wolken met dunnere plekken ertussen. Nooit helemaal leeg: anders
       drijft er soms een halve minuut lang een gat over het hele scherm. */
    "  float wolk = 0.4 + 0.6 * smoothstep(0.28, 0.62, fbm(p * 0.38 - wind * 0.55 + vec2(3.0, 0.0)));",
    "  d = smoothstep(0.34, 0.88, d) * wolk;",
    /* rook hangt laag: onderaan het dichtst, bovenaan bijna niets */
    "  d *= smoothstep(1.05, 0.2, uv.y) * 0.85 + 0.15 * smoothstep(0.9, 0.5, uv.y);",
    "  float l = 1.0 - smoothstep(0.0, 0.55, distance(uv * vec2(res.x / res.y, 1.0), licht * vec2(res.x / res.y, 1.0)));",
    "  vec3 kleur = mix(vec3(0.72, 0.78, 0.9), vec3(1.0, 0.72, 0.38), l * 0.75);",
    "  float a = d * (0.40 + l * 0.14) * sterkte;",
    "  vec3 kleur_a = kleur * a;",
    /* discolicht: drie bundels, elk met een eigen tempo en kleur */
    "  if (disco > 0.0) {",
    "    float asp = res.x / res.y;",
    "    vec2 q = vec2(uv.x * asp, uv.y);",
    "    vec3 lichtje = vec3(0.0);",
    "    for (int k = 0; k < 3; k++) {",
    "      float fk = float(k);",
    "      vec2 bron = vec2((0.18 + 0.32 * fk) * asp, 1.06);",
    "      float hoek = sin(t * (0.21 + 0.06 * fk) + fk * 2.1) * 0.55 + sin(t * 0.09 + fk) * 0.18;",
    /* bij een lichtslag zwaaien alle bundels naar het doel */
    "      vec2 naar = vec2(doel.x * asp, doel.y) - bron;",
    "      float wieg = sin(t * (0.55 + 0.12 * fk) + fk * 1.7) * 0.03;",
    "      hoek = mix(hoek, atan(naar.x, -naar.y) + wieg, focus);",
    "      float b = bundel(q, bron, hoek);",
    "      vec3 bk = mix(tint(t * 0.018 + fk * 0.33), flitskleur, focus);",
    /* in de rook goed zichtbaar, in heldere lucht amper */
    "      lichtje += bk * b * (0.2 + 0.5 * focus + 1.1 * d) * (1.0 + 2.4 * flits + 0.9 * focus);",
    "    }",
    /* de zaal licht even op, het meest rond het doel en in de rook */
    "    float bij = exp(-dot(q - vec2(doel.x * asp, doel.y), q - vec2(doel.x * asp, doel.y)) * 5.0);",
    "    kleur_a += flitskleur * flits * (0.085 + 0.27 * d + 0.2 * bij) * sterkte;",
    /* zachte kleurwas onderaan, traag pulserend */
    "    float puls = 0.55 + 0.45 * sin(t * 0.55);",
    "    vec3 was = tint(t * 0.012 + 0.6) * smoothstep(0.55, 0.0, uv.y) * puls * (0.25 + 0.75 * d);",
    "    kleur_a += (lichtje * 0.3 + was * 0.07) * disco * sterkte;",
    "  }",
    "  float alfa = clamp(max(a, max(kleur_a.r, max(kleur_a.g, kleur_a.b))), 0.0, 1.0);",
    "  gl_FragColor = vec4(kleur_a, alfa);",
    "}"
  ].join("\n");

  function maakShader(soort, bron) {
    var sh = gl.createShader(soort);
    gl.shaderSource(sh, bron); gl.compileShader(sh);
    return gl.getShaderParameter(sh, gl.COMPILE_STATUS) ? sh : null;
  }
  try {
    gl = doek && (doek.getContext("webgl", { premultipliedAlpha: true, antialias: false })
                  || doek.getContext("experimental-webgl"));
  } catch (e) { gl = null; }
  if (!gl) return null;

  var vs = maakShader(gl.VERTEX_SHADER, HOEK_SHADER), fs = maakShader(gl.FRAGMENT_SHADER, ROOK_SHADER);
  if (vs && fs) {
    prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) prog = null;
  }
  if (!prog) return null;

  gl.useProgram(prog);
  var buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  var loc = gl.getAttribLocation(prog, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  var uRes = gl.getUniformLocation(prog, "res");
  var uTijd = gl.getUniformLocation(prog, "t");
  var uLicht = gl.getUniformLocation(prog, "licht");
  gl.uniform1f(gl.getUniformLocation(prog, "sterkte"), opties.sterkte || 1);
  gl.uniform1f(gl.getUniformLocation(prog, "disco"), opties.disco === false ? 0 : 1);
  var uFlits = gl.getUniformLocation(prog, "flits"), uFocus = gl.getUniformLocation(prog, "focus");
  var uDoel = gl.getUniformLocation(prog, "doel"), uFlitskleur = gl.getUniformLocation(prog, "flitskleur");
  gl.uniform3f(uFlitskleur, 1, .72, .25);
  /* De lichtslag beweegt als een gedempte veer: vertrekt uit stilstand, versnelt
     zacht en glijdt tot stilstand, zonder doorschieten en zonder schok. Komt er
     een nieuwe slag tijdens de vorige, dan glijden de bundels gewoon verder
     naar het nieuwe doel. */
  var veer = { f: 0, v: 0, x: .5, y: .5, vx: 0, vy: 0, doelX: .5, doelY: .5, tot: 0 };
  var flitsStart = -1e9, flitsRest = 0, flitsKracht = 1, vorigeNu = null;
  var VASTHOUDEN = 2600;                 // ms dat de bundels op het doel blijven

  function zacht(x) { x = Math.max(0, Math.min(1, x)); return x * x * (3 - 2 * x); }
  function veerStap(o, k, vk, doel, w, dt) {
    var a = w * w * (doel - o[k]) - 2 * w * o[vk];
    o[vk] += a * dt; o[k] += o[vk] * dt;
  }
  /* De zaal: zwelt aan in 0,35 s en dooft traag uit. Een nieuwe slag laat
     de vorige nooit eerst wegvallen. */
  function flitsWaarde(nu) {
    var ms = nu - flitsStart;
    var golf = ms < 350 ? zacht(ms / 350) : Math.exp(-(ms - 350) / 1500);
    return Math.max(golf * flitsKracht, flitsRest * Math.exp(-ms / 1500));
  }
  gl.uniform2f(uLicht, .5, .5);

  var t0 = performance.now() - 20000 * Math.random();   // elke keer een andere wolk

  return {
    /* Canvas op de maat van zijn ouder zetten; licht in 0..1 (y omhoog). */
    maat: function (lichtX, lichtY) {
      var B = doek.parentNode.clientWidth, H = doek.parentNode.clientHeight;
      doek.width = Math.max(1, Math.round(B * SCHAAL));
      doek.height = Math.max(1, Math.round(H * SCHAAL));
      doek.style.width = B + "px"; doek.style.height = H + "px";
      gl.viewport(0, 0, doek.width, doek.height);
      gl.uniform2f(uRes, doek.width, doek.height);
      if (lichtX != null) gl.uniform2f(uLicht, lichtX, lichtY);
    },
    lichtbeat: function (x, y, kleur, kracht) {
      var nu = performance.now();
      kleur = kleur || [1, .72, .25];
      gl.uniform3f(uFlitskleur, kleur[0], kleur[1], kleur[2]);
      flitsRest = flitsWaarde(nu); flitsStart = nu; flitsKracht = kracht || 1;
      veer.doelX = x; veer.doelY = y;
      if (veer.f < .05) { veer.x = x; veer.y = y; veer.vx = veer.vy = 0; }
      veer.tot = nu + VASTHOUDEN;
    },
    /* De bundels zachtjes naar een ander punt laten meegaan (zonder nieuwe
       flits), bv. achter een team aan dat naar de volgende ronde vliegt. */
    volg: function (x, y, extraMs) {
      var nu = performance.now();
      veer.doelX = x; veer.doelY = y;
      veer.tot = Math.max(veer.tot, nu + (extraMs || 1500));
    },
    teken: function (t) {
      var nu = performance.now();
      var dt = vorigeNu == null ? 0 : Math.min(.05, Math.max(0, (nu - vorigeNu) / 1000));
      vorigeNu = nu;
      veerStap(veer, "f", "v", nu < veer.tot ? 1 : 0, 2.4, dt);      // naar het doel en terug
      veerStap(veer, "x", "vx", veer.doelX, 2.2, dt);                  // van doel naar doel
      veerStap(veer, "y", "vy", veer.doelY, 2.2, dt);
      gl.uniform1f(uFlits, flitsWaarde(nu));
      gl.uniform1f(uFocus, Math.max(0, Math.min(1, veer.f)));
      gl.uniform2f(uDoel, veer.x, veer.y);
      gl.uniform1f(uTijd, (t - t0) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }
  };
};
