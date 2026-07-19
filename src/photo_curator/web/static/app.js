"use strict";

document.documentElement.classList.add("js-ready");

const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
  const response = await fetch(url, { ...options, headers });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
  return data;
}

const newProjectForm = document.querySelector("#new-project-form");
newProjectForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = newProjectForm.querySelector(".form-status");
  const form = new FormData(newProjectForm);
  try {
    status.textContent = "Создаём проект…";
    const result = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({
        name: form.get("name") || null,
        album_id: form.get("album_id"),
        selection_density: form.get("selection_density"),
        source_provenance: form.get("source_provenance"),
      }),
    });
    window.location.assign(result.url);
  } catch (error) {
    status.textContent = error.message;
  }
});

document.querySelector('[data-action="pipeline-start"], [data-action="pipeline-resume"]')?.addEventListener("click", async (event) => {
  const root = document.querySelector("[data-project-id]");
  const button = event.currentTarget;
  const status = document.querySelector("[data-pipeline-status]");
  const action = button.dataset.action === "pipeline-resume" ? "resume" : "start";
  button.disabled = true;
  if (status) status.textContent = "Запускаем анализ…";
  try {
    await api(`/api/projects/${root.dataset.projectId}/pipeline/${action}`, { method: "POST" });
    if (status) status.textContent = action === "resume" ? "Анализ продолжается" : "Анализ запущен";
    pollProject(root, button, true);
  } catch (error) {
    button.disabled = false;
    if (status) status.textContent = `Не удалось запустить анализ: ${error.message}`;
  }
});

const projectRoot = document.querySelector("[data-project-state][data-project-id]");
if (projectRoot?.dataset.projectState === "running") pollProject(projectRoot);

async function pollProject(root, startButton = null, reloadOnTerminal = false) {
  let previousState = root.dataset.projectState;
  while (true) {
    await new Promise((resolve) => window.setTimeout(resolve, 900));
    let payload;
    try { payload = await api(`/api/projects/${root.dataset.projectId}`); }
    catch (error) {
      document.querySelector("[data-live-status]").textContent = `Связь потеряна: ${error.message}`;
      continue;
    }
    const state = payload.project.state;
    root.dataset.projectState = state;
    document.querySelector("[data-project-state-label]").textContent = state;
    const latest = new Map(payload.jobs.map((job) => [job.stage, job]));
    payload.stages.forEach((stage) => {
      const row = document.querySelector(`[data-stage="${stage.code}"]`);
      if (!row) return;
      row.className = `stage ${stage.status}`;
      const statusLabels = { pending: "Ожидает", running: "В работе", done: "Готово", warning: "Внимание", error: "Ошибка", interrupted: "Прервано" };
      row.querySelector(".stage-number").textContent = stage.status === "done" ? "✓" : stage.number;
      row.querySelector(".stage-status").textContent = statusLabels[stage.status] || stage.status_label;
      row.querySelector("[data-stage-message]").textContent = stage.message;
      row.querySelector("[data-stage-count]").textContent = `${stage.processed} / ${stage.total}`;
      row.querySelector(".progress").setAttribute("aria-valuenow", String(stage.progress));
      row.querySelector(".progress span").style.width = `${stage.progress}%`;
    });
    const active = [...latest.values()].find((job) => job.status === "running");
    const activeRow = active ? document.querySelector(`[data-stage="${["duplicates", "vision", "decisions"].includes(active.stage) ? "metrics" : active.stage}"]`) : null;
    if (active) {
      document.querySelector("[data-active-stage]").textContent = activeRow?.querySelector("h3")?.textContent || active.stage;
      document.querySelector("[data-active-stage-message]").textContent = active.current_message || "В работе";
      document.querySelector("[data-active-stage-count]").textContent = `${active.processed_items} / ${active.total_items}`;
      document.querySelector("[data-active-stage-health]").textContent = `⚠ ${active.warning_count || 0} · ✕ ${active.error_count || 0}`;
      document.querySelector("[data-active-stage-rate]").textContent = jobRateLabel(active);
    }
    document.querySelector("[data-live-status]").textContent = active?.current_message || `Статус: ${state}`;
    if (["ready", "error", "interrupted"].includes(state)) {
      if (startButton) startButton.disabled = false;
      if (reloadOnTerminal || previousState !== state) window.location.reload();
      return;
    }
    previousState = state;
  }
}

