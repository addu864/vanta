(function () {
  "use strict";
  var REPO = "addu864/vanta";
  var root = document.documentElement;
  root.classList.add("js");
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  // OS- and CPU-aware download buttons
  var main = $("#dl-main"), note = $("#dl-note");
  var ua = navigator.userAgent || "", plat = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
  var mobile = /Android|iPhone|iPad|iPod/i.test(ua) || (/Mac/.test(plat) && navigator.maxTouchPoints > 1);
  var isMac = !mobile && (/Mac/i.test(plat) || /Mac OS X/.test(ua));
  var isWin = !mobile && (/Win/i.test(plat) || /Windows/.test(ua));
  function gpuName() {
    try {
      var c = document.createElement("canvas"), gl = c.getContext("webgl") || c.getContext("experimental-webgl");
      if (!gl) return "";
      var ext = gl.getExtension("WEBGL_debug_renderer_info");
      return String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER) || "");
    } catch (e) { return ""; }
  }
  function macArch() {
    // Chromium browsers tell us directly; otherwise the GPU name is a good hint ("Apple M2" vs "Intel Iris").
    var hint = navigator.userAgentData && navigator.userAgentData.getHighEntropyValues
      ? navigator.userAgentData.getHighEntropyValues(["architecture"]).then(function (v) { return v.architecture === "arm" ? "arm" : v.architecture === "x86" ? "intel" : ""; }, function () { return ""; })
      : Promise.resolve("");
    return hint.then(function (a) {
      if (a) return a;
      var g = gpuName();
      if (/Apple (M\d|GPU)/i.test(g)) return "arm";
      if (/Intel|AMD|Radeon|NVIDIA|GeForce/i.test(g)) return "intel";
      return "";
    });
  }
  function pick(id, otherId) {
    var a = $("#" + id), b = $("#" + otherId);
    if (a) { a.classList.add("primary"); a.classList.remove("ghost"); a.insertAdjacentHTML("beforeend", ' <span class="badge">Your Mac</span>'); }
    if (b) { b.classList.remove("primary"); b.classList.add("ghost"); }
  }
  if (main) {
    var v = main.dataset.ver || "", vm = main.dataset.macver || "";
    if (isWin) {
      main.href = main.dataset.win; main.textContent = "Download for Windows";
      if (note) note.innerHTML = "Windows 10/11 · " + v + ' · 12 MB (the game downloads on first launch). On a Mac? <a href="index.html#dl-mac">Get the macOS version</a>.';
      var w = $("#dl-win"); if (w) w.classList.add("detected");
    } else if (isMac) {
      main.href = main.dataset.mac; main.textContent = "Download for macOS";
      if (note) note.innerHTML = "macOS · " + vm + ' · universal zip. Or pick the <a href="index.html#dl-mac">Intel Mac / Apple Silicon</a> build.';
      var m = $("#dl-mac"); if (m) m.classList.add("detected");
      macArch().then(function (arch) {
        if (arch === "arm") {
          main.href = main.dataset.macArm; main.textContent = "Download for Apple Silicon Mac"; pick("dl-mac-arm", "dl-mac-intel");
          if (note) note.innerHTML = "We think this Mac has an Apple chip (M1–M4) · " + vm + ' · native arm64. Not right? <a href="index.html#dl-mac">Pick another build</a>.';
        } else if (arch === "intel") {
          main.href = main.dataset.macIntel; main.textContent = "Download for Intel Mac"; pick("dl-mac-intel", "dl-mac-arm");
          if (note) note.innerHTML = "We think this Mac has an Intel processor · " + vm + ' · native x86_64, macOS 12+. Not right? <a href="index.html#dl-mac">Pick another build</a>.';
        }
      });
    } else if (mobile && note) {
      note.textContent = "Vanta runs on Windows and macOS computers. Open this page on your PC or Mac to download.";
    }
  }

  // mobile menu
  var menu = $(".menu");
  if (menu) {
    menu.addEventListener("click", function () {
      var open = document.body.classList.toggle("nav-open");
      menu.setAttribute("aria-expanded", open ? "true" : "false");
    });
    $$("#site-nav a").forEach(function (a) { a.addEventListener("click", function () { document.body.classList.remove("nav-open"); menu.setAttribute("aria-expanded", "false"); }); });
  }

  // toast + copy
  var toast = $("#toast"), tTimer;
  function say(msg) {
    if (!toast) return;
    toast.textContent = msg; toast.hidden = false;
    clearTimeout(tTimer); tTimer = setTimeout(function () { toast.hidden = true; }, 1800);
  }
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (ok, bad) {
      var t = document.createElement("textarea"); t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.select();
      try { document.execCommand("copy") ? ok() : bad(); } catch (e) { bad(e); } finally { t.remove(); }
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-copy]");
    if (!b) return;
    copy(b.dataset.copy).then(function () {
      say("Copied " + (b.dataset.copy.length > 40 ? "SHA-256" : b.dataset.copy));
      var old = b.textContent; b.textContent = "Copied ✓"; setTimeout(function () { b.textContent = old; }, 1500);
    }, function () { say("Couldn't copy. Select the text instead."); });
  });

  // filters
  function filter(input, items, getText, empty) {
    if (!input) return;
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase(), shown = 0;
      items.forEach(function (el) { var hit = !q || getText(el).indexOf(q) !== -1; el.hidden = !hit; if (hit) shown++; });
      if (empty) empty.hidden = shown !== 0;
    });
  }
  filter($("#srv-filter"), $$("#srv-grid .srv"), function (el) { return (el.dataset.search || el.textContent).toLowerCase(); });
  filter($("#faq-filter"), $$("#faq .faq-item"), function (el) { return el.textContent.toLowerCase(); }, $("#faq-empty"));

  // lightbox
  var lb = $("#lightbox"), lbImg = $("#lb-img"), lbCap = $("#lb-cap"), shots = $$("[data-lightbox]"), idx = 0, lastFocus = null;
  function show(i) {
    idx = (i + shots.length) % shots.length;
    var a = shots[idx]; lbImg.src = a.getAttribute("href"); lbImg.alt = a.dataset.caption || ""; lbCap.textContent = a.dataset.caption || "";
  }
  function openLb(i) { lastFocus = document.activeElement; show(i); lb.hidden = false; document.body.style.overflow = "hidden"; $(".lb-close", lb).focus(); }
  function closeLb() { lb.hidden = true; lbImg.removeAttribute("src"); document.body.style.overflow = ""; if (lastFocus) lastFocus.focus(); }
  if (lb && shots.length) {
    shots.forEach(function (a, i) { a.addEventListener("click", function (e) { e.preventDefault(); openLb(i); }); });
    $(".lb-close", lb).addEventListener("click", closeLb);
    $(".lb-prev", lb).addEventListener("click", function () { show(idx - 1); });
    $(".lb-next", lb).addEventListener("click", function () { show(idx + 1); });
    lb.addEventListener("click", function (e) { if (e.target === lb) closeLb(); });
    document.addEventListener("keydown", function (e) {
      if (lb.hidden) return;
      if (e.key === "Escape") closeLb();
      else if (e.key === "ArrowLeft") show(idx - 1);
      else if (e.key === "ArrowRight") show(idx + 1);
      else if (e.key === "Tab") { // trap focus
        var f = $$("button", lb), first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
  }

  // reveal on scroll + active nav
  var reveals = $$(".reveal");
  if (!reduce && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (es) { es.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } }); }, { rootMargin: "0px 0px -8% 0px" });
    reveals.forEach(function (el) { io.observe(el); });
    var links = {}; $$("#site-nav a[href^='#']").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var so = new IntersectionObserver(function (es) {
      es.forEach(function (en) { var a = links[en.target.id]; if (a && en.isIntersecting) { $$("#site-nav a.active").forEach(function (x) { x.classList.remove("active"); }); a.classList.add("active"); } });
    }, { rootMargin: "-45% 0px -50% 0px" });
    $$("main section[id]").forEach(function (s) { so.observe(s); });
  } else {
    reveals.forEach(function (el) { el.classList.add("in"); });
  }

  // live GitHub data (no trackers; one public API call, cached 10 min)
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function fmtDate(s) { try { return new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); } catch (e) { return s.slice(0, 10); } }
  function mdLite(md) {
    var out = [], inList = false;
    String(md || "").split(/\r?\n/).forEach(function (line) {
      var l = esc(line).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
      var m = /^\s*[-*]\s+(.*)/.exec(l);
      if (m) { if (!inList) { out.push("<ul>"); inList = true; } out.push("<li>" + m[1] + "</li>"); return; }
      if (inList) { out.push("</ul>"); inList = false; }
      var h = /^#{1,6}\s+(.*)/.exec(l);
      if (h) out.push("<p><b>" + h[1] + "</b></p>"); else if (l.trim()) out.push("<p>" + l + "</p>");
    });
    if (inList) out.push("</ul>");
    return out.join("");
  }
  function getReleases() {
    try {
      var c = JSON.parse(sessionStorage.getItem("vanta-rel") || "null");
      if (c && Date.now() - c.t < 600000) return Promise.resolve(c.d);
    } catch (e) {}
    return fetch("https://api.github.com/repos/" + REPO + "/releases?per_page=30", { headers: { Accept: "application/vnd.github+json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { try { sessionStorage.setItem("vanta-rel", JSON.stringify({ t: Date.now(), d: d })); } catch (e) {} return d; });
  }
  var clMode = "all";
  function applyClFilter() {
    $$("#changelog .rel").forEach(function (el) { el.hidden = clMode !== "all" && el.dataset.os !== clMode; });
  }
  $$(".cl-filter button").forEach(function (b) {
    b.addEventListener("click", function () {
      clMode = b.dataset.os;
      $$(".cl-filter button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      applyClFilter();
    });
  });
  getReleases().then(function (rels) {
    rels = rels.filter(function (r) { return !r.draft; });
    if (!rels.length) return;
    // download counts, clearly labeled
    $$(".live-count").forEach(function (p) {
      var r = rels.filter(function (x) { return x.tag_name === p.dataset.tag; })[0];
      if (!r) return;
      var n = r.assets.reduce(function (s, a) { return s + (a.download_count || 0); }, 0);
      if (!n) return;
      p.textContent = n.toLocaleString() + " downloads of this release (live count from GitHub)";
    });
    // older versions
    var older = $("#older-list");
    if (older) {
      var list = rels.filter(function (r) { return ["v1.6.3", "v1.6.3-mac", "v1.6.2", "v1.6.2-mac", "v1.6.1", "v1.6.1-mac", "v1.6.0", "v1.6.0-mac"].indexOf(r.tag_name) === -1; });
      if (list.length) older.innerHTML = list.map(function (r) {
        return '<li><a href="' + esc(r.html_url) + '">' + esc(r.name || r.tag_name) + "</a> <span class=\"small\">· " + fmtDate(r.published_at) + "</span></li>";
      }).join("");
    }
    // full changelog page
    var cl = $("#changelog");
    if (cl) {
      cl.innerHTML = rels.map(function (r) {
        var mac = /-mac$/.test(r.tag_name);
        return '<article class="glass rel" data-os="' + (mac ? "mac" : "win") + '"><h2><a href="' + esc(r.html_url) + '">' + esc(r.name || r.tag_name) + '</a> <small>' + esc(r.tag_name) + " · " + fmtDate(r.published_at) + '</small> <span class="tags"><span class="tag">' + (mac ? "macOS" : "Windows") + "</span></span></h2><div class=\"body\" tabindex=\"0\" role=\"region\" aria-label=\"Release notes for " + esc(r.tag_name) + "\">" + mdLite(r.body) + "</div></article>";
      }).join("");
      var src2 = $("#changelog-src"); if (src2) src2.textContent = "Live from GitHub Releases: " + rels.length + " releases.";
      applyClFilter();
    }
    // updates
    var box = $("#releases");
    if (box) {
      box.innerHTML = rels.slice(0, 6).map(function (r) {
        return '<article class="glass rel"><h3><a href="' + esc(r.html_url) + '">' + esc(r.name || r.tag_name) + "</a> <small>" + esc(r.tag_name) + " · " + fmtDate(r.published_at) + (r.prerelease ? " · pre-release" : "") + '</small></h3><div class="body" tabindex="0" role="region" aria-label="Release notes for ' + esc(r.tag_name) + '">' + mdLite(r.body) + "</div></article>";
      }).join("");
      var src = $("#updates-src"); if (src) src.textContent = "Live from GitHub Releases.";
    }
  }).catch(function () { /* keep the saved copy baked into the page */ });
})();
