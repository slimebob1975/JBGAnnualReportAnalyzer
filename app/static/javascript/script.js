"use strict";

// The forms submit via fetch, then poll the job endpoint until it finishes.
// The server does the work in a background thread, so neither the browser nor
// a reverse proxy has to hold a request open for minutes.

const API = {
    analysis: { start: "/api/analyze" },
    masking: { start: "/api/mask" },
};

const POLL_INTERVAL_MS = 2000;

function showTab(tabId) {
    document.querySelectorAll(".tab-content").forEach(tab => {
        tab.classList.toggle("active", tab.id === tabId);
    });
    document.querySelectorAll(".tab-button").forEach(button => {
        const selected = button.dataset.tab === tabId;
        button.classList.toggle("active", selected);
        button.setAttribute("aria-selected", selected ? "true" : "false");
    });
}

/**
 * Start a download without navigating away.
 *
 * The download route sends Content-Disposition: attachment, so a hidden iframe
 * is enough and, unlike assigning window.location, it cannot replace the page
 * if the server ever answers with something other than a file.
 */
function startDownload(url) {
    const frame = document.createElement("iframe");
    frame.style.display = "none";
    frame.src = url;
    document.body.appendChild(frame);
    setTimeout(() => frame.remove(), 120000);
}

function formatDuration(seconds) {
    const total = Math.max(0, Math.round(seconds));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

class StatusPanel {
    constructor(root) {
        this.root = root;
        this.message = root.querySelector("[data-role=message]");
        this.elapsed = root.querySelector("[data-role=elapsed]");
        this.timer = null;
    }

    busy(text) {
        const startedAt = Date.now();
        this.root.dataset.state = "busy";
        this.root.hidden = false;
        this.message.textContent = text;
        this.elapsed.hidden = false;
        this.elapsed.textContent = "0:00";
        clearInterval(this.timer);
        this.timer = setInterval(() => {
            this.elapsed.textContent = formatDuration((Date.now() - startedAt) / 1000);
        }, 1000);
    }

    update(text) {
        this.message.textContent = text;
    }

    stop() {
        clearInterval(this.timer);
        this.timer = null;
        this.elapsed.hidden = true;
    }

    done(text) {
        this.stop();
        this.root.dataset.state = "done";
        this.message.textContent = text;
    }

    failed(text) {
        this.stop();
        this.root.dataset.state = "error";
        this.message.textContent = text;
    }
}

async function readJson(response) {
    try {
        return await response.json();
    } catch (err) {
        throw new Error(`Servern svarade med status ${response.status}.`);
    }
}

async function pollJob(jobId, panel) {
    while (true) {
        await sleep(POLL_INTERVAL_MS);

        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
        const job = await readJson(response);

        if (response.status === 404) {
            throw new Error(job.detail || "Jobbet finns inte längre.");
        }
        if (!response.ok) {
            throw new Error(job.message || job.detail || `Fel (status ${response.status}).`);
        }
        if (job.status === "error") {
            throw new Error(job.message || job.error || "Analysen misslyckades.");
        }
        if (job.status === "done") {
            return job;
        }

        panel.update(job.message || "Arbetar...");
    }
}

async function submitForm(form, panel, busyText) {
    const endpoint = API[form.dataset.kind].start;
    const submitButton = form.querySelector("button[type=submit]");

    panel.busy(busyText);
    if (submitButton) submitButton.disabled = true;

    try {
        const response = await fetch(endpoint, { method: "POST", body: new FormData(form) });
        const started = await readJson(response);

        if (!response.ok || !started.ok) {
            throw new Error(
                started.message || started.detail || `Fel (status ${response.status}).`
            );
        }

        const job = await pollJob(started.job_id, panel);
        panel.done(job.message);
        if (job.download_url) startDownload(job.download_url);
    } catch (err) {
        panel.failed(err.message || String(err));
    } finally {
        if (submitButton) submitButton.disabled = false;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const savedKey = localStorage.getItem("openai_api_key");
    const apikeyField = document.getElementById("apikey");
    if (savedKey && apikeyField) apikeyField.value = savedKey;

    document.querySelectorAll(".tab-button").forEach(button => {
        button.addEventListener("click", () => showTab(button.dataset.tab));
    });

    document.querySelectorAll("form[data-kind]").forEach(form => {
        const panel = new StatusPanel(document.getElementById(form.dataset.status));
        const busyText = form.dataset.busy || "Arbetar...";

        form.addEventListener("submit", event => {
            event.preventDefault();
            const keyField = form.querySelector("#apikey");
            if (keyField) localStorage.setItem("openai_api_key", keyField.value.trim());
            submitForm(form, panel, busyText);
        });
    });

    showTab(document.body.dataset.activeTab || "analysis");
});
