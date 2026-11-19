const byId = (id) => document.getElementById(id);
const names = {operator: "Vận hành", reviewer: "Soát xét", auditor: "Kiểm toán"};
const states = {open: "Đang mở", in_review: "Chờ soát xét", closed: "Đã đóng"};
const flags = {
  duplicate_payout: "Trùng dòng chi trả", unknown_order: "Thiếu đơn nguồn",
  currency_mismatch: "Sai tiền tệ", missing_payout: "Thiếu khoản đến hạn",
  amount_mismatch: "Chênh số tiền",
};
const actionNames = {
  detected: "Phát hiện ngoại lệ", submitted: "Gửi khắc phục",
  retest_failed: "Tái kiểm thử chưa đạt", retest_passed: "Tái kiểm thử đạt",
};
const number = new Intl.NumberFormat("vi-VN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const timestamp = new Intl.DateTimeFormat("vi-VN", {dateStyle: "short", timeStyle: "short"});
let session;
let overview = {runs: [], cases: []};
let currentCase;

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}

async function api(url, json) {
  const post = json !== undefined;
  const headers = post ? {"Content-Type": "application/json"} : {};
  if (post && session) headers["X-Meridian-CSRF"] = session.csrf;
  if (url === "/api/login") headers["X-Meridian-Client"] = "workbench";
  const response = await fetch(url, post ? {method: "POST", headers, body: JSON.stringify(json)} : {});
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 401 && session) show(null);
    const error = new Error(data?.error ?? "Không thực hiện được yêu cầu.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function show(user) {
  session = user;
  byId("login-screen").hidden = Boolean(user);
  byId("navigation").hidden = !user;
  byId("workspace").hidden = !user;
  byId("show-intake").hidden = user?.role !== "operator";
  byId("identity").textContent = user ? user.username + " · " + names[user.role] : "";
  if (!user) {
    for (const id of ["case", "intake"]) byId(id).close();
    currentCase = null;
    overview = {runs: [], cases: []};
    byId("case-list").replaceChildren();
    byId("case-body").replaceChildren();
    byId("notice").textContent = "";
  }
}

function announce(text, success = false, target = byId("notice")) {
  target.textContent = text;
  target.classList.toggle("success", success);
}

function renderOverview() {
  const period = byId("period").value;
  const state = byId("state-filter").value;
  const cases = overview.cases.filter((item) => !period || item.as_of + "|" + item.period === period);
  const visible = cases.filter((item) => !state || item.status === state);
  const stats = [
    ["Tổng hồ sơ", cases.length],
    ["Đang mở", cases.filter((item) => item.status === "open").length],
    ["Chờ soát xét", cases.filter((item) => item.status === "in_review").length],
    ["Đã đóng", cases.filter((item) => item.status === "closed").length],
  ];
  byId("summary").replaceChildren(...stats.map(([label, count]) => {
    const metric = node("div", undefined, "metric");
    metric.append(node("b", count), node("span", label));
    return metric;
  }));
  byId("case-list").replaceChildren(...visible.map((item) => {
    const tr = node("tr");
    const order = node("td");
    const link = node("button", item.reference, "row-link");
    link.type = "button";
    link.onclick = async () => {
      try {
        await loadCase(item.id);
        byId("case").showModal();
      } catch (error) {
        announce(error.message);
      }
    };
    order.append(link, node("small", item.channel + " · " + item.account));
    const statusCell = node("td");
    statusCell.append(node("span", states[item.status], "badge " + item.status));
    tr.append(order, node("td", item.period), node("td", item.flags.map((flag) => flags[flag]).join(", ")), statusCell);
    return tr;
  }));
  byId("empty").hidden = visible.length > 0;
  byId("empty").textContent = overview.runs.length
    ? "Không có ngoại lệ cho bộ lọc này."
    : session?.role === "operator" ? "Nạp kỳ đối soát đầu tiên hoặc dùng dữ liệu mẫu." : "Chưa có kỳ đối soát được nạp.";
}

async function refresh() {
  overview = await api("/api/audit");
  const filter = byId("period");
  const chosen = filter.value;
  const all = new Option("Tất cả các kỳ", "");
  const periods = new Map(overview.runs.map((item) => [item.as_of + "|" + item.period, item]));
  filter.replaceChildren(all, ...Array.from(periods, ([key, item]) => new Option(item.period + " · " + item.as_of, key)));
  filter.value = periods.has(chosen) ? chosen : "";
  renderOverview();
}

function money(amount, currency) {
  return amount === null || amount === undefined ? "—" : number.format(amount / 100) + " " + currency;
}

function snapshot(title, record) {
  const section = node("section", undefined, "snapshot");
  section.append(node("h3", title));
  if (!record) {
    section.append(node("p", "Chưa tái kiểm thử"));
    return section;
  }
  const actual = Object.entries(record.actual).map(([currency, amount]) => money(amount, currency)).join(" + ");
  section.append(
    node("p", "Kỳ vọng · " + money(record.expected, record.currency)),
    node("p", "Chi trả · " + (actual || "Chưa ghi nhận")),
    node("p", record.flags.map((flag) => flags[flag]).join(", ") || "Đạt tiêu chí đối soát", "note"),
  );
  return section;
}

async function readDataset(file, fixture) {
  if (fixture) return api("/samples/audit/" + fixture + ".json");
  if (!file || !file.size) throw new Error("Chọn một tệp JSON.");
  if (file.size > 950_000) throw new Error("Tệp nguồn vượt 950 KB.");
  try {
    return JSON.parse(await file.text());
  } catch {
    throw new Error("Tệp nguồn không phải JSON hợp lệ.");
  }
}

