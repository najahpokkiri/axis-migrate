/* axis-migrate report swipe map. Offline: vendored MapLibre GL 4.7.1 + maplibre-gl-compare 0.5.0, layers from
   assets/layers.js (loaded by <script>, because file:// blocks fetch). No basemap. Display only. */
(function () {
  "use strict";
  var DATA = window.AXIS_LAYERS;
  var sel = document.getElementById("layer-select");
  var diffBox = document.getElementById("diff-toggle");
  var wrap = document.getElementById("swipe-wrap");
  var fallback = document.getElementById("map-fallback");
  var caption = document.getElementById("map-caption");
  var legend = document.getElementById("legend");
  var rightLabel = document.getElementById("right-label");
  if (!DATA || !DATA.layers || !DATA.layers.length || !wrap) { return; }
  var byId = {};
  DATA.layers.forEach(function (l) { byId[l.dataset] = l; });
  var VIRIDIS = ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"];
  var PUOR = ["#7f3b08", "#b35806", "#e08214", "#fdb863", "#fee0b6", "#f7f7f7", "#d8daeb", "#b2abd2", "#8073ac", "#542788", "#2d004b"];

  function fmt(v) {
    if (v === null || v === undefined) { return "?"; }
    if (v === 0) { return "0"; }
    return Number(v).toPrecision(6).replace(/\.?0+(e|$)/, "$1");
  }
  function ramp(colours) { return "linear-gradient(to right," + colours.join(",") + ")"; }
  function setLegend(l) {
    if (l.family !== "raster") {
      legend.innerHTML = '<span><span class="ramp" style="width:24px;background:#0b5cad"></span> legacy</span>' +
        '<span><span class="ramp" style="width:24px;background:#e66101"></span> new</span>';
      return;
    }
    var s = l.scale || {}, d = l.diff_scale || {};
    legend.innerHTML = "";
    var a = document.createElement("span");
    a.textContent = "values (" + (s.units || "no units") + ", shared scale p2–p98 of both sides): " + fmt(s.min) + " ";
    var r = document.createElement("span"); r.className = "ramp"; r.style.background = ramp(VIRIDIS);
    a.appendChild(r); a.appendChild(document.createTextNode(" " + fmt(s.max)));
    var b = document.createElement("span");
    b.textContent = "difference new − legacy: " + fmt(d.min) + " ";
    var r2 = document.createElement("span"); r2.className = "ramp"; r2.style.background = ramp(PUOR);
    b.appendChild(r2); b.appendChild(document.createTextNode(" " + fmt(d.max) + " (zero transparent; blue = legacy only, red = new only)"));
    legend.appendChild(a); legend.appendChild(b);
  }
  function setCaption(l) { caption.textContent = DATA.caption.replace("{crs}", l.crs || "native CRS"); }
  function showFallback(why) {
    wrap.hidden = true;
    if (fallback) {
      fallback.hidden = false;
      var cur = sel ? sel.value : DATA["default"];
      Array.prototype.forEach.call(fallback.querySelectorAll("img"), function (img) {
        img.hidden = img.getAttribute("data-dataset") !== cur;
      });
      if (why) { fallback.setAttribute("data-reason", why); }
    }
  }
  function webglOk() {
    try {
      var c = document.createElement("canvas");
      return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
    } catch (e) { return false; }
  }
  var current = byId[(sel && sel.value) || DATA["default"]] || DATA.layers[0];
  setCaption(current); setLegend(current);
  if (!window.maplibregl || !maplibregl.Compare || !webglOk()) { showFallback("webgl-unavailable"); return; }

  var STYLE = { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#f6f6f4" } }] };
  var mapA, mapB, compare;
  try {
    mapA = new maplibregl.Map({ container: "map-legacy", style: STYLE, attributionControl: false, center: [0, 0], zoom: 1, fadeDuration: 0 });
    mapB = new maplibregl.Map({ container: "map-new", style: STYLE, attributionControl: false, center: [0, 0], zoom: 1, fadeDuration: 0 });
    compare = new maplibregl.Compare(mapA, mapB, "#swipe-wrap", {});
  } catch (e) { showFallback("map-error"); return; }
  window.AXIS_MAPS = { a: mapA, b: mapB, compare: compare };

  function clear(map) {
    ["ax-fill", "ax-line", "ax-img"].forEach(function (id) { if (map.getLayer(id)) { map.removeLayer(id); } });
    if (map.getSource("ax")) { map.removeSource("ax"); }
  }
  function addRaster(map, uri, corners) {
    if (!uri) { return; }
    map.addSource("ax", { type: "image", url: uri, coordinates: corners });
    map.addLayer({ id: "ax-img", type: "raster", source: "ax", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0, "raster-opacity": 1 } });
  }
  function addVector(map, gj, colour) {
    if (!gj) { return; }
    map.addSource("ax", { type: "geojson", data: gj });
    map.addLayer({ id: "ax-fill", type: "fill", source: "ax", paint: { "fill-color": colour, "fill-opacity": 0.35 } });
    map.addLayer({ id: "ax-line", type: "line", source: "ax", paint: { "line-color": colour, "line-width": 1.5 } });
  }
  function bounds(l) {
    if (l.family === "raster") {
      var c = l.corners;
      return [[Math.min(c[0][0], c[3][0]), Math.min(c[2][1], c[3][1])], [Math.max(c[1][0], c[2][0]), Math.max(c[0][1], c[1][1])]];
    }
    var b = l.bounds_4326 || [0, 0, 0, 0];
    return [[b[0], b[1]], [b[2], b[3]]];
  }
  function draw() {
    var l = current, diff = diffBox && diffBox.checked;
    clear(mapA); clear(mapB);
    if (l.family === "raster") {
      addRaster(mapA, l.legacy, l.corners);
      addRaster(mapB, diff ? l.diff : l["new"], l.corners);
    } else {
      addVector(mapA, l.legacy, "#0b5cad");
      addVector(mapB, l["new"], "#e66101");
    }
    if (rightLabel) { rightLabel.textContent = (diff && l.family === "raster") ? "difference (new − legacy)" : "new"; }
    var b = bounds(l);
    mapA.fitBounds(b, { padding: 20, animate: false });
    setCaption(l); setLegend(l);
  }
  var loaded = 0;
  function onLoad() { loaded += 1; if (loaded === 2) { draw(); wrap.setAttribute("data-ready", "1"); } }
  mapA.on("load", onLoad); mapB.on("load", onLoad);
  mapA.on("error", function (e) { if (console && console.warn) { console.warn("map", e && e.error && e.error.message); } });
  if (sel) { sel.addEventListener("change", function () { current = byId[sel.value]; if (loaded === 2) { draw(); } }); }
  if (diffBox) { diffBox.addEventListener("change", function () { if (loaded === 2) { draw(); } }); }
})();
