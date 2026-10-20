/**
 * Sales explorer View (examples/sales_explorer). Uses the official App SDK:
 * filters call the app-only `query_sales` tool through the host, and the
 * selected region is sent back with updateModelContext / sendMessage.
 */
import { App } from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/client";

type Row = { region: string; product: string; quarter: string; units: number; revenue: number };
type Data = {
  filters: { region: string; product: string; quarter: string };
  regions: string[];
  products: string[];
  quarters: string[];
  rows: Row[];
  total_revenue: number;
};

const app = new App({ name: "Sales explorer", version: "1.0.0" });
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const money = (n: number) => `$${n.toLocaleString("en-US")}`;
let data: Data | null = null;
let selected: string | null = null;

function fillSelect(el: HTMLSelectElement, options: string[], value: string) {
  if (el.options.length !== options.length + 1) {
    el.replaceChildren(...["All", ...options].map((o) => new Option(o, o)));
  }
  el.value = value;
}

function regionTotals(rows: Row[], regions: string[]) {
  return regions.map((region) => ({
    region,
    revenue: rows.filter((r) => r.region === region).reduce((s, r) => s + r.revenue, 0),
  }));
}

function render() {
  if (!data) return;
  fillSelect($("product"), data.products, data.filters.product);
  fillSelect($("quarter"), data.quarters, data.filters.quarter);
  $("total").textContent = money(data.total_revenue);

  const totals = regionTotals(data.rows, data.regions);
  const max = Math.max(1, ...totals.map((t) => t.revenue));
  $("bars").replaceChildren(
    ...totals.map(({ region, revenue }) => {
      const b = document.createElement("button");
      b.className = "bar";
      b.setAttribute("aria-pressed", String(region === selected));
      b.setAttribute("aria-label", `${region}: ${money(revenue)}`);
      b.innerHTML = `<span></span><span class="fill"></span><span class="amount"></span>`;
      (b.children[0] as HTMLElement).textContent = region;
      (b.children[1] as HTMLElement).style.width = `${(100 * revenue) / max}%`;
      (b.children[2] as HTMLElement).textContent = money(revenue);
      b.addEventListener("click", () => select(region));
      return b;
    }),
  );

  const scope = selected ? data.rows.filter((r) => r.region === selected) : data.rows;
  const byProduct = data.products.map((product) => {
    const rs = scope.filter((r) => r.product === product);
    return { product, units: rs.reduce((s, r) => s + r.units, 0), revenue: rs.reduce((s, r) => s + r.revenue, 0) };
  });
  $("rows").replaceChildren(
    ...byProduct
      .filter((p) => p.revenue > 0)
      .sort((a, b) => b.revenue - a.revenue)
      .map((p) => {
        const tr = document.createElement("tr");
        for (const [text, cls] of [[p.product, ""], [p.units.toLocaleString("en-US"), "num"], [money(p.revenue), "num"]]) {
          const td = document.createElement("td");
          td.textContent = text;
          if (cls) td.className = cls;
          tr.appendChild(td);
        }
        return tr;
      }),
  );
  ($("ask") as HTMLButtonElement).disabled = !selected;
}

function selectionSummary() {
  if (!data || !selected) return null;
  const rows = data.rows.filter((r) => r.region === selected);
  const revenue = rows.reduce((s, r) => s + r.revenue, 0);
  const { product, quarter } = data.filters;
  return { region: selected, product, quarter, revenue };
}

async function shareSelection() {
  const s = selectionSummary();
  if (!s) return;
  await app.updateModelContext({
    content: [
      { type: "text", text: `User selected ${s.region} (product: ${s.product}, quarter: ${s.quarter}), revenue ${money(s.revenue)}.` },
    ],
    structuredContent: { selection: s },
  });
  $("selection-status").textContent = `Shared with host: ${s.region}, ${money(s.revenue)}`;
}

async function select(region: string) {
  selected = selected === region ? null : region;
  render();
  if (selected) await shareSelection();
  else {
    await app.updateModelContext({ content: [] });
    $("selection-status").textContent = "Click a region to share it with the host.";
  }
}

async function refilter() {
  if (!data) return;
  const args = { product: $<HTMLSelectElement>("product").value, quarter: $<HTMLSelectElement>("quarter").value };
  try {
    const result = await app.callServerTool({ name: "query_sales", arguments: args });
    data = result.structuredContent as Data;
    render();
    if (selected) await shareSelection();
  } catch (e) {
    $("selection-status").textContent = `Query failed: ${e instanceof Error ? e.message : String(e)}`;
  }
}

app.ontoolresult = (result: CallToolResult) => {
  data = result.structuredContent as Data;
  if (data.filters.region !== "All") selected = data.filters.region;
  render();
  if (selected) void shareSelection();
};
$("product").addEventListener("change", refilter);
$("quarter").addEventListener("change", refilter);
$("ask").addEventListener("click", async () => {
  const s = selectionSummary();
  if (!s) return;
  const res = await app.sendMessage({
    role: "user",
    content: [{ type: "text", text: `What is driving sales in ${s.region} (product: ${s.product}, quarter: ${s.quarter})?` }],
  });
  $("selection-status").textContent = res.isError ? "The host declined the message." : "Question sent to the chat.";
});

app.connect();