function actionForm(item, action) {
  const form = node("form", undefined, "case-action");
  const isReview = action === "retest";
  const note = node("textarea");
  note.name = "note";
  note.rows = 3;
  note.required = true;
  note.minLength = 5;
  note.maxLength = 1000;
  const noteLabel = node("label", isReview ? "Căn cứ soát xét" : "Mô tả xử lý và tham chiếu chứng từ");
  noteLabel.append(note);
  const message = node("p", undefined, "message");
  message.setAttribute("role", "alert");
  form.append(node("h3", isReview ? "Tái kiểm thử" : "Gửi khắc phục"), noteLabel);
  if (isReview) {
    const fileLabel = node("label", "Tệp chi trả đối chiếu lại");
    const file = node("input");
    file.type = "file";
    file.name = "dataset";
    file.accept = ".json,application/json";
    fileLabel.append(file);
    form.append(fileLabel);
  }
  const buttons = node("div", undefined, "actions");
  const primary = node("button", isReview ? "Chạy lại và ghi kết quả" : "Gửi soát xét", "primary");
  primary.value = "upload";
  buttons.append(primary);
  if (isReview) {
    const sample = node("button", "Dùng mẫu tái kiểm thử");
    sample.value = "sample";
    sample.hidden = item.period !== "DEMO-2026-11-K1";
    buttons.append(sample);
  }
  form.append(buttons, message);
  form.onsubmit = async (event) => {
    event.preventDefault();
    const {submitter} = event;
    for (const button of buttons.children) button.disabled = true;
    try {
      const request = {note: note.value};
      if (isReview) {
        request.dataset = await readDataset(form.elements.dataset.files[0], submitter.value === "sample" ? "retest" : null);
      }
      const updated = await api("/api/audit/cases/" + item.id + "/" + action, request);
      renderCase(updated);
      await refresh();
      announce("Hồ sơ " + item.reference + " · " + states[updated.status].toLowerCase() + ".", true);
    } catch (error) {
      announce(error.message, false, message);
    } finally {
      for (const button of buttons.children) button.disabled = false;
    }
  };
  return form;
}

function renderCase(item) {
  currentCase = item.id;
  byId("case-title").textContent = item.reference;
  byId("case-subtitle").textContent = item.channel + " · " + item.account + " · " + item.period;
  const comparison = node("div", undefined, "comparison");
  comparison.append(snapshot("Kỳ đối soát gốc", item.baseline), snapshot("Lần tái kiểm thử gần nhất", item.latest));
  const history = node("ol", undefined, "history");
  for (const event of item.events) {
    const row = node("li");
    row.append(node("strong", actionNames[event.action] + " · " + event.actor));
    row.append(node("p", timestamp.format(new Date(event.recorded_at)) + " · Mã chạy " + event.run_id, "note"));
    row.append(node("p", event.note));
    history.append(row);
  }
  const body = byId("case-body");
  body.replaceChildren(node("span", states[item.status], "badge " + item.status), comparison,
    node("h3", "Lịch sử xử lý"), history);
  if (session.role === "operator" && item.status === "open") body.append(actionForm(item, "submit"));
  if (session.role === "reviewer" && item.status === "in_review") body.append(actionForm(item, "retest"));
}

async function loadCase(id) {
  renderCase(await api("/api/audit/cases/" + id));
}

byId("login").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  const message = form.querySelector(".message");
  announce("", false, message);
  try {
    await api("/api/login", Object.fromEntries(new FormData(form)));
    show(await api("/api/session"));
    form.reset();
    await refresh();
  } catch (error) {
    announce(error.message, false, session ? byId("notice") : message);
  } finally {
    button.disabled = false;
  }
};

byId("logout").onclick = async () => {
  try {
    await api("/api/logout", {});
    show(null);
  } catch (error) {
    announce(error.message);
  }
};

byId("period").onchange = renderOverview;
byId("state-filter").onchange = renderOverview;
byId("show-intake").onclick = () => {
  byId("intake-form").reset();
  announce("", false, byId("intake-form").querySelector(".message"));
  byId("intake").showModal();
};
for (const button of document.querySelectorAll("[data-close]")) {
  button.onclick = () => byId(button.dataset.close).close();
}

byId("intake-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const buttons = Array.from(form.querySelectorAll("button[type=submit]"));
  for (const button of buttons) button.disabled = true;
  try {
    const dataset = await readDataset(form.elements.dataset.files[0], event.submitter.value === "sample" ? "baseline" : null);
    const result = await api("/api/audit/intake", dataset);
    await refresh();
    byId("intake").close();
    const description = result.findings + " ngoại lệ · " + result.pending + " đơn chưa đến hạn.";
    announce((result.created ? "Đã nạp kỳ đối soát. " : "Kỳ đã được nạp trước đó. ") + description, true);
  } catch (error) {
    announce(error.message, false, form.querySelector(".message"));
  } finally {
    for (const button of buttons) button.disabled = false;
  }
};

byId("evidence").onclick = async () => {
  try {
    const data = await api("/api/audit/cases/" + currentCase + "/evidence");
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
    const anchor = node("a");
    anchor.href = url;
    anchor.download = "meridian-ho-so-" + currentCase + ".json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    announce(error.message);
  }
};

async function start() {
  try {
    show(await api("/api/session"));
    await refresh();
  } catch (error) {
    if (session) announce(error.message);
    else {
      show(null);
      if (error.status !== 401) announce(error.message, false, byId("login").querySelector(".message"));
    }
  }
}

start();
