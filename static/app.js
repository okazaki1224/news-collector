const API = "/api";

function formatDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

let toastTimer;
function showToast(message, type = "success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.className = "toast hidden";
  }, 3500);
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `エラー: ${res.status}`);
  }
  return data;
}

async function loadStatus() {
  const status = await api("/status");
  document.getElementById("schedulerStatus").textContent = status.scheduler_running
    ? "稼働中（1時間ごと）"
    : "停止中";
  document.getElementById("schedulerStatus").className =
    "status-value" + (status.scheduler_running ? " active" : "");
  document.getElementById("nextRun").textContent = formatDate(status.next_run);
  document.getElementById("keywordCount").textContent = status.keyword_count;
  document.getElementById("articleCount").textContent = status.article_count;
}

async function loadKeywords() {
  const keywords = await api("/keywords");
  const list = document.getElementById("keywordList");
  const filter = document.getElementById("filterKeyword");

  filter.innerHTML = '<option value="">すべてのキーワード</option>';
  keywords.forEach((kw) => {
    const opt = document.createElement("option");
    opt.value = kw.id;
    opt.textContent = kw.text;
    filter.appendChild(opt);
  });

  if (keywords.length === 0) {
    list.innerHTML = '<li class="empty">キーワードが登録されていません</li>';
    return;
  }

  list.innerHTML = keywords
    .map(
      (kw) => `
    <li class="keyword-item" data-id="${kw.id}">
      <div class="keyword-info">
        <div class="keyword-text">${escapeHtml(kw.text)}</div>
        <div class="keyword-meta">${kw.article_count} 件の記事 · 登録: ${formatDate(kw.created_at)}</div>
      </div>
      <div class="keyword-actions">
        <label class="toggle" title="自動収集のON/OFF">
          <input type="checkbox" ${kw.enabled ? "checked" : ""} onchange="toggleKeyword(${kw.id}, this.checked)">
          <span class="toggle-slider"></span>
        </label>
        <button class="btn btn-secondary btn-sm" onclick="collectKeyword(${kw.id})">収集</button>
        <button class="btn btn-danger btn-sm" onclick="deleteKeyword(${kw.id})">削除</button>
      </div>
    </li>`
    )
    .join("");
}

async function loadArticles() {
  const keywordId = document.getElementById("filterKeyword").value;
  const source = document.getElementById("filterSource").value;
  const params = new URLSearchParams({ limit: "50" });
  if (keywordId) params.set("keyword_id", keywordId);
  if (source) params.set("source", source);

  const articles = await api(`/articles?${params}`);
  const container = document.getElementById("articlesList");

  if (articles.length === 0) {
    container.innerHTML =
      '<p class="empty">記事がありません。キーワードを登録して収集を開始してください。</p>';
    return;
  }

  container.innerHTML = articles
    .map(
      (a) => `
    <article class="article-card">
      <h3 class="article-title">
        <a href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(a.title)}</a>
      </h3>
      <div class="article-meta">
        <span class="badge badge-source">${escapeHtml(a.source)}</span>
        <span class="badge badge-keyword">${escapeHtml(a.keyword_text)}</span>
        <span>公開: ${formatDate(a.published_at)}</span>
        <span>収集: ${formatDate(a.collected_at)}</span>
      </div>
      ${a.summary ? `<p class="article-summary">${escapeHtml(stripHtml(a.summary))}</p>` : ""}
    </article>`
    )
    .join("");
}

async function loadLogs() {
  const logs = await api("/logs?limit=10");
  const container = document.getElementById("logsList");

  if (logs.length === 0) {
    container.innerHTML = '<p class="empty">ログがありません</p>';
    return;
  }

  container.innerHTML = logs
    .map(
      (log) => `
    <div class="log-item">
      <span class="log-status ${log.status}">${log.status}</span>
      <span>${formatDate(log.started_at)}</span>
      <span>検出: ${log.articles_found}件</span>
      <span>新規: ${log.articles_new}件</span>
      <span>${escapeHtml(log.message || "")}</span>
    </div>`
    )
    .join("");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function stripHtml(html) {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div.textContent || "";
}

async function refreshAll() {
  await Promise.all([loadStatus(), loadKeywords(), loadArticles(), loadLogs()]);
}

document.getElementById("keywordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("keywordInput");
  const text = input.value.trim();
  if (!text) return;

  try {
    await api("/keywords", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    input.value = "";
    showToast(`「${text}」を登録しました`);
    await refreshAll();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("searchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("searchInput");
  const text = input.value.trim();
  if (!text) return;

  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const result = await api("/search", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    showToast(`${result.articles_found}件検出、${result.articles_new}件を新規保存`);
    await refreshAll();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("collectAllBtn").addEventListener("click", async () => {
  const btn = document.getElementById("collectAllBtn");
  btn.disabled = true;
  btn.textContent = "収集中...";
  try {
    const result = await api("/collect", { method: "POST" });
    showToast(
      `${result.keywords_processed}キーワード処理: ${result.articles_found}件検出、${result.articles_new}件新規`
    );
    await refreshAll();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "今すぐ全キーワード収集";
  }
});

document.getElementById("filterKeyword").addEventListener("change", loadArticles);
document.getElementById("filterSource").addEventListener("change", loadArticles);
document.getElementById("refreshArticlesBtn").addEventListener("click", refreshAll);

window.toggleKeyword = async (id, enabled) => {
  try {
    await api(`/keywords/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
    showToast(enabled ? "自動収集を有効にしました" : "自動収集を無効にしました");
  } catch (err) {
    showToast(err.message, "error");
    await loadKeywords();
  }
};

window.collectKeyword = async (id) => {
  try {
    const result = await api(`/collect/${id}`, { method: "POST" });
    showToast(`${result.articles_found}件検出、${result.articles_new}件新規保存`);
    await refreshAll();
  } catch (err) {
    showToast(err.message, "error");
  }
};

window.deleteKeyword = async (id) => {
  if (!confirm("このキーワードを削除しますか？関連する記事も削除されます。")) return;
  try {
    await api(`/keywords/${id}`, { method: "DELETE" });
    showToast("キーワードを削除しました");
    await refreshAll();
  } catch (err) {
    showToast(err.message, "error");
  }
};

refreshAll();
setInterval(loadStatus, 60000);
