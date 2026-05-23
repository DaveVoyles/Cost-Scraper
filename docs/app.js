// Cost Scraper frontend. Two views share helpers; Alpine roots are
// `costScraper` (index.html) and `changeView` (changes.html).

function fmtPrice(v) { return (v == null) ? "—" : "$" + Number(v).toFixed(2); }

function relTime(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.round(diff/60) + " min ago";
  if (diff < 86400) return Math.round(diff/3600) + " hr ago";
  return Math.round(diff/86400) + " d ago";
}

function ageBucket(iso) {
  if (!iso) return "missing";
  const days = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (days < 1) return "fresh";
  if (days < 7) return "ok";
  if (days < 30) return "stale";
  return "old";
}

function bestPriceCell(cell) {
  const r = cell.getRow().getData();
  const bp = r.best_price || {};
  if (bp.value == null) return '<span style="color:var(--muted)">—</span>';
  const msrp = bp.msrp;
  let html = `<span class="price-best">${fmtPrice(bp.value)}</span>`;
  if (msrp && msrp > bp.value) html += `<span class="price-msrp">${fmtPrice(msrp)}</span>`;
  if (bp.url) html = `<a href="${bp.url}" target="_blank" rel="noopener">${html}</a>`;
  return html;
}

function discountCell(cell) {
  const v = cell.getValue();
  if (v == null) return '<span style="color:var(--muted)">—</span>';
  const cls = v >= 50 ? "discount big" : "discount";
  return `<span class="${cls}">-${v}%</span>`;
}

function sourceCell(cell) {
  const bp = cell.getRow().getData().best_price || {};
  return bp.source || "—";
}

function steamCell(cell) {
  const s = cell.getRow().getData().steam || {};
  if (!s.appid) return '<span style="color:var(--muted)">—</span>';
  const owned = s.installed ? '<span class="badge owned" title="Installed via Steam">✓ owned</span>' : "";
  return `${owned}<a href="${s.url}" target="_blank" rel="noopener">app/${s.appid}</a>`;
}

function itadCell(cell) {
  const itad = cell.getRow().getData().itad || {};
  if (!itad.game_id) return '<span style="color:var(--muted)">—</span>';
  const low = itad.historical_low || {};
  if (low.price == null) {
    return `<a href="${itad.url}" target="_blank" rel="noopener">ITAD</a>`;
  }
  return `<a href="${itad.url}" target="_blank" rel="noopener" title="Historical low ${fmtPrice(low.price)} at ${low.store || '?'} on ${low.date || '?'}">low ${fmtPrice(low.price)}</a>`;
}

function titleCell(cell) {
  const r = cell.getRow().getData();
  let badges = "";
  if (r.alert_hit) badges += `<span class="badge alert" title="Below your target $${r.alert_target}">🔔 alert</span>`;
  if (r.dlc_count > 0) badges += `<span class="badge dlc">${r.dlc_count} DLC</span>`;
  return `${badges}${escapeHtml(r.title)}`;
}