function jobRateLabel(job) {
  const started = Date.parse(job.started_at || "");
  const finished = Date.parse(job.finished_at || "") || Date.now();
  if (!Number.isFinite(started)) return "— · —";
  const elapsed = Math.max(0, (finished - started) / 1000);
  const duration = elapsed < 60 ? `${elapsed.toFixed(1)} с` : `${Math.floor(elapsed / 60)} мин ${Math.round(elapsed % 60)} с`;
  const throughput = elapsed > 0 && job.processed_items ? `${(job.processed_items / elapsed).toFixed(1)}/с` : "—";
  return `${duration} · ${throughput}`;
}

document.querySelectorAll("[data-retry-stage]").forEach((button) => {
  button.addEventListener("click", async () => {
    const root = document.querySelector("[data-project-id]");
    await api(`/api/projects/${root.dataset.projectId}/stages/${button.dataset.retryStage}/retry`, { method: "POST" });
    window.location.reload();
  });
});

document.querySelector('[data-action="cache-clean"]')?.addEventListener("click", async () => {
  const root = document.querySelector("[data-project-id]");
  await api(`/api/projects/${root.dataset.projectId}/cache/clean`, { method: "POST" });
  window.location.reload();
});

document.querySelector('[data-action="retry-missing"]')?.addEventListener("click", async (event) => {
  const root = document.querySelector("[data-project-id]");
  const button = event.currentTarget;
  const status = document.querySelector("[data-pipeline-status]");
  button.disabled = true;
  try {
    await api(`/api/projects/${root.dataset.projectId}/retry-missing`, { method: "POST" });
    if (status) status.textContent = "Повторяем недоступные preview…";
    pollProject(root, button, true);
  } catch (error) {
    button.disabled = false;
    if (status) status.textContent = `Не удалось повторить preview: ${error.message}`;
  }
});

