// Sales explorer View: plain JavaScript on the official MCP Apps SDK (`McpApps`),
// which shiny_mcp_host.view.app_html() inlines. No build step.
// Filters call the app-only `query_sales` tool through the host; the selected
// region is shared back with updateModelContext, and questions with sendMessage.

const app = new McpApps.App({ name: "Sales explorer", version: "1.0.0" });
const $ = (id) => document.getElementById(id);
const money = (n) => `$${n.toLocaleString("en-US")}`;
let data = null;
let selected = null;

function fillSelect(el, options, value) {
  if (el.options.length !== options.length + 1) {
    el.replaceChildren(...["All", ...options].map((o) => new Option(o, o)));
  }
  el.value = value;
}

function cell(text, cls) {
  const td = document.createElement("td");
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

function render() {
  if (!data) return;
  fillSelect($("product"), data.products, data.filters.product);
  fillSelect($("quarter"), data.quarters, data.filters.quarter);
  $("total").textContent = money(data.total_revenue);

  const totals = data.regions.map((region) => ({
    region,
    revenue: data.rows.filter((r) => r.region === region).reduce((s, r) => s + r.revenue, 0),
  }));
  const max = Math.max(1, ...totals.map((t) => t.revenue));
  $("bars").replaceChildren(
    ...totals.map(({ region, revenue }) => {
      const b = document.createElement("button");
      b.className = "bar";
      b.setAttribute("aria-pressed", String(region === selected));
      b.setAttribute("aria-label", `${region}: ${money(revenue)}`);
      const name = document.createElement("span");
      const fill = document.createElement("span");
      const amount = document.createElement("span");
      name.textContent = region;
      fill.className = "fill";
      fill.style.width = `${(100 * revenue) / max}%`;
      amount.className = "amount";
      amount.textContent = money(revenue);
      b.append(name, fill, amount);
      b.addEventListener("click", () => select(region));
      return b;
    }),
  );

  const scope = selected ? data.rows.filter((r) => r.region === selected) : data.rows;
  const byProduct = data.products
    .map((product) => {
      const rs = scope.filter((r) => r.product === product);
      return { product, units: rs.reduce((s, r) => s + r.units, 0), revenue: rs.reduce((s, r) => s + r.revenue, 0) };
    })
    .filter((p) => p.revenue > 0)
    .sort((a, b) => b.revenue - a.revenue);
  $("rows").replaceChildren(
    ...byProduct.map((p) => {
      const tr = document.createElement("tr");
      tr.append(cell(p.product), cell(p.units.toLocaleString("en-US"), "num"), cell(money(p.revenue), "num"));
      return tr;
    }),
  );
  $("ask").disabled = !selected;
}

function selection() {
  if (!data || !selected) return null;
  const revenue = data.rows.filter((r) => r.region === selected).reduce((s, r) => s + r.revenue, 0);
  return { region: selected, product: data.filters.product, quarter: data.filters.quarter, revenue };
}

async function shareSelection() {
  const s = selection();
  if (!s) return;
  await app.updateModelContext({
    content: [
      {
        type: "text",
        text: `User selected ${s.region} (product: ${s.product}, quarter: ${s.quarter}), revenue ${money(s.revenue)}.`,
      },
    ],
    structuredContent: { selection: s },
  });
  $("selection-status").textContent = `Shared with host: ${s.region}, ${money(s.revenue)}`;
}

async function select(region) {
  selected = selected === region ? null : region;
  render();
  if (selected) {
    await shareSelection();
  } else {
    await app.updateModelContext({ content: [] });
    $("selection-status").textContent = "Click a region to share it with the host.";
  }
}

async function refilter() {
  if (!data) return;
  const args = { product: $("product").value, quarter: $("quarter").value };
  try {
    const result = await app.callServerTool({ name: "query_sales", arguments: args });
    data = result.structuredContent;
    render();
    if (selected) await shareSelection();
  } catch (e) {
    $("selection-status").textContent = `Query failed: ${e.message ?? e}`;
  }
}

app.ontoolresult = (result) => {
  data = result.structuredContent;
  if (data.filters.region !== "All") selected = data.filters.region;
  render();
  if (selected) shareSelection();
};
$("product").addEventListener("change", refilter);
$("quarter").addEventListener("change", refilter);
$("ask").addEventListener("click", async () => {
  const s = selection();
  if (!s) return;
  const res = await app.sendMessage({
    role: "user",
    content: [{ type: "text", text: `What is driving sales in ${s.region} (product: ${s.product}, quarter: ${s.quarter})?` }],
  });
  $("selection-status").textContent = res.isError ? "The host declined the message." : "Question sent to the chat.";
});

app.connect();
