#!/usr/bin/env python3
"""Generate a single, self-contained, shareable HTML page from listings.db.

    uv run viz.py                 # reads ./listings.db, writes ./listings_viz.html
    uv run viz.py other.db out.html

This is a GENERATOR, not a server. It reads the SQLite store ONCE at generation
time and inlines the rows into the page as `const LISTINGS = [...]`. The output
`listings_viz.html` is one file you can email / double-click — no server, no DB
at view time. Charts (Plotly) and the table (Tabulator) load from CDNs, so the
viewer needs an internet connection; a fully-offline variant would inline those
libs (multi-MB) which we deliberately do not do here.

NOTE: listings.db has no lat/lng, so there is no map. Coordinates are available
via tmp/listings_with_locations.json or by extending store.py + `otodom.py
location <url>`. See the in-page note.
"""
import json
import sqlite3
import sys

DB = "listings.db"
OUT = "listings_viz.html"

# Plotly "cartesian" partial bundle (bar + histogram + scatter), smaller than full.
PLOTLY_CDN = "https://cdn.plot.ly/plotly-cartesian-3.6.0.min.js"
TABULATOR_CSS = "https://unpkg.com/tabulator-tables@6.4.0/dist/css/tabulator_midnight.min.css"
TABULATOR_JS = "https://unpkg.com/tabulator-tables@6.4.0/dist/js/tabulator.min.js"


def _to_float(v):
    """area_m2/price may be REAL, a string like '67.36', empty, or None."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def load_listings(db_path=DB):
    """Read the listings table into a list of plain dicts (JSON-ready)."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM listings").fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            extras = json.loads(r["extras"]) if r["extras"] else []
        except (ValueError, TypeError):
            extras = []
        out.append({
            "id": r["id"],
            "url": r["url"],
            "title": r["title"] or "(no title)",
            "area_tag": r["area_tag"],          # may be None
            "rooms": r["rooms"],                # may be None
            "area_m2": _to_float(r["area_m2"]),  # float or None
            "price": _to_float(r["price"]),     # float or None
            "bathtub": r["bathtub"] or "unknown",   # yes/no/unknown
            "garden": r["garden"] or "unknown",     # yes/no/unknown
            "prefs_score": r["prefs_score"],
            "extras": extras,
        })
    return out