const gallery = document.querySelector(".gallery");
if (gallery) {
  let lastCheckbox = null;
  let previewCard = null;
  const selected = () => [...gallery.querySelectorAll('.asset-card input[type="checkbox"]:checked')]
    .map((checkbox) => checkbox.closest(".asset-card"));
  const updateSelected = () => {
    document.querySelector("#selected-count").textContent = String(selected().length);
  };
  gallery.addEventListener("change", updateSelected);

  async function setDecision(card, disposition, note = null) {
    const value = disposition === "clear" ? null : disposition;
    await api(`/api/projects/${gallery.dataset.projectId}/assets/${card.dataset.assetUuid}/decision`, {
      method: "PATCH",
      body: JSON.stringify({ disposition: value, note }),
    });
    card.dataset.disposition = value || "pending";
    card.className = `asset-card disposition-${value || "pending"}`;
    const labels = { keep: "Отобрано", review: "Проверить", reject: "Исключено" };
    card.querySelector(".decision-badge").textContent = labels[value] || "Авто";
  }

  gallery.addEventListener("click", async (event) => {
    const checkbox = event.target.closest('input[type="checkbox"]');
    if (checkbox) {
      if (event.shiftKey && lastCheckbox) {
        const boxes = [...gallery.querySelectorAll('.asset-card input[type="checkbox"]')];
        const [start, end] = [boxes.indexOf(lastCheckbox), boxes.indexOf(checkbox)].sort((a, b) => a - b);
        boxes.slice(start, end + 1).forEach((box) => { box.checked = checkbox.checked; });
      }
      lastCheckbox = checkbox;
      updateSelected();
    }
    const decisionButton = event.target.closest("[data-decision]");
    if (decisionButton) await setDecision(decisionButton.closest(".asset-card"), decisionButton.dataset.decision);
    const imageButton = event.target.closest("[data-preview-url]");
    if (imageButton) {
      const dialog = document.querySelector("#preview-dialog");
      previewCard = imageButton.closest(".asset-card");
      dialog.querySelector("img").src = imageButton.dataset.previewUrl;
      dialog.querySelector(".preview-evidence").textContent =
        `Оценка ${previewCard.dataset.quality}/100 · ${previewCard.dataset.components} · ${previewCard.dataset.reasons || "без дополнительных предупреждений"}`;
      dialog.querySelector("textarea").value = previewCard.dataset.note || "";
      dialog.showModal();
    }
  });

  document.querySelectorAll("[data-batch-decision]").forEach((button) => {
    button.addEventListener("click", async () => {
      const cards = selected();
      if (!cards.length) return;
      const disposition = button.dataset.batchDecision === "clear" ? null : button.dataset.batchDecision;
      await api(`/api/projects/${gallery.dataset.projectId}/assets/batch-decision`, {
        method: "PATCH",
        body: JSON.stringify({ asset_uuids: cards.map((card) => card.dataset.assetUuid), disposition }),
      });
      cards.forEach((card) => {
        card.dataset.disposition = disposition || "pending";
        card.className = `asset-card disposition-${disposition || "pending"}`;
        const labels = { keep: "Отобрано", review: "Проверить", reject: "Исключено" };
        card.querySelector(".decision-badge").textContent = labels[disposition] || "Авто";
      });
    });
  });

  document.querySelector("[data-save-note]")?.addEventListener("click", async () => {
    if (!previewCard) return;
    const note = document.querySelector("#preview-dialog textarea").value;
    const disposition = previewCard.dataset.disposition || "review";
    await setDecision(previewCard, disposition, note);
    previewCard.dataset.note = note;
  });

  document.addEventListener("keydown", async (event) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) || event.target.isContentEditable) return;
    if (event.key === "Escape") document.querySelector("#preview-dialog")?.close();
    const cards = [...gallery.querySelectorAll(".asset-card")];
    const focused = document.activeElement.closest?.(".asset-card");
    if (["ArrowLeft", "ArrowRight"].includes(event.key) && focused) {
      event.preventDefault();
      const offset = event.key === "ArrowLeft" ? -1 : 1;
      cards[Math.max(0, Math.min(cards.length - 1, cards.indexOf(focused) + offset))]?.focus();
      return;
    }
    if (event.key === " " && focused) {
      event.preventDefault();
      focused.querySelector("[data-preview-url]")?.click();
      return;
    }
    const map = { k: "keep", r: "review", x: "reject", c: "clear" };
    const disposition = map[event.key.toLowerCase()];
    if (!disposition) return;
    const target = selected()[0] || document.activeElement.closest?.(".asset-card");
    if (target) await setDecision(target, disposition);
  });
}

document.querySelector(".dialog-close")?.addEventListener("click", () => document.querySelector("#preview-dialog").close());

const duplicateList = document.querySelector(".duplicate-list");
duplicateList?.addEventListener("click", async (event) => {
  const decision = event.target.closest("[data-duplicate-decision]");
  if (decision) {
    const value = decision.dataset.duplicateDecision === "clear" ? null : decision.dataset.duplicateDecision;
    await api(`/api/projects/${duplicateList.dataset.projectId}/assets/${decision.dataset.assetUuid}/decision`, {
      method: "PATCH",
      body: JSON.stringify({ disposition: value }),
    });
    window.location.reload();
    return;
  }
  const button = event.target.closest("[data-make-leader]");
  if (!button) return;
  const group = button.closest("[data-group-id]");
  await api(`/api/projects/${duplicateList.dataset.projectId}/duplicate-groups/${group.dataset.groupId}/leader`, {
    method: "PATCH",
    body: JSON.stringify({ asset_uuid: button.dataset.makeLeader }),
  });
  window.location.reload();
});

