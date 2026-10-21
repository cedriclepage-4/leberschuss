/* Rook zoals uit de rookmachine in Tonzent. Gedeeld door de thuispagina en
 * het beamerscherm.
 *
 * De grafische kaart rekent voor elk puntje uit hoe dicht de rook daar is,
 * met "fractale ruis": golfjes van verschillende groottes over elkaar, die
 * zichzelf ook nog eens vervormen. Zo krijg je grote wolken met fijne krullen
 * erin, die voortdurend van vorm veranderen terwijl ze wegdrijven. Rond het
 * "licht" (bv. het logo) kleurt de rook amber.
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
    "uniform vec2 res; uniform float t; uniform vec2 licht; uniform float sterkte;",
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
    "  gl_FragColor = vec4(kleur * a, a);",
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
    teken: function (t) {
      gl.uniform1f(uTijd, (t - t0) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }
  };
};