def render(listings):
    # Escape "</" so a stray "</script>" in any field can't break the inline JSON.
    data_js = json.dumps(listings, ensure_ascii=False).replace("</", "<\\/")
    area_tags = sorted({l["area_tag"] for l in listings if l["area_tag"]})
    prices = [l["price"] for l in listings if l["price"] is not None]
    median = sorted(prices)[len(prices) // 2] if prices else 0
    return TEMPLATE.format(
        plotly=PLOTLY_CDN, tab_css=TABULATOR_CSS, tab_js=TABULATOR_JS,
        data=data_js, total=len(listings), median=int(median),
        area_tags=json.dumps(area_tags, ensure_ascii=False),
    )


# CDN libs (Plotly + Tabulator) are loaded by <script src>; the file therefore
# needs internet to render. An offline build would inline these (multi-MB) — not
# done here to keep the shareable file small.
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Otodom listings — browse &amp; compare</title>
<!-- Charts + table load from CDNs => needs internet. Offline = inline libs (multi-MB), not done here. -->
<script src="{plotly}" charset="utf-8"></script>
<link href="{tab_css}" rel="stylesheet">
<script src="{tab_js}"></script>
<style>
  :root{{--bg:#0f1419;--panel:#1a212b;--panel2:#222c39;--line:#2e3a48;
    --ink:#e6edf3;--muted:#8b98a8;--accent:#4cc2ff}}
  *{{box-sizing:border-box}}
  body{{margin:0;font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}}
  header{{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}}
  header h1{{font-size:18px;margin:0;font-weight:650}}
  header .count{{color:var(--accent);font-weight:650}}
  header .sub{{color:var(--muted);font-size:12px}}
  .note{{margin:10px 16px;padding:8px 12px;background:#2a2233;border:1px solid #4a3a5a;border-radius:6px;color:#d9c7ec;font-size:12px}}
  .wrap{{display:grid;grid-template-columns:280px 1fr;gap:0;align-items:start}}
  aside{{border-right:1px solid var(--line);padding:14px;background:var(--panel);position:sticky;top:0}}
  main{{padding:14px 16px;overflow:auto}}
  .grp{{margin-bottom:18px}}
  .grp h3{{margin:0 0 8px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}
  label.ck{{display:inline-flex;gap:6px;align-items:center;cursor:pointer;padding:3px 8px;margin:0 6px 6px 0;background:var(--panel2);border:1px solid var(--line);border-radius:6px}}
  select,input[type=number]{{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font:inherit;width:100%}}
  input[type=range]{{width:100%;accent-color:var(--accent)}}
  .twin{{display:flex;gap:8px}}
  .twin>div{{flex:1}}
  .twin label{{font-size:11px;color:var(--muted)}}
  button.reset{{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 10px;cursor:pointer;font:inherit;width:100%}}
  .charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin-bottom:14px}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px}}
  .plot{{width:100%;height:260px}}
  #table{{border:1px solid var(--line);border-radius:8px}}
  footer{{padding:14px 18px;color:var(--muted);font-size:12px}}
</style>
</head>
<body>
<header>
  <h1>Otodom listings</h1>
  <span class="sub">showing <span id="shown" class="count">0</span> / {total} listings</span>
  <span class="sub">median rent <span id="median" class="count">{median}</span> PLN/mo</span>
</header>

<div class="note">No map: <code>listings.db</code> has no lat/lng columns. A map needs
coordinates — available in <code>tmp/listings_with_locations.json</code> or by extending
<code>store.py</code> + <code>otodom.py location &lt;url&gt;</code>.</div>

<div class="wrap">
  <aside>
    <div class="grp"><h3>Rooms</h3>
      <label class="ck"><input type="checkbox" class="rooms" value="3" checked>3</label>
      <label class="ck"><input type="checkbox" class="rooms" value="4" checked>4</label>
      <label class="ck"><input type="checkbox" class="rooms" value="5plus" checked>5+</label>
    </div>
    <div class="grp"><h3>Price (PLN/mo) &le; <span id="priceLbl"></span></h3>
      <input type="range" id="priceMax">
    </div>
    <div class="grp"><h3>Bathtub</h3>
      <select id="bathtub"><option value="any">any</option><option value="yes">yes</option><option value="no">no</option></select>
    </div>
    <div class="grp"><h3>Garden</h3>
      <select id="garden"><option value="any">any</option><option value="yes">yes</option><option value="no">no</option></select>
    </div>
    <div class="grp"><h3>Area tag</h3>
      <div id="areaTags"></div>
    </div>
    <div class="grp"><button class="reset" id="reset">Reset filters</button></div>
  </aside>

  <main>
    <div class="charts">
      <div class="card"><div id="histPrice" class="plot"></div></div>
      <div class="card"><div id="scatterPA" class="plot"></div></div>
      <div class="card"><div id="barArea" class="plot"></div></div>
    </div>
    <div id="table"></div>
  </main>
</div>

<footer>Generated from listings.db at build time. Re-run <code>uv run viz.py</code> to refresh.</footer>

<script>
const LISTINGS = {data};
const AREA_TAGS = {area_tags};

// --- build area-tag checkboxes ---
const areaBox = document.getElementById("areaTags");
AREA_TAGS.forEach(t => {{
  const l = document.createElement("label"); l.className = "ck";
  l.innerHTML = '<input type="checkbox" class="atag" value="' + t + '" checked>' + t;
  areaBox.appendChild(l);
}});

// --- price slider bounds ---
const allPrices = LISTINGS.map(l => l.price).filter(p => p != null);
const maxPrice = allPrices.length ? Math.ceil(Math.max(...allPrices)) : 0;
const slider = document.getElementById("priceMax");
slider.min = 0; slider.max = maxPrice; slider.value = maxPrice; slider.step = 100;

const PLOT_LAYOUT = {{
  paper_bgcolor:"#1a212b", plot_bgcolor:"#1a212b", font:{{color:"#e6edf3",size:11}},
  margin:{{l:45,r:12,t:34,b:38}}, xaxis:{{gridcolor:"#2e3a48"}}, yaxis:{{gridcolor:"#2e3a48"}}
}};
const PLOT_CFG = {{responsive:true, displayModeBar:false}};

// --- Tabulator table ---
const table = new Tabulator("#table", {{
  data: LISTINGS, layout:"fitColumns", height:"520px",
  placeholder:"No listings match the filters.",
  initialSort:[{{column:"prefs_score", dir:"desc"}}],
  columns:[
    {{title:"Title", field:"url", widthGrow:3, formatter:"link",
      formatterParams:{{labelField:"title", target:"_blank"}},
      sorter:(a,b,ar,br)=> (ar.getData().title||"").localeCompare(br.getData().title||"")}},
    {{title:"Rooms", field:"rooms", hozAlign:"center", sorter:"number", width:80}},
    {{title:"m\\u00b2", field:"area_m2", hozAlign:"right", sorter:"number", width:80}},
    {{title:"Price", field:"price", hozAlign:"right", sorter:"number", width:100,
      formatter:c=> c.getValue()==null ? "?" : Math.round(c.getValue()).toLocaleString()}},
    {{title:"Bathtub", field:"bathtub", hozAlign:"center", width:90}},
    {{title:"Garden", field:"garden", hozAlign:"center", width:90}},
    {{title:"Score", field:"prefs_score", hozAlign:"center", sorter:"number", width:80}},
  ],
}});

function selectedValues(cls) {{
  return [...document.querySelectorAll("." + cls + ":checked")].map(e => e.value);
}}

function applyFilters() {{
  const rooms = selectedValues("rooms");
  const tags = selectedValues("atag");
  const bathtub = document.getElementById("bathtub").value;
  const garden = document.getElementById("garden").value;
  const pmax = Number(slider.value);
  document.getElementById("priceLbl").textContent = pmax.toLocaleString();

  const out = LISTINGS.filter(l => {{
    const roomKey = l.rooms == null ? null : (l.rooms >= 5 ? "5plus" : String(l.rooms));
    if (!rooms.includes(roomKey)) return false;
    if (l.area_tag && !tags.includes(l.area_tag)) return false;
    if (bathtub !== "any" && l.bathtub !== bathtub) return false;
    if (garden !== "any" && l.garden !== garden) return false;
    if (l.price != null && l.price > pmax) return false;
    return true;
  }});

  table.replaceData(out);
  document.getElementById("shown").textContent = out.length;
  const pr = out.map(l => l.price).filter(p => p != null).sort((a,b)=>a-b);
  document.getElementById("median").textContent = pr.length ? Math.round(pr[Math.floor(pr.length/2)]).toLocaleString() : "-";
  drawCharts(out);
}}

function drawCharts(rows) {{
  Plotly.react("histPrice",
    [{{x: rows.map(r=>r.price).filter(p=>p!=null), type:"histogram",
       marker:{{color:"#4cc2ff"}}, nbinsx:25}}],
    Object.assign({{title:"Price distribution (PLN/mo)"}}, PLOT_LAYOUT), PLOT_CFG);

  Plotly.react("scatterPA",
    [{{x: rows.map(r=>r.area_m2), y: rows.map(r=>r.price), type:"scatter", mode:"markers",
       marker:{{color:"#36d399",size:7,opacity:0.75}},
       text: rows.map(r=>r.title), hovertemplate:"%{{text}}<br>%{{x}} m\\u00b2 — %{{y}} PLN<extra></extra>"}}],
    Object.assign({{title:"Price vs area", xaxis:{{title:"m\\u00b2",gridcolor:"#2e3a48"}}, yaxis:{{title:"PLN",gridcolor:"#2e3a48"}}}}, PLOT_LAYOUT), PLOT_CFG);

  const counts = {{}};
  rows.forEach(r => {{ const k = r.area_tag || "(none)"; counts[k] = (counts[k]||0)+1; }});
  const keys = Object.keys(counts).sort();
  Plotly.react("barArea",
    [{{x: keys, y: keys.map(k=>counts[k]), type:"bar", marker:{{color:"#fbbd23"}}}}],
    Object.assign({{title:"Count by area tag"}}, PLOT_LAYOUT), PLOT_CFG);
}}

document.querySelectorAll("input,select").forEach(el => el.addEventListener("input", applyFilters));
document.getElementById("reset").addEventListener("click", () => {{
  document.querySelectorAll(".rooms,.atag").forEach(e => e.checked = true);
  document.getElementById("bathtub").value = "any";
  document.getElementById("garden").value = "any";
  slider.value = maxPrice;
  applyFilters();
}});

applyFilters();
</script>
</body>
</html>
"""


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    db_path = argv[0] if len(argv) > 0 else DB
    out_path = argv[1] if len(argv) > 1 else OUT
    listings = load_listings(db_path)
    html = render(listings)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[viz] wrote {out_path} ({len(html):,} bytes) from {len(listings)} listings in {db_path}",
          file=sys.stderr)
    return html


if __name__ == "__main__":
    # Tiny self-check (no test framework): only runs when SELF_CHECK is set so it
    # never interferes with normal generation.
    if "--self-check" in sys.argv:
        rows = load_listings(DB)
        assert rows, "load_listings returned no rows"
        assert {"url", "title", "rooms", "area_m2", "price", "bathtub", "garden",
                "prefs_score"} <= set(rows[0]), "missing expected columns"
        html = render(rows)
        assert html and "const LISTINGS" in html, "LISTINGS array missing"
        assert rows[0]["title"] in html, "known listing title not inlined"
        print("[viz] self-check OK", file=sys.stderr)
    else:
        main()
