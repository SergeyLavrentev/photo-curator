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
      body: JSON.stringify({ name: form.get("name"), album_id: form.get("album_id") }),
    });
    window.location.assign(result.url);
  } catch (error) {
    status.textContent = error.message;
  }
});

document.querySelector('[data-action="pipeline-start"]')?.addEventListener("click", async (event) => {
  const root = document.querySelector("[data-project-id]");
  const button = event.currentTarget;
  const status = document.querySelector("[data-pipeline-status]");
  button.disabled = true;
  if (status) status.textContent = "Запускаем анализ…";
  try {
    await api(`/api/projects/${root.dataset.projectId}/pipeline/start`, { method: "POST" });
    sessionStorage.setItem("photo-curator-running", `${root.dataset.projectId}:0`);
    window.location.reload();
  } catch (error) {
    button.disabled = false;
    if (status) status.textContent = `Не удалось запустить анализ: ${error.message}`;
  }
});

const projectRoot = document.querySelector("[data-project-state][data-project-id]");
if (projectRoot) {
  const tracked = sessionStorage.getItem("photo-curator-running") || "";
  const [trackedProject, trackedAttempts = "0"] = tracked.split(":");
  const attempts = Number(trackedAttempts);
  if (projectRoot.dataset.projectState === "running" || trackedProject === projectRoot.dataset.projectId) {
    if (projectRoot.dataset.projectState === "error" || (projectRoot.dataset.projectState === "ready" && attempts >= 2)) {
      sessionStorage.removeItem("photo-curator-running");
    } else {
      sessionStorage.setItem("photo-curator-running", `${projectRoot.dataset.projectId}:${attempts + 1}`);
      window.setTimeout(() => window.location.reload(), 1000);
    }
  }
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
    card.querySelector(".decision-badge").textContent = value || "pending";
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
        `${previewCard.dataset.reasons || "Нет дополнительных причин"} · quality ${previewCard.dataset.quality} · sharpness ${previewCard.dataset.sharpness}`;
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
        card.querySelector(".decision-badge").textContent = disposition || "pending";
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
  await api(`/api/projects/${root.dataset.projectId}/publish/dry-run`, { method: "POST" });
  window.location.reload();
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
