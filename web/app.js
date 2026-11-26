"use strict";
const $ = id => document.getElementById(id);
let csrf = "", documentId = "", sample = "", state = null, dirty = false, working = false, downloading = false, uiPending = false;
let activeTab = "local", pollTimer = null;
let aiStatus = null;
const labels = {local_only:"仅本地完成", completed:"流程完成 · 待复核", ai_incomplete:"AI 未完成", invalid_input:"输入无效"};
const editor = $("contract");

function notice(message, error = false) {
  $("alert").textContent = message;
  $("alert").classList.toggle("error", error);
  $("alert").hidden = !message;
}
async function api(path, options = {}) {
  const headers = {"X-CSRF-Token": csrf, ...(options.headers || {})};
  if (options.body && typeof options.body !== "string" && !(options.body instanceof Blob)) {
    options.body = JSON.stringify(options.body);
    headers["Content-Type"] = "application/json";
  }
  let response;
  try { response = await fetch(path, {...options, headers, credentials:"same-origin", cache:"no-store"}); }
  catch { throw new Error("无法连接本机服务，当前文字仍保留在页面。请确认“启动网页版”窗口仍在运行，再重试。"); }
  if (!response.ok) {
    const info = await response.json().catch(() => ({}));
    throw new Error(info.error || `本机请求未完成（${response.status}）。`);
  }
  return response;
}
const endpoint = action => `/api/documents/${documentId}${action ? "/" + action : ""}`;
const asJson = async (path, options) => (await api(path, options)).json();
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function empty(container, title, text) {
  const box = element("div", undefined, "empty");
  box.append(element("div", "≡", "empty-icon"), element("h3", title), element("p", text));
  container.replaceChildren(box);
}
function tab(name, focus = false) {
  activeTab = name;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const selected = button.dataset.tab === name;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $("view-" + button.dataset.tab).hidden = !selected;
    if (focus && selected) button.focus();
  }
}
function buttons() {
  const ready = Boolean(state) && !working && !uiPending;
  const confirmed = ready && !dirty && state.confirmed;
  $("confirm").disabled = !ready || !editor.value.trim() || (state.source?.status === "partial" && !$("partial").checked);
  $("review").disabled = !confirmed;
  $("import").disabled = !ready;
  $("sample").disabled = !ready;
  $("ai-refresh").disabled = !ready;
  $("ai-test").disabled = !ready || !aiStatus?.configured || !aiStatus?.sdk_available;
  editor.readOnly = working || uiPending;
  for (const id of ["ai", "anonymized", "partial"]) $(id).disabled = working || uiPending || !state;
  for (const button of document.querySelectorAll("[data-export]")) button.disabled = !ready || dirty || !state.snapshot || downloading;
  $("char-count").textContent = `${Array.from(editor.value).length.toLocaleString()} 字符`;
  $("input-status").textContent = working ? "处理中" : confirmed ? "已确认" : editor.value.trim() ? "待确认" : "待输入";
  $("input-status").className = "pill" + (confirmed ? " active" : "");
  $("confirm-note").textContent = confirmed ? "本次文本已确认，可以开始审查。" : "修改文本或审查设置后，需要重新确认和审查。";
  const status = !dirty && state?.snapshot?.result.status;
  $("review-status").textContent = working ? "后台处理中" : status ? labels[status] || status : "等待审查";
  $("review-status").className = "pill" + (status === "ai_incomplete" ? " warning" : status ? " active" : "");
  for (const [name, done, current] of [
    ["input",!!editor.value.trim(),!editor.value.trim()],
    ["confirm",!!confirmed,!!editor.value.trim() && !confirmed],
    ["review",!!status,!!confirmed && !status],
    ["export",false,!!status]]) {
    $("step-" + name).className = done ? "done" : current ? "current" : "";
  }
}
function sourceView() {
  const box = $("view-source");
  const source = state?.source;
  $("source-strip").hidden = !state?.filename;
  $("partial-row").hidden = source?.status !== "partial";
  if (state?.filename) {
    $("source-name").textContent = state.filename;
    $("source-status").textContent = source ? `${source.page_count} 页 · ${source.status === "complete" ? "全部提取，待人工核对" : "提取不完整"}` : "TXT 导入 · 待人工核对";
  }
  if (!source && !state?.filename) {
    empty(box, "当前为粘贴输入", "图片或 PDF 导入后，这里保留逐页初始文本与识别状态。校正不会覆盖初始记录。");
    return;
  }
  box.replaceChildren();
  const info = element("div", undefined, "source-details");
  info.append(element("p", "来源文件：" + state.filename));
  const download = element("button", "下载来源原件以便对照 ↗", "text-button");
  download.addEventListener("click", () => downloadSource().catch(showError));
  info.append(download);
  if (source) {
    info.append(element("p", `页数：${source.page_count}；失败或空白页：${source.failed_pages.join("、") || "无"}`),
      element("p", "来源文件 SHA-256：" + source.file_sha256),
      element("p", "导入时间：" + source.imported_at));
  }
  box.append(info, element("p", "请在本机查看器对照原件核对。初始提取文本独立保留；引用匹配通过不代表 OCR 与原件一致。", "inline-note"));
  if (source) for (const page of source.pages) {
    const details = element("details", undefined, "source-page");
    details.open = page.page_number === 1 || page.status !== "ok";
    details.append(element("summary", `第 ${page.page_number} 页 · ${page.method === "ocr" ? "本地 OCR" : "PDF 文本层"} · ${page.status === "ok" ? "已提取" : page.status === "empty" ? "空识别" : "失败"}`),
      element("p", page.message, "inline-note"), element("pre", page.initial_text || "（无可用文本，须补充）"));
    box.append(details);
  }
}
function resultView() {
  const snapshot = dirty ? null : state?.snapshot;
  const result = snapshot?.result;
  for (const [id, value] of [["local-count",result?.local_findings.length],["ai-count",result?.ai_findings.length],
    ["unmatched-count",result?.ai_findings.reduce((sum,item) => sum + item.unverified_evidence.length,0)]]) $(id).textContent = value ?? "—";
  $("report-meta").textContent = snapshot ? `${snapshot.report_id} · ${snapshot.reviewed_at} · SHA-256 ${snapshot.contract_sha256}` : "暂无有效审查快照 · 三种格式将使用同一报告编号与文本";
  if (!result) {
    empty($("view-local"), dirty ? "输入已变化，需重新审查" : "从确认原文开始", "完成审查后，这里展示本地待核查项。每一条提示都需要结合合同与材料复核。");
    empty($("view-ai"), "尚无本次 AI 意见", "AI 默认关闭。启用后沿用已有后端配置，未配置或未完成时会保留本地提示。");
    empty($("view-messages"), "尚无本次审查记录", "导入后先核对并确认文本，再开始审查。");
    return;
  }
  const local = $("view-local"); local.replaceChildren();
  local.append(element("p", "本地规则提示须结合担保类型和完整材料判断；空结果不表示合同安全。", "inline-note"));
  if (!result.local_findings.length) local.append(element("p", "本地规则没有列出待核查项，仍须人工复核。", "inline-note"));
  result.local_findings.forEach((item, index) => {
    const article = element("article", undefined, "finding");
    const heading = element("h3"); heading.append(element("span", String(index + 1).padStart(2,"0"), "index"), document.createTextNode(item.risk_name));
    article.append(heading, element("p", item.risk_message)); local.append(article);
  });
  const ai = $("view-ai"); ai.replaceChildren();
  ai.append(element("p", "等级、问题和理由由 AI 生成，须律师复核。三类引用仅核验文字与确认文本的关系。", "inline-note"));
  if (!result.ai_findings.length) ai.append(element("p", "本次没有可展示的 AI 意见，请查看流程消息。", "inline-note"));
  result.ai_findings.forEach((item, index) => {
    const article = element("article", undefined, "finding");
    const heading = element("h3"); heading.append(element("span", `${({high:"高",medium:"中",low:"低"})[item.risk_level]}风险`, "level " + item.risk_level), document.createTextNode(`${index+1}. ${item.issue}`));
    article.append(heading, element("p", "理由：" + item.reason));
    for (const [key, title, type] of [["verified_evidence","原文精确匹配","exact"],["whitespace_matched_evidence","空白差异匹配 · 须核对上下文","whitespace"],["unverified_evidence","未匹配 · 须重点核对","unmatched"]]) {
      const group = element("div", undefined, "evidence " + type); group.append(element("h4", title));
      for (const quote of item[key] || []) group.append(element("blockquote", quote));
      if (!(item[key] || []).length) group.append(element("p", "（无）"));
      article.append(group);
    }
    ai.append(article);
  });
  const messages = $("view-messages"); messages.replaceChildren();
  messages.append(element("p", `${labels[result.status]}（${result.status}）`, "inline-note"));
  for (const text of [...result.messages, ...snapshot.notices]) messages.append(element("div", text, "message-item"));
}
function applyState(data, replaceEditor = true) {
  state = data; dirty = false;
  if (replaceEditor) editor.value = data.text;
  $("ai").checked = data.use_ai; $("anonymized").checked = data.anonymized; $("partial").checked = data.partial_ack;
  sourceView(); resultView(); buttons();
}
function changed() {
  dirty = true;
  resultView(); buttons();
}
async function sync(clearSource = false) {
  if (!dirty && !clearSource) return;
  const data = await asJson(endpoint(), {method:"POST",body:{revision:state.revision,text:editor.value,
    use_ai:$("ai").checked,anonymized:$("anonymized").checked,partial_ack:$("partial").checked,clear_source:clearSource}});
  applyState(data, false);
}
async function confirmed() {
  await sync();
  applyState(await asJson(endpoint("confirm"), {method:"POST",body:{revision:state.revision}}));
  notice("已确认本次文本。请点击“开始审查”；识别文字仍须与原件核对。");
}
function progress(job) {
  $("job-progress").hidden = false;
  $("job-message").textContent = job.message;
  $("cancel").hidden = job.kind !== "import";
  if (job.total) { $("progress").value = job.done / job.total * 100; }
  else $("progress").removeAttribute("value");
}
function showError(error) { notice(error.message || "请求未完成，请检查本机服务是否仍在运行。", true); }
async function action(work) {
  if (working || uiPending) return;
  uiPending = true; buttons();
  try { await work(); } catch (error) { showError(error); }
  finally { uiPending = false; buttons(); }
}
async function poll() {
  try {
    const data = await asJson(endpoint("job"));
    const job = data.job;
    if (job?.state === "running") { progress(job); pollTimer = setTimeout(poll,450); return; }
    working = false; $("job-progress").hidden = true;
    applyState(await asJson(endpoint()));
    if (job?.kind === "ai_test") await refreshAi(false);
    notice(job?.message || "任务已结束。", job?.state === "failed");
    if (job?.kind === "review" && job.state === "done") tab("local");
    if (job?.kind === "import" && job.state === "done" && state.source?.status === "partial") tab("source");
  } catch (error) {
    // Keep the editor locked while the server job may still be running. Retry allows
    // recovery after a temporary connection failure without duplicate review calls.
    showError(error); pollTimer = setTimeout(poll,2500);
  }
}
async function refreshAi(syncDocument = true) {
  aiStatus = await asJson("/api/ai/status");
  $("ai-provider").textContent = `DeepSeek · ${aiStatus.model} · ${aiStatus.configured ? "已配置" : "未配置"}`;
  $("ai-config-message").textContent = aiStatus.last_test?.message || aiStatus.message;
  if (syncDocument && documentId) {
    const remote = await asJson(endpoint());
    if (!dirty) applyState(remote);
    else state.revision = remote.revision;
  }
  buttons();
}
async function beginAiTest() {
  await sync();
  const job = await asJson(endpoint("ai-test"), {method:"POST",body:{revision:state.revision}});
  working = true; buttons(); progress(job); notice(""); pollTimer = setTimeout(poll,200);
}
async function beginReview() {
  if (dirty) throw new Error("文本已修改，请重新确认。");
  const job = await asJson(endpoint("review"),{method:"POST",body:{revision:state.revision}});
  state.snapshot = null; working = true; resultView(); buttons(); progress(job); notice(""); pollTimer = setTimeout(poll,200);
}
async function importFile(file) {
  if (!file) return;
  if (!/\.(txt|png|jpe?g|pdf)$/i.test(file.name)) throw new Error("请选择 TXT、PNG、JPG/JPEG 或 PDF 文件。");
  if (!file.size || file.size > 20 * 1024 * 1024) throw new Error("请选择非空且不超过20 MB的文件。");
  // Save pending edits before upload. A failed import keeps that text and snapshot.
  await sync();
  // Detect an expired session before transferring a potentially large PDF body.
  const current = await asJson(endpoint());
  if (current.revision !== state.revision || current.job?.state === "running") throw new Error("记录已变化或仍在处理，请刷新同步后再导入。");
  working = true; buttons(); notice("正在将文件交给本机后台，稍后显示逐页进度。");
  try {
    const job = await asJson(endpoint("import"),{method:"POST",headers:{"Content-Type":"application/octet-stream","X-Filename":encodeURIComponent(file.name),"X-Revision":String(state.revision)},body:file});
    progress(job); pollTimer = setTimeout(poll,200);
  } catch (error) { working = false; buttons(); throw error; }
}
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob), link = element("a");
  link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url),60000);
}
async function downloadReport(format) {
  if (dirty || !state?.snapshot || working) throw new Error("没有有效报告，请重新确认并审查。");
  downloading = true; buttons();
  const revision = state.revision, reportId = state.snapshot.report_id;
  try {
    const response = await api(endpoint(format));
    const blob = await response.blob();
    if (dirty || state.revision !== revision || state.snapshot?.report_id !== reportId) throw new Error("生成期间输入已变化，请重新审查后导出。");
    saveBlob(blob, reportId + (format === "word" ? ".docx" : "." + format));
    notice("报告已交给浏览器下载。请在下载列表确认保存；取消或保存失败不会清除当前审查结果。");
  } finally { downloading = false; buttons(); }
}
async function downloadSource() {
  const response = await api(endpoint("source"));
  saveBlob(await response.blob(), state.filename);
}
async function loadSample() {
  editor.value = sample; $("partial").checked = false; changed();
  await sync(true); notice("已载入虚构样本，请核对并确认后审查。未调用 AI。");
}
editor.addEventListener("input",changed);
for (const id of ["ai","anonymized","partial"]) $(id).addEventListener("change",changed);
$("confirm").addEventListener("click",() => action(confirmed));
$("review").addEventListener("click",() => action(beginReview));
$("sample").addEventListener("click",() => action(loadSample));
$("ai-refresh").addEventListener("click",() => action(() => refreshAi()));
$("ai-test").addEventListener("click",() => action(beginAiTest));
$("import").addEventListener("click",() => $("file").click());
$("file").addEventListener("change",() => { const file = $("file").files[0]; $("file").value = ""; action(() => importFile(file)); });
$("show-source").addEventListener("click",() => tab("source"));
$("cancel").addEventListener("click",async () => {
  try { const data = await asJson(endpoint("cancel"),{method:"POST",body:{}}); notice(data.message); } catch (error) { showError(error); }
});
for (const button of document.querySelectorAll("[data-export]")) button.addEventListener("click",() => downloadReport(button.dataset.export).catch(showError));
const tabButtons = Array.from(document.querySelectorAll("[data-tab]"));
tabButtons.forEach((button,index) => {
  button.addEventListener("click",() => tab(button.dataset.tab));
  button.addEventListener("keydown",event => {
    let next = null;
    if (event.key === "ArrowRight") next = (index+1)%tabButtons.length;
    if (event.key === "ArrowLeft") next = (index+tabButtons.length-1)%tabButtons.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabButtons.length-1;
    if (next !== null) { event.preventDefault(); tab(tabButtons[next].dataset.tab,true); }
  });
});
window.addEventListener("beforeunload",event => {
  if (editor.value.trim() || working) { event.preventDefault(); event.returnValue = ""; }
});
async function initialize() {
  try {
    const session = await asJson("/api/session"); csrf = session.csrf; sample = session.sample;
    let existingId = "";
    try { existingId = sessionStorage.getItem("reviewDocument") || ""; } catch {}
    let data = null;
    if (/^[a-f0-9]{32}$/.test(existingId)) {
      documentId = existingId;
      try { data = await asJson(endpoint()); } catch {}
    }
    if (!data) {
      data = await asJson("/api/documents",{method:"POST",body:{}}); documentId = data.id;
      try { sessionStorage.setItem("reviewDocument",documentId); } catch {}
    }
    applyState(data);
    await refreshAi(false);
    if (data.job?.state === "running") {
      working = true; buttons(); progress(data.job); pollTimer = setTimeout(poll,200);
    }
  } catch (error) { showError(error); }
}
initialize();