document.querySelector('[data-action="publish-dry-run"]')?.addEventListener("click", async () => {
  const root = document.querySelector("[data-project-id]");
  const suffix = root.dataset.publishKind === "best" ? "/best" : "";
  await api(`/api/projects/${root.dataset.projectId}/publish${suffix}/dry-run`, { method: "POST" });
  window.location.reload();
});

document.querySelector('[data-action="export-selected"]')?.addEventListener("click", async (event) => {
  const root = document.querySelector("[data-project-id]");
  event.currentTarget.disabled = true;
  try {
    const result = await api(`/api/projects/${root.dataset.projectId}/export/selected`, { method: "POST" });
    window.location.assign(result.url);
  } finally {
    event.currentTarget.disabled = false;
  }
});

document.querySelector('[data-action="publish-apply"]')?.addEventListener("click", async (event) => {
  const root = document.querySelector("[data-project-id]");
  const confirmed = document.querySelector("#publish-confirm")?.checked || false;
  const result = await api(`/api/projects/${root.dataset.projectId}/publish/apply`, {
    method: "POST",
    body: JSON.stringify({ publish_id: event.currentTarget.dataset.publishId, confirmed }),
  });
  if (result.status === "applied") window.location.assign("photos://");
  else window.location.reload();
});

const sharedCopyForm = document.querySelector("#shared-copy-form");
sharedCopyForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const root = document.querySelector("[data-shared-album-id]");
  const status = sharedCopyForm.querySelector(".form-status");
  const submitter = event.submitter;
  const mode = submitter?.dataset.copyMode;
  const form = new FormData(sharedCopyForm);
  const selected = [...document.querySelectorAll('[data-shared-selection] input[type="checkbox"]:checked')]
    .map((checkbox) => checkbox.value);
  submitter.disabled = true;
  status.textContent = "Проверяем локальные renders…";
  try {
    const result = await api(`/api/shared/${root.dataset.sharedAlbumId}/copies`, {
      method: "POST",
      body: JSON.stringify({
        mode,
        destination_album_name: form.get("destination_album_name") || null,
        sample_size: mode === "sample" ? Number(form.get("sample_size")) : null,
        asset_uuids: mode === "custom" ? selected : [],
      }),
    });
    window.location.assign(result.url);
  } catch (error) {
    submitter.disabled = false;
    status.textContent = error.message;
  }
});

document.querySelector('[data-action="shared-copy-apply"]')?.addEventListener("click", async (event) => {
  const root = document.querySelector("[data-shared-copy-job]");
  const confirmed = document.querySelector("#shared-copy-confirm")?.checked || false;
  if (!confirmed) {
    document.querySelector("[data-copy-message]").textContent = "Подтвердите создание обычного альбома Photos";
    return;
  }
  event.currentTarget.disabled = true;
  await api(`/api/shared-copies/${root.dataset.sharedCopyJob}/apply`, {
    method: "POST",
    body: JSON.stringify({ confirmed }),
  });
  pollSharedCopy(root.dataset.sharedCopyJob);
});

const sharedCopyRoot = document.querySelector("[data-shared-copy-job]");
if (sharedCopyRoot && ["queued", "running"].includes(document.querySelector("[data-copy-state]")?.textContent)) {
  pollSharedCopy(sharedCopyRoot.dataset.sharedCopyJob);
}

async function pollSharedCopy(jobId) {
  while (true) {
    await new Promise((resolve) => window.setTimeout(resolve, 900));
    const job = await api(`/api/shared-copies/${jobId}`);
    document.querySelector("[data-copy-state]").textContent = job.status;
    document.querySelector("[data-copy-message]").textContent = job.current_message;
    document.querySelector("[data-copy-count]").textContent = `${job.processed_items} / ${job.total_items}`;
    const progress = document.querySelector(".copy-status-panel .progress");
    progress.setAttribute("aria-valuenow", String(job.processed_items));
    progress.querySelector("span").style.width = `${job.total_items ? job.processed_items * 100 / job.total_items : 0}%`;
    if (["done", "error", "interrupted"].includes(job.status)) {
      window.location.reload();
      return;
    }
  }
}