function freshnessCell(cell) {
  const r = cell.getRow().getData();
  const dates = [
    r.loaded?.checked_at, r.itad?.checked_at, r.steam ? new Date().toISOString() : null
  ].filter(Boolean);
  if (!dates.length) return '<span class="badge stale">missing</span>';
  const oldest = dates.sort()[0];
  const bucket = ageBucket(oldest);
  const cls = bucket === "fresh" ? "" : (bucket === "ok" ? "" : "badge stale");
  return `<span class="${cls}">${relTime(oldest)}</span>`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// ---------- Catalogue view ----------
function costScraper() {
  return {
    games: [],
    meta: null,
    table: null,
    search: "",
    hideOwned: false,
    alertsOnly: false,
    dealsOnly: false,

    async boot() {
      const [games, meta] = await Promise.all([
        fetch("data/games.json").then(r => r.json()),
        fetch("data/meta.json").then(r => r.json()),
      ]);
      this.games = games.games || [];
      this.meta = meta;
      this.renderTable();
    },

    renderTable() {
      this.table = new Tabulator("#grid", {
        data: this.games,
        layout: "fitColumns",
        responsiveLayout: "collapse",
        height: "70vh",
        initialSort: [{column: "discount_pct", dir: "desc"}],
        columns: [
          { title: "Title", field: "title", formatter: titleCell, headerFilter: "input",
            minWidth: 240, frozen: true },
          { title: "Best Price", field: "best_price.value", formatter: bestPriceCell,
            sorter: "number", width: 140, hozAlign: "right" },
          { title: "Discount", field: "discount_pct", formatter: discountCell,
            sorter: "number", width: 100, hozAlign: "right" },
          { title: "Source", field: "best_price.source", formatter: sourceCell,
            headerFilter: "input", width: 170 },
          { title: "Steam", field: "steam.appid", formatter: steamCell,
            headerFilter: "input", width: 160 },
          { title: "ITAD low", field: "itad.historical_low.price", formatter: itadCell,
            width: 140, hozAlign: "right", sorter: "number" },
          { title: "Playnite Platform", field: "playnite_platform",
            headerFilter: "input", width: 170 },
          { title: "Last checked", field: "loaded.checked_at", formatter: freshnessCell,
            sorter: "string", width: 130, hozAlign: "right" },
        ],
        rowFormatter: (row) => {
          const d = row.getData();
          if (d.alert_hit) row.getElement().style.borderLeft = "3px solid var(--amber)";
        },
      });
    },

    applyFilter() {
      const s = this.search.toLowerCase().trim();
      this.table.setFilter((d) => {
        if (this.hideOwned && d.steam?.installed) return false;
        if (this.alertsOnly && !d.alert_hit) return false;
        if (this.dealsOnly && (d.discount_pct == null || d.discount_pct < 25)) return false;
        if (s) {
          const hay = [
            d.title,
            d.best_price?.source,
            d.steam?.name,
            ...(d.loaded?.editions || []).map(e => e.product),
            ...(d.loaded?.editions || []).map(e => e.edition),
          ].filter(Boolean).join(" ").toLowerCase();
          if (!hay.includes(s)) return false;
        }
        return true;
      });
    },

    resetFilters() {
      this.search = ""; this.hideOwned = false; this.alertsOnly = false; this.dealsOnly = false;
      this.table.clearFilter(true);
    },

    relTime,
  };
}

// ---------- Changes view ----------
function changeView() {
  return {
    changes: null,
    rows: [],
    table: null,
    search: "",
    dropsOnly: true,
    dropCount: 0,
    riseCount: 0,

    async boot() {
      const c = await fetch("data/changes.json").then(r => r.json());
      this.changes = c;
      this.rows = c.changes || [];
      this.dropCount = this.rows.filter(r => (r.delta ?? 0) < 0).length;
      this.riseCount = this.rows.filter(r => (r.delta ?? 0) > 0).length;
      this.renderTable();
      this.applyFilter();
    },

    renderTable() {
      this.table = new Tabulator("#grid", {
        data: this.rows,
        layout: "fitColumns",
        height: "70vh",
        initialSort: [{column: "delta", dir: "asc"}],
        columns: [
          { title: "Title", field: "title", headerFilter: "input", minWidth: 240, frozen: true,
            formatter: c => escapeHtml(c.getValue()) },
          { title: "Now",  field: "price_now",  formatter: c => fmtPrice(c.getValue()),
            width: 100, hozAlign: "right", sorter: "number" },
          { title: "Prev", field: "price_prev", formatter: c => fmtPrice(c.getValue()),
            width: 100, hozAlign: "right", sorter: "number" },
          { title: "Δ $", field: "delta", formatter: c => {
              const v = c.getValue();
              if (v == null) return "—";
              const cls = v < 0 ? "delta-down" : v > 0 ? "delta-up" : "delta-flat";
              return `<span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(2)}</span>`;
            },
            width: 100, hozAlign: "right", sorter: "number" },
          { title: "Δ %", field: "delta_pct", formatter: c => {
              const v = c.getValue();
              if (v == null) return "—";
              const cls = v < 0 ? "delta-down" : v > 0 ? "delta-up" : "delta-flat";
              return `<span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(1)}%</span>`;
            },
            width: 100, hozAlign: "right", sorter: "number" },
          { title: "Source", field: "source_now", headerFilter: "input", width: 170 },
          { title: "Hist. low", field: "historical_low.price",
            formatter: c => fmtPrice(c.getValue()), width: 110, hozAlign: "right", sorter: "number" },
        ],
      });
    },

    applyFilter() {
      const s = this.search.toLowerCase().trim();
      this.table.setFilter((d) => {
        if (this.dropsOnly && !(d.delta < 0)) return false;
        if (s && !(d.title || "").toLowerCase().includes(s)) return false;
        return true;
      });
    },

    relTime,
  };
}
