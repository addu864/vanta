// Live release notes from the GitHub Releases API; the static list in index.html stays if this fails.
(function () {
  document.querySelectorAll("nav a").forEach(function (a) {
    a.addEventListener("click", function () { document.body.classList.remove("nav-open"); });
  });
  var box = document.getElementById("releases");
  var src = document.getElementById("updates-src");
  if (!box || !window.fetch) return;
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function plain(md) {
    return String(md || "")
      .replace(/\r/g, "")
      .replace(/```[\s\S]*?```/g, "")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1")
      .replace(/^#+\s*/gm, "")
      .replace(/\*\*|__|`/g, "")
      .replace(/^>\s?/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }
  function mb(n) { return (n / 1048576).toFixed(0) + " MB"; }
  fetch("https://api.github.com/repos/addu864/vanta/releases?per_page=10", { headers: { Accept: "application/vnd.github+json" } })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (list) {
      if (!Array.isArray(list) || !list.length) return;
      box.innerHTML = list.map(function (rel) {
        var date = (rel.published_at || "").slice(0, 10);
        var assets = (rel.assets || []).map(function (a) {
          return '<a href="' + esc(a.browser_download_url) + '">' + esc(a.name) + "</a> (" + mb(a.size) + ")";
        }).join(" · ");
        return '<article class="glass rel"><h3>' + esc(rel.name || rel.tag_name) + " <small>" + esc(rel.tag_name) + " · " + esc(date) + "</small></h3>" +
          (assets ? '<p class="note">' + assets + "</p>" : "") +
          '<div class="body">' + esc(plain(rel.body)) + "</div>" +
          '<p><a href="' + esc(rel.html_url) + '" target="_blank" rel="noopener">Full notes on GitHub →</a></p></article>';
      }).join("");
      if (src) src.textContent = "Live from GitHub Releases.";
    })
    .catch(function () { if (src) src.textContent = "Showing saved release notes (GitHub couldn't be reached)."; });
})();
