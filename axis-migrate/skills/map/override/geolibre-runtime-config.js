// axis-migrate override of GeoLibre's geolibre-runtime-config.js (served by skills/map/serve.py from the per-session
// override folder; start.py installs it only for the pinned GeoLibre version, see AXIS_GEOLIBRE_VERSION below).
// 1. Keeps the stock content: no managed services, no AI proxy.
// 2. Sets the raster engine to maplibre-gl-raster (crisp cells; the stock default cog-tiler-wasm smooths them) as
//    soon as the raster control exists, so nobody opens Add Data -> Raster Layer to switch it.
// 3. Readiness: counts the page's /data/ requests; when the project is loaded, the engine is set (projects with a
//    raster layer) and no /data/ request has been in flight for QUIET_MS, it triggers one map resize (repaint) and
//    marks the page ready: <html data-axis-map="ready">, window.__AXIS_MAP__ and a status line in the page text
//    ("axis-map: ready · engine … · project sha256 … · click centre x,y" — the centre of the map canvas in CSS px).
// Display only: nothing here changes data, values or what the identify popup reports.
window.__GEOLIBRE_DEPLOYMENT_ENV__ = {};
(function () {
  "use strict";
  var AXIS_GEOLIBRE_VERSION = "3.4.0";
  var ENGINE = "maplibre-gl-raster";
  var QUIET_MS = 800, MIN_MS = 1500, GIVE_UP_MS = 30000;
  var st = window.__AXIS_MAP__ = {
    version: AXIS_GEOLIBRE_VERSION, state: "loading", engine: null, engineWanted: null, project: null,
    projectSha256: null, inflight: 0, requests: 0, lastActivity: Date.now(), started: Date.now(), centre: null,
    rasterLayers: null, problems: []
  };
  function touch() { st.lastActivity = Date.now(); }
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
        "border:1px solid #999;border-radius:3px;pointer-events:none;white-space:nowrap";
      document.body.appendChild(bar);
    }
    if (bar.textContent !== text) bar.textContent = text;
  }
  function line() {
    var c = st.centre;
    return "axis-map: " + st.state + " · engine " + (st.engineWanted ? (st.engine || "pending") : "n/a (no raster layer)") +
      " · project sha256 " + (st.projectSha256 || "pending") + (c ? " · click centre " + c.x + "," + c.y : "");
  }
  var repainted = false;
  function tick() {
    var now = Date.now();
    var engineOk = st.rasterLayers === 0 || (st.rasterLayers > 0 && setEngine());
    st.centre = centre();
    var quiet = st.inflight <= 0 && now - st.lastActivity >= QUIET_MS && now - st.started >= MIN_MS;
    var known = st.rasterLayers !== null && st.projectSha256 !== null;
    if (known && engineOk && quiet && st.centre) {
      if (!repainted) {           // layers that only drew after a pan: one resize makes MapLibre repaint
        repainted = true;
        window.dispatchEvent(new Event("resize"));
        touch();
      } else {
        st.state = "ready";
      }
    } else if (st.state === "ready" && st.inflight > 0) {
      st.state = "drawing";
    } else if (st.state !== "ready") {
      st.state = now - st.started > GIVE_UP_MS && !engineOk ? "engine-not-set (switch it by hand: Add Data -> Raster Layer)" : "loading";
    }
    document.documentElement.setAttribute("data-axis-map", st.state === "ready" ? "ready" : "loading");
    show(line());
  }
  setInterval(tick, 200);
})();
