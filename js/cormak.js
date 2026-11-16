import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const track = "utm_source=comfyui&utm_medium=integration&utm_campaign=cormak_nodes";
const links = [
  ["Create Free Account", "https://app.cormak.app/3d?" + track + "&utm_content=create_account"],
  ["Manage Credits", "https://app.cormak.app/billing?" + track + "&utm_content=manage_credits"],
  ["Open Cormak", "https://cormak.app/?" + track + "&utm_content=open_cormak"],
  ["Documentation", "https://cormak.app/blog/create-3d-model-with-ai?" + track + "&utm_content=documentation"],
  ["Manage Connections", "https://app.cormak.app/mcp?" + track + "&utm_content=connections"],
];
const apiCall = async (action, method = "GET", body) => {
  const response = await api.fetchApi("/cormak/" + action, {
    method, cache: "no-store", credentials: "same-origin",
    headers: { "X-Cormak-Request": "1", ...(body && { "Content-Type": "application/json" }) },
    ...(body && { body: JSON.stringify(body) }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Cormak request failed.");
  return payload;
};

function el(tag, text, parent) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  parent?.append(item);
  return item;
}

function link(label, url, parent) {
  const a = el("a", label, parent);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  Object.assign(a.style, { margin: "4px 0", textDecoration: "underline", overflowWrap: "anywhere" });
  return a;
}

function render(container) {
  container.replaceChildren();
  const panel = el("section", null, container);
  panel.setAttribute("aria-label", "Cormak");
  Object.assign(panel.style, { padding: "16px", display: "grid", gap: "10px", alignContent: "start", minWidth: "0" });
  el("h2", "Cormak", panel).style.margin = "0";
  const label = el("div", "Checking connection…", panel);
  label.setAttribute("role", "status");
  const message = el("div", "", panel);
  message.setAttribute("role", "status");
  const connect = el("button", "Connect Cormak", panel);
  const fallback = link("Open Cormak sign-in", "https://app.cormak.app", panel);
  const disconnect = el("button", "Disconnect Cormak", panel);
  const refresh = el("button", "Refresh balance", panel);
  connect.hidden = disconnect.hidden = refresh.hidden = fallback.hidden = true;
  const approvals = el("div", null, panel);

  const quoteTitle = el("label", "Quality", panel);
  const quality = el("select", null, quoteTitle);
  quality.style.marginInlineStart = "8px";
  ["basic", "medium", "high"].forEach((value) => { el("option", value, quality).value = value; });
  const quote = el("button", "Check credit cost", panel);
  const price = el("div", "", panel);
  price.setAttribute("role", "status");
  quoteTitle.hidden = quote.hidden = true;
  el("p", "Cloud generation uses Cormak AI credits. Enter a credit cap in the node after checking the cost. On retry, keep the same run number.", panel);
  links.forEach(([name, url]) => link(name, url, panel));

  let timer;
  let stopped = false;
  let refreshing = false;
  let previous = false;
  let balanceText = "";
  let busy = false;
  const showError = (error) => { message.textContent = error instanceof Error ? error.message : "Cormak is unavailable."; };
  const getBalance = async () => {
    const data = await apiCall("account", "POST");
    balanceText = `${data.credits} AI credits${data.plan ? " · " + data.plan : ""}`;
    label.textContent = balanceText;
  };
  const safe = async (fn) => {
    if (busy) return;
    busy = true;
    message.textContent = "";
    try { await fn(); } catch (error) { showError(error); }
    finally { busy = false; }
  };

  const update = async () => {
    if (refreshing || stopped || !panel.isConnected) return;
    refreshing = true;
    try {
      const state = await apiCall("state");
      if (stopped) return;
      connect.hidden = state.connected || state.pending;
      fallback.hidden = !state.pending || !fallback.dataset.ready;
      disconnect.hidden = !state.connected && !state.pending;
      refresh.hidden = quoteTitle.hidden = quote.hidden = !state.connected;
      if (state.error) message.textContent = state.error;
      label.textContent = state.connected ? balanceText || "Connected" :
        state.pending ? "Finish sign-in in your browser." : "Disconnected";
      if (!state.connected) { balanceText = ""; price.textContent = ""; }
      if (state.connected && !previous) {
        previous = true;
        await getBalance();
      } else previous = state.connected;
      const jobs = Object.entries(state.approvals || {}).filter(([job, url]) =>
        /^[0-9a-f-]{36}$/.test(job) && /^https:\/\/app\.cormak\.app\/mcp\?approval_id=[0-9a-f-]{36}$/.test(url));
      if (approvals.dataset.jobs !== JSON.stringify(jobs)) {
        approvals.dataset.jobs = JSON.stringify(jobs);
        approvals.replaceChildren();
        if (jobs.length) el("p", "Open the request in Cormak. After approval, queue the same workflow again.", approvals);
        jobs.forEach(([job, url]) => link("Approve job " + job.slice(0, 8), url, approvals));
      }
    } catch (error) {
      label.textContent = "Cormak connection unavailable";
      showError(error);
    } finally {
      refreshing = false;
    }
  };

  connect.addEventListener("click", () => {
    if (busy) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    void safe(async () => {
      try {
        const data = await apiCall("connect", "POST");
        const url = new URL(data.url);
        if (url.origin !== "https://mcp.cormak.app" || url.pathname !== "/mcp/authorize" ||
            url.username || url.password || url.hash) throw new Error("Invalid Cormak sign-in destination.");
        fallback.href = url.href;
        fallback.dataset.ready = "1";
        fallback.hidden = false;
        if (popup) popup.location.replace(url.href);
        await update();
      } catch (error) {
        popup?.close();
        throw error;
      }
    });
  });
  disconnect.addEventListener("click", () => void safe(async () => {
    try { await apiCall("disconnect", "POST"); }
    finally {
      delete fallback.dataset.ready;
      await update();
    }
  }));
  refresh.addEventListener("click", () => void safe(getBalance));
  quality.addEventListener("change", () => { price.textContent = ""; });
  quote.addEventListener("click", () => void safe(async () => {
    const choice = quality.value;
    const data = await apiCall("quote", "POST", { quality: choice });
    if (quality.value === choice) price.textContent = `${choice}: up to ${data.maximum} AI credits. ` +
      (data.advance ? "Within your Cormak spending permission." : "Confirm the request in Cormak if prompted.");
  }));
  api.addEventListener("execution_error", update);
  api.addEventListener("execution_success", update);
  timer = window.setInterval(() => void update(), 1500);
  void update();
  return () => {
    stopped = true;
    window.clearInterval(timer);
    api.removeEventListener("execution_error", update);
    api.removeEventListener("execution_success", update);
  };
}

let dispose;
app.registerExtension({
  name: "Cormak.Nodes",
  setup() {
    app.extensionManager.registerSidebarTab({
      id: "cormak",
      title: "Cormak",
      tooltip: "Cormak account and AI credit cost",
      icon: "pi pi-box",
      type: "custom",
      render(container) { dispose?.(); dispose = render(container); },
      destroy() { dispose?.(); dispose = undefined; },
    });
  },
});
