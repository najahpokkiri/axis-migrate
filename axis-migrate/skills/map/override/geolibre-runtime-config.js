// axis-migrate override of GeoLibre's geolibre-runtime-config.js (served by skills/map/serve.py from the per-session
// override folder; start.py installs it only for the pinned GeoLibre version, see AXIS_GEOLIBRE_VERSION below).
// 1. Keeps the stock content: no managed services, no AI proxy.
// 2. Sets the raster engine to maplibre-gl-raster (crisp cells; the stock default cog-tiler-wasm smooths them) as
//    soon as the raster control exists, so nobody opens Add Data -> Raster Layer to switch it.
// 3. Camera: a project whose metadata["axis-migrate"].fit_bounds is [west, south, east, north] (overview, nodata,
//    inputs-overview) is fitted to those bounds once, in whatever canvas size the pane has (1280x960 or an 800 px
//    side pane alike). Cell and feature projects keep their camera (the cell sits at the canvas centre).
// 4. Readiness: the page is "ready" when the project is loaded and hashed, the engine is set (projects with a raster
//    layer), no /data/ request has been in flight for QUIET_MS, and the MapLibre map (found through GeoLibre 3.4.0's
//    React tree) is loaded, has every tile, is not moving, and has gone idle after one forced resize + repaint (the
//    layers that drew only after a pan: MapLibre watches its container, not window resize events, so the old
//    window "resize" event never reached it). Marks: <html data-axis-map="ready">, window.__AXIS_MAP__ and a status
//    line in the page text ("axis-map: ready · engine … · project sha256 … · click centre x,y" — the centre of the
//    map canvas in CSS px). When the map is moved and tiles load again the line says "drawing" until idle again.
// 5. Never arms GeoLibre's "unsaved changes" beforeunload prompt (it arms after an identify click and would block the
//    next navigate in the agent's browser): view projects are display only and never saved from the page.
// 6. Caption: a project with metadata["axis-migrate"].label {title, layers: [{id, name, scale}]} gets a caption in the
//    top-left corner of the map canvas: title (dataset · view) · each visible layer with its colour scale · "display
//    only". Layer visibility is read from GeoLibre's layer list (a layer toggled in the app updates the caption within
//    a second), else from the project. Every screenshot then names what it shows.
// Display only: nothing here changes data, values or what the identify popup reports.
window.__GEOLIBRE_DEPLOYMENT_ENV__ = {};
(function () {
  "use strict";
  var AXIS_GEOLIBRE_VERSION = "3.4.0";
  var ENGINE = "maplibre-gl-raster";
  var QUIET_MS = 600, MIN_MS = 1200, GIVE_UP_MS = 30000, IDLE_WAIT_MS = 4000;
  var st = window.__AXIS_MAP__ = {
    version: AXIS_GEOLIBRE_VERSION, state: "loading", engine: null, engineWanted: null, project: null,
    projectSha256: null, inflight: 0, requests: 0, lastActivity: Date.now(), started: Date.now(), centre: null,
    rasterLayers: null, fitBounds: null, fitted: false, map: false, idle: false, repaints: 0, problems: [],
    label: null, caption: null
  };
  function touch() { st.lastActivity = Date.now(); }
  // view projects are display only and never edited here: GeoLibre's "unsaved changes" prompt (it arms itself after
  // an identify click) would block the next navigate, so the page never registers one
  var addEv = window.addEventListener;
  window.addEventListener = function (type) {
    if (type === "beforeunload") return undefined;
    return addEv.apply(this, arguments);
  };
  try { Object.defineProperty(window, "onbeforeunload", { configurable: true, get: function () { return null; }, set: function () {} }); } catch (e) { st.problems.push("beforeunload: " + e); }
  function isData(u) { try { return new URL(String(u), location.href).pathname.indexOf("/data/") === 0; } catch (e) { return false; } }

  var origFetch = window.fetch;
  window.fetch = function (input) {
    var u = typeof input === "string" ? input : (input && input.url) || "";
    var d = isData(u);
    if (d) { st.inflight++; st.requests++; touch(); }
    var p = origFetch.apply(this, arguments);
    if (d) { var done = function () { st.inflight--; touch(); }; p.then(done, done); }
    return p;
  };
  var XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__axisData = isData(u); return XO.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    if (this.__axisData) {
      var x = this; st.inflight++; st.requests++; touch();
      x.addEventListener("loadend", function () { st.inflight--; touch(); });
    }
    return XS.apply(this, arguments);
  };

  function hex(buf) {
    return Array.prototype.map.call(new Uint8Array(buf), function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  // the project this page shows: the same bytes the app loads (?url=), hashed here so a receipt can cite them
  try {
    var pu = new URLSearchParams(location.search).get("url");
    if (pu) {
      st.project = new URL(pu, location.href).pathname;
      origFetch(pu, { cache: "no-store" }).then(function (r) { return r.arrayBuffer(); }).then(function (buf) {
        var txt = new TextDecoder().decode(buf), doc = JSON.parse(txt);
        st.rasterLayers = (doc.layers || []).filter(function (l) { return l && l.type === "cog"; }).length;
        st.engineWanted = st.rasterLayers ? ENGINE : null;
        var fb = ((doc.metadata || {})["axis-migrate"] || {}).fit_bounds;
        if (Array.isArray(fb) && fb.length === 4 && fb.every(function (v) { return typeof v === "number" && isFinite(v); })) st.fitBounds = fb;
        var lb = ((doc.metadata || {})["axis-migrate"] || {}).label;
        if (lb && typeof lb.title === "string" && Array.isArray(lb.layers)) {
          var vis = {};
          (doc.layers || []).forEach(function (l) { if (l && l.id) vis[l.id] = l.visible !== false; });
          st.label = { title: lb.title, layers: lb.layers.filter(function (l) { return l && l.id; }).map(function (l) {
            return { id: String(l.id), name: String(l.name || ""), scale: String(l.scale || ""), visible: vis[l.id] !== false };
          }) };
        }
        return crypto.subtle ? crypto.subtle.digest("SHA-256", buf) : null;
      }).then(function (d) { if (d) st.projectSha256 = hex(d); touch(); })
        .catch(function (e) { st.problems.push("project: " + e); st.rasterLayers = 0; });
    } else { st.rasterLayers = 0; }
  } catch (e) { st.problems.push("project url: " + e); st.rasterLayers = 0; }

  function engineSelect() {
    var all = document.querySelectorAll("select");
    for (var i = 0; i < all.length; i++) {
      var o = all[i].options;
      for (var j = 0; j < o.length; j++) if (o[j].value === ENGINE) return all[i];
    }
    return null;
  }
  function setEngine() {
    var s = engineSelect();
    if (!s) return false;
    if (s.value !== ENGINE) {
      s.value = ENGINE;
      s.dispatchEvent(new Event("input", { bubbles: true }));
      s.dispatchEvent(new Event("change", { bubbles: true }));
      touch();
    }
    st.engine = s.value;
    return s.value === ENGINE;
  }
  // the MapLibre map instance: GeoLibre 3.4.0 passes it as a `map` prop / ref inside the React tree of the container
  var map = null;
  function isMap(o) { return !!o && typeof o === "object" && typeof o.areTilesLoaded === "function" && typeof o.triggerRepaint === "function"; }
  function findMap() {
    if (map) return map;
    var el = document.querySelector(".maplibregl-map");
    if (!el) return null;
    var key = Object.keys(el).filter(function (k) { return k.indexOf("__reactFiber") === 0; })[0];
    for (var f = key ? el[key] : null, n = 0; f && n < 40; f = f.return, n++) {
      var cand = [f.memoizedProps, f.stateNode];
      for (var h = f.memoizedState, m = 0; h && m < 60; h = h.next, m++) {
        var ms = h.memoizedState;
        cand.push(ms);
        if (ms && typeof ms === "object" && "current" in ms) cand.push(ms.current);
      }
      for (var i = 0; i < cand.length; i++) {
        var x = cand[i];
        if (isMap(x)) { map = x; break; }
        if (x && typeof x === "object" && !Array.isArray(x)) {
          var y; try { y = x.map; } catch (e) { y = null; }
          if (isMap(y)) { map = y; break; }
        }
      }
      if (map) break;
    }
    if (map) {
      st.map = true;
      map.on("idle", function () { st.idle = true; });
      ["movestart", "dataloading", "sourcedataloading"].forEach(function (ev) { map.on(ev, function () { st.idle = false; }); });
    }
    return map;
  }
  function mapSettled(m) {
    try {
      st.mapState = { loaded: m.loaded(), tiles: m.areTilesLoaded(), moving: m.isMoving() };
      return st.mapState.loaded && st.mapState.tiles && !st.mapState.moving;
    } catch (e) { return false; }
  }
  function centre() {
    var c = document.querySelector(".maplibregl-canvas") || document.querySelector("canvas");
    if (!c) return null;
    var r = c.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), width: Math.round(r.width), height: Math.round(r.height) };
  }
  var bar = null;
  function show(text) {
    if (!document.body) return;
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "axis-map-status";
      bar.setAttribute("role", "status");
      bar.style.cssText = "position:fixed;left:50%;bottom:30px;transform:translateX(-50%);z-index:2147483647;" +
        "font:11px/1.3 ui-monospace,monospace;background:rgba(255,255,255,.92);color:#111;padding:1px 6px;" +
        "border:1px solid #999;border-radius:3px;pointer-events:none;max-width:calc(100% - 24px);" +
        "overflow-wrap:anywhere;text-align:center";
      document.body.appendChild(bar);
    }
    if (bar.textContent !== text) bar.textContent = text;
  }
  function line() {
    var c = st.centre;
    return "axis-map: " + st.state + " · engine " + (st.engineWanted ? (st.engine || "pending") : "n/a (no raster layer)") +
      " · project sha256 " + (st.projectSha256 || "pending") + (c ? " · click centre " + c.x + "," + c.y : "");
  }
  // GeoLibre keeps its layer list (with `visible`) in React state: the first hook-state array whose first item has
  // the id of one of this project's layers is that list; read at most once a second (≈1000 fibers). Not found =
  // the project's own visible flags.
  var visAt = 0, visById = null;
  function liveVisibility(ids) {
    var root = document.getElementById("root"), key = root && Object.keys(root).filter(function (k) { return k.indexOf("__reactContainer") === 0; })[0];
    if (!key) return null;
    var stack = [root[key]], n = 0;
    function pick(x) {
      if (!Array.isArray(x) || !x.length || !x[0] || ids.indexOf(x[0].id) < 0) return null;
      var out = {};
      x.forEach(function (l) { if (l && l.id) out[l.id] = l.visible !== false; });
      return out;
    }
    while (stack.length && n < 20000) {
      var f = stack.pop(); n++;
      if (!f) continue;
      for (var h = f.memoizedState, m = 0; h && typeof h === "object" && "next" in h && m < 80; h = h.next, m++) {
        var ms = h.memoizedState, got = pick(ms) || (ms && typeof ms === "object" && !Array.isArray(ms) && "current" in ms ? pick(ms.current) : null);
        if (got) return got;
      }
      if (f.sibling) stack.push(f.sibling);
      if (f.child) stack.push(f.child);
    }
    return null;
  }
  function layerVisible(l) {
    return visById && l.id in visById ? visById[l.id] : l.visible;
  }
  var cap = null;
  function caption() {
    var lb = st.label, c = st.centre;
    if (!lb || !document.body) return;
    if (Date.now() - visAt > 1000) {
      visAt = Date.now();
      try { visById = liveVisibility(lb.layers.map(function (l) { return l.id; })) || visById; } catch (e) { st.problems.push("caption: " + e); }
    }
    var groups = [];
    lb.layers.forEach(function (l) {
      if (!layerVisible(l)) return;
      var g = groups.length && groups[groups.length - 1].scale === l.scale ? groups[groups.length - 1] : null;
      if (g) g.names.push(l.name); else groups.push({ names: [l.name], scale: l.scale });
    });
    var shown = groups.length ? groups.map(function (g) { return g.names.join(" + ") + (g.scale ? ": " + g.scale : ""); }) : ["no layer visible"];
    var text = [lb.title].concat(shown).concat(["display only · verdicts come from compare.py"]);
    st.caption = text.join(" · ");
    if (!cap) {
      cap = document.createElement("div");
      cap.id = "axis-map-caption";
      cap.style.cssText = "position:fixed;z-index:2147483646;font:13px/1.35 system-ui,sans-serif;color:#111;" +
        "background:rgba(255,255,255,.9);border:1px solid #888;border-radius:3px;padding:3px 7px;pointer-events:none;" +
        "max-width:min(560px,60vw);overflow-wrap:anywhere;white-space:pre-line";
      document.body.appendChild(cap);
    }
    var box = c ? { left: c.x - c.width / 2, top: c.y - c.height / 2 } : { left: 0, top: 0 };
    cap.style.left = Math.round(box.left + 10) + "px";
    cap.style.top = Math.round(box.top + 10) + "px";
    var html = "<b></b>", t = text.slice(1).join("\n");
    if (cap.getAttribute("data-text") !== st.caption) {
      cap.setAttribute("data-text", st.caption);
      cap.innerHTML = html;
      cap.firstChild.textContent = text[0];
      cap.appendChild(document.createTextNode("\n" + t));
    }
  }
  var repaintAt = 0;
  function repaint(m) {
    st.idle = false;
    st.repaints++;
    repaintAt = Date.now();
    try { m.resize(); m.triggerRepaint(); } catch (e) { st.problems.push("repaint: " + e); }
    touch();
  }
  function tick() {
    var now = Date.now();
    var engineOk = st.rasterLayers === 0 || (st.rasterLayers > 0 && setEngine());
    st.centre = centre();
    var quiet = st.inflight <= 0 && now - st.lastActivity >= QUIET_MS && now - st.started >= MIN_MS;
    var known = st.rasterLayers !== null && st.projectSha256 !== null;
    var m = findMap();
    var settled = m ? mapSettled(m) : true;
    var pre = known && engineOk && quiet && st.centre && settled;
    if (pre && m && st.fitBounds && !st.fitted) {           // once: the whole data extent in this canvas, any size
      st.fitted = true;
      var b = st.fitBounds, pad = Math.max(8, Math.min(40, Math.round(Math.min(st.centre.width, st.centre.height) / 20)));
      try { m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: { top: pad, left: pad, right: pad, bottom: pad + 24 }, animate: false, maxZoom: 22 }); }
      catch (e) { st.problems.push("fitBounds: " + e); }
      touch();
      pre = false;
    }
    if (pre) {
      if (!st.repaints) {
        if (m) repaint(m); else { st.repaints = 1; repaintAt = now; window.dispatchEvent(new Event("resize")); touch(); }
        pre = false;
      } else if (m && !st.idle && now - repaintAt < IDLE_WAIT_MS) {
        pre = false;                                          // drawn frame not confirmed yet (MapLibre 'idle')
      }
    }
    if (pre) {
      st.state = "ready";
    } else if (st.state === "ready" || st.state === "drawing") {
      st.state = "drawing";
    } else if (document.visibilityState === "hidden") {
      st.state = "loading (tab hidden: the browser does not draw it until it is shown)";
    } else {
      st.state = now - st.started > GIVE_UP_MS && !engineOk ? "engine-not-set (this GeoLibre build has no maplibre-gl-raster engine select)" : "loading";
    }
    document.documentElement.setAttribute("data-axis-map", st.state === "ready" ? "ready" : "loading");
    caption();
    show(line());
  }
  setInterval(tick, 150);
})();
