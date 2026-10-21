/* Thuispagina: het 3D-logo, de rook en de tellers.
 *
 * Alles hier is versiering. Valt dit script weg, dan blijft de pagina
 * gewoon werken: de cijfers staan al in de HTML, het logo staat stil.
 * Wie op zijn toestel "minder beweging" heeft aangezet, krijgt niets
 * van de animaties.
 */
(function () {
  "use strict";

  var rustig = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var held = document.querySelector(".thuis-held");
  if (!held) return;
  if (rustig) { held.classList.add("klaar"); return; }

  /* -------------------------------------------------------- tellers -- */
  /* Elk cijfer met data-tel telt op van 0 naar zijn waarde. */
  function tel(el, vertraging) {
    var doel = parseInt(el.getAttribute("data-tel"), 10);
    if (isNaN(doel)) return;
    var duur = 1400, start = null;
    el.textContent = "0";
    function stap(t) {
      if (start === null) start = t + vertraging;
      var p = Math.min(1, Math.max(0, (t - start) / duur));
      var e = 1 - Math.pow(1 - p, 4);            // snel vertrekken, zacht landen
      el.textContent = Math.round(doel * e);
      if (p < 1) requestAnimationFrame(stap);
    }
    requestAnimationFrame(stap);
  }
  held.querySelectorAll(".held-cijfers [data-tel]").forEach(function (el, i) { tel(el, 150 + i * 120); });
  held.querySelectorAll(".podium [data-tel]").forEach(function (el) { tel(el, 700); });

  /* Eerst alles in de beginstand zetten, en een beeld later de opbouw
     starten (podium, logo). Zonder script blijft alles gewoon zichtbaar. */
  held.classList.add("wacht");
  requestAnimationFrame(function () {
    requestAnimationFrame(function () { held.classList.add("klaar"); });
  });

  /* ------------------------------------------------------- 3D-logo -- */
  /* Het logo kantelt naar de muis. Zonder muis (gsm) wiegt het zacht heen
     en weer. De hoeken gaan via CSS-variabelen naar de stylesheet. */
  var logo = held.querySelector(".logo3d-binnen");
  var doelX = 0, doelY = 0, nuX = 0, nuY = 0, laatsteMuis = 0;
  var heeftMuis = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  if (logo && heeftMuis) {
    held.addEventListener("pointermove", function (e) {
      var r = logo.getBoundingClientRect();
      var dx = (e.clientX - (r.left + r.width / 2)) / window.innerWidth;
      var dy = (e.clientY - (r.top + r.height / 2)) / window.innerHeight;
      doelY = Math.max(-1, Math.min(1, dx * 2.2)) * 32;
      doelX = Math.max(-1, Math.min(1, -dy * 2.2)) * 24;
      laatsteMuis = performance.now();
    });
  }

  /* ---------------------------------------------------------- rook -- */
  /* Rook zoals uit de rookmachine in Tonzent. De grafische kaart rekent voor
     elk puntje uit hoe dicht de rook daar is, met "fractale ruis": golfjes
     van verschillende groottes over elkaar, die zichzelf ook nog eens
     vervormen. Zo krijg je grote wolken met fijne krullen erin, die
     voortdurend van vorm veranderen terwijl ze wegdrijven. Bij het logo
     kleurt het amberlicht de rook mee. Zonder WebGL is er gewoon geen rook. */
  var doek = held.querySelector("canvas.rook");
  var gl = null, prog = null, uRes, uTijd, uLicht, B = 0, H = 0;
  var SCHAAL = .5;                 // rook is wazig: halve resolutie is genoeg

  var HOEK_SHADER = "attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }";
  var ROOK_SHADER = [
    "precision mediump float;",
    "uniform vec2 res; uniform float t; uniform vec2 licht;",
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
    /* grote wolken met open plekken ertussen */
    "  float wolk = smoothstep(0.30, 0.62, fbm(p * 0.38 - wind * 0.55 + vec2(3.0, 0.0)));",
    "  d = smoothstep(0.34, 0.88, d) * wolk;",
    /* rook hangt laag: onderaan het dichtst, bovenaan bijna niets */
    "  d *= smoothstep(1.05, 0.2, uv.y) * 0.85 + 0.15 * smoothstep(0.9, 0.5, uv.y);",
    "  float l = 1.0 - smoothstep(0.0, 0.55, distance(uv * vec2(res.x / res.y, 1.0), licht * vec2(res.x / res.y, 1.0)));",
    "  vec3 kleur = mix(vec3(0.72, 0.78, 0.9), vec3(1.0, 0.72, 0.38), l * 0.75);",
    "  float a = d * (0.40 + l * 0.14);",
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
  if (gl) {
    var vs = maakShader(gl.VERTEX_SHADER, HOEK_SHADER), fs = maakShader(gl.FRAGMENT_SHADER, ROOK_SHADER);
    if (vs && fs) {
      prog = gl.createProgram();
      gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) prog = null;
    }
    if (prog) {
      gl.useProgram(prog);
      var buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      var loc = gl.getAttribLocation(prog, "p");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      uRes = gl.getUniformLocation(prog, "res");
      uTijd = gl.getUniformLocation(prog, "t");
      uLicht = gl.getUniformLocation(prog, "licht");
    } else { gl = null; }
  }

  function maat() {
    if (!gl) return;
    B = held.clientWidth; H = held.clientHeight;
    doek.width = Math.max(1, Math.round(B * SCHAAL));
    doek.height = Math.max(1, Math.round(H * SCHAAL));
    doek.style.width = B + "px"; doek.style.height = H + "px";
    gl.viewport(0, 0, doek.width, doek.height);
    gl.uniform2f(uRes, doek.width, doek.height);
    /* het licht staat waar het logo staat (in 0..1, y omhoog) */
    var hr = held.getBoundingClientRect(), lr = (logo || held).getBoundingClientRect();
    gl.uniform2f(uLicht, (lr.left + lr.width / 2 - hr.left) / hr.width,
                 1 - (lr.top + lr.height / 2 - hr.top) / hr.height);
  }
  var t0 = performance.now() - 20000 * Math.random();   // elke keer een andere wolk
  function tekenRook(t) {
    gl.uniform1f(uTijd, (t - t0) / 1000);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  /* ----------------------------------------------------- hoofdlus -- */
  var zichtbaar = true, vorige = 0, loopt = false;

  function frame(t) {
    if (!zichtbaar || document.hidden) { loopt = false; return; }
    var dt = vorige ? Math.min(.05, (t - vorige) / 1000) : 0;
    vorige = t;

    if (logo) {
      /* Geen muis gezien in de laatste 2,5 s? Dan zelf zacht wiegen. */
      if (!heeftMuis || t - laatsteMuis > 2500) {
        doelY = Math.sin(t / 2400) * 22;
        doelX = Math.sin(t / 3100) * 9;
      }
      nuX += (doelX - nuX) * Math.min(1, dt * 5);
      nuY += (doelY - nuY) * Math.min(1, dt * 5);
      logo.style.setProperty("--rx", nuX.toFixed(2) + "deg");
      logo.style.setProperty("--ry", nuY.toFixed(2) + "deg");
    }

    if (gl) tekenRook(t);
    requestAnimationFrame(frame);
  }
  function start() {
    if (loopt) return;
    loopt = true; vorige = 0;
    requestAnimationFrame(frame);
  }

  maat();
  window.addEventListener("resize", maat);
  /* Enkel animeren als de kop in beeld is en het tabblad open staat:
     scheelt batterij op de gsm aan de tafel. */
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (items) {
      zichtbaar = items[0].isIntersecting;
      if (zichtbaar) start();
    }).observe(held);
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) start(); });
  start();
})();
