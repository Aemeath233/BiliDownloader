"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const titles = {
    parse: "添加下载",
    queue: "下载队列",
    settings: "设置",
    logs: "运行日志",
  };
  const PAGE_SIZE = 30,
    EPISODE_SIZE = 12;
  const state = {
    page: "parse",
    ready: false,
    config: null,
    media: null,
    selected: new Set(),
    episodePage: 0,
    activeEpisode: 0,
    qualities: [],
    qn: null,
    downloadMode: "combined",
    authVersion: 0,
    parseGeneration: 0,
    parsing: false,
    qualityGeneration: 0,
    qualityWanted: null,
    qualityRunning: false,
    adding: false,
    choosingDirectory: false,
    previewGeneration: 0,
    scrollPositions: {},
    tasks: [],
    selectedTasks: new Set(),
    taskPage: 0,
    filter: "all",
    logs: [],
    logCursor: 0,
    settingsDirty: false,
    pollRunning: false,
    qrGeneration: 0,
  };
  let pollTimer, toastTimer, previewTimer, qualityTimer, qrTimer;
  const taskRows = new Map();
  const text = (node, value) => {
    const next = String(value ?? "");
    if (node.textContent !== next) node.textContent = next;
  };
  const element = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = value;
    return node;
  };
  const notify = (message, error = false) => {
    text($("toast"), message);
    $("toast").classList.toggle("error", error);
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => ($("toast").hidden = true), 4400);
  };
  const call = async (command, payload = {}) => {
    if (!window.pywebview?.api) throw new Error("桌面连接尚未就绪，请稍候");
    const result = await window.pywebview.api.invoke(command, payload);
    if (!result.ok) throw new Error(result.error || "操作失败，请查看运行日志");
    return result.data;
  };
  const listen = (id, event, handler) =>
    $(id).addEventListener(event, async (e) => {
      try {
        await handler(e);
      } catch (error) {
        notify(error.message, true);
      }
    });
  const busy = async (button, action) => {
    if (button.disabled) return;
    button.disabled = true;
    button.classList.add("busy");
    try {
      return await action();
    } finally {
      button.disabled = false;
      button.classList.remove("busy");
    }
  };
  const confirmAction = (title, body, label = "确认") =>
    new Promise((resolve) => {
      const dialog = $("confirm-dialog");
      if (dialog.open) {
        resolve(false);
        return;
      }
      text($("confirm-title"), title);
      text($("confirm-body"), body);
      text($("confirm-ok"), label);
      dialog.returnValue = "cancel";
      $("confirm-ok").onclick = () => dialog.close("ok");
      $("confirm-cancel").onclick = () => dialog.close("cancel");
      dialog.addEventListener(
        "close",
        () => resolve(dialog.returnValue === "ok"),
        { once: true },
      );
      dialog.showModal();
    });
  function showPage(page) {
    if (!titles[page] || state.page === page) return;
    state.scrollPositions[state.page] = $(`page-${state.page}`).scrollTop;
    document.querySelectorAll(".page").forEach((node) => {
      const active = node.id === `page-${page}`;
      node.hidden = !active;
      node.classList.toggle("active", active);
    });
    document.querySelectorAll(".nav-button").forEach((node) => {
      const active = node.dataset.page === page;
      node.classList.toggle("active", active);
      if (active) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
    state.page = page;
    text($("page-title"), titles[page]);
    $(`page-${page}`).scrollTop = state.scrollPositions[page] || 0;
    $("header-add").hidden = page === "parse";
    if (page === "queue") {
      renderQueue();
      refresh();
    }
    if (page === "logs") renderLogs();
    if (page === "parse" && !state.media) $("link-input").focus();
  }
  document
    .querySelectorAll("[data-page]")
    .forEach((button) =>
      button.addEventListener("click", () => showPage(button.dataset.page)),
    );
  listen("header-add", "click", () => $("clear-input").click());
  function theme(config) {
    const mode =
      config.appearance_mode === "system"
        ? matchMedia("(prefers-color-scheme:dark)").matches
          ? "dark"
          : "light"
        : config.appearance_mode;
    document.documentElement.dataset.theme = mode;
    document.documentElement.dataset.motion = config.animations_enabled
      ? "on"
      : "off";
  }
  matchMedia("(prefers-color-scheme:dark)").addEventListener(
    "change",
    () => state.config && theme(state.config),
  );
  function fillSettings(config) {
    for (const [key, value] of Object.entries(config)) {
      const field = $("settings-form").elements.namedItem(key);
      if (field) {
        if (field.type === "checkbox") field.checked = value;
        else field.value = value;
      }
    }
  }
  async function refresh() {
    if (state.pollRunning || !window.pywebview?.api) return;
    clearTimeout(pollTimer);
    state.pollRunning = true;
    try {
      const next = await call("state", {
        log_cursor: state.logCursor,
        include_tasks: state.page === "queue",
      });
      state.logCursor = next.log_cursor;
      if (next.logs.length) {
        state.logs.push(...next.logs);
        state.logs = state.logs.slice(-1000);
        if (state.page === "logs") renderLogs();
      }
      if (next.startup_error) {
        $("startup-error").hidden = false;
        text(
          $("startup-error"),
          `启动失败：${next.startup_error}。请关闭后重试。`,
        );
      }
      if (!next.ready) return;
      const first = !state.ready;
      state.ready = true;
      if (first && document.activeElement === document.body)
        $("link-input").focus();
      if (first) await call("ui_ready");
      state.config = next.config;
      if (first)
        setDownloadMode(next.config.default_download_mode || "combined", false);
      theme(next.config);
      if (first || !state.settingsDirty) fillSettings(next.config);
      $("parse-button").disabled = state.parsing || state.adding;
      $("account-button").disabled = false;
      text($("account-button"), next.logged_in ? "账号设置" : "扫码登录");
      const m = next.metrics;
      text(
        $("global-status"),
        m.active_count
          ? `${speed(m.total_speed_bps)} · ${m.active_count} 个下载中`
          : m.queued_count
            ? `${m.queued_count} 个任务排队中`
            : "就绪",
      );
      const pending = m.active_count + m.queued_count;
      $("queue-badge").hidden = !pending;
      text($("queue-badge"), pending);
      text(
        $("account-status"),
        next.logged_in
          ? "已保存登录凭证，可检查当前登录状态"
          : "尚未登录，部分画质可能不可用",
      );
      $("logout").hidden = !next.logged_in;
      text(
        $("ffmpeg-status"),
        next.ffmpeg_available
          ? "FFmpeg 可用"
          : "未找到 FFmpeg，请选择 ffmpeg.exe",
      );
      if (next.auth_version !== state.authVersion) {
        state.authVersion = next.auth_version;
        if (state.media) requestQualities();
      }
      if (next.tasks) {
        state.tasks = next.tasks;
        const live = new Set(next.tasks.map((task) => task.task_id));
        for (const id of state.selectedTasks)
          if (!live.has(id)) state.selectedTasks.delete(id);
        if (state.page === "queue") renderQueue();
      }
      if (
        next.persistence_error &&
        next.persistence_error !== state.lastPersistenceError
      )
        notify("任务记录保存失败，下载继续；请检查磁盘空间与权限", true);
      state.lastPersistenceError = next.persistence_error;
      if (!state.media || !state.previewDirectory) {
        text($("destination-button"), next.config.save_path);
        $("destination-button").title =
          `${next.config.save_path}\n更改本次下载目录`;
      }
    } catch (error) {
      text($("global-status"), error.message);
    } finally {
      state.pollRunning = false;
      pollTimer = setTimeout(refresh, document.hidden ? 3000 : 1000);
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  window.addEventListener("pywebviewready", refresh);
  setTimeout(refresh, 150);
  setTimeout(() => {
    if (!state.ready && !window.pywebview?.api) {
      $("startup-error").hidden = false;
      text($("startup-error"), "桌面连接未建立，请关闭程序后重新打开。");
    }
  }, 15000);

  function clearMedia() {
    state.media = null;
    state.previewDirectory = null;
    state.qualities = [];
    state.qn = null;
    state.selected.clear();
    state.qualityGeneration++;
    document.body.classList.remove("has-media", "single-media");
    state.qualityWanted = null;
    $("media-result").hidden = true;
    $("parse-empty").hidden = false;
    text($("parse-empty-title"), "复制链接，粘贴到上方");
    text($("parse-empty-detail"), "支持 B 站视频、番剧、合集和分享文本");
    updateAddButton();
  }
  async function parseLink() {
    if (!state.ready || state.parsing || state.adding) return;
    const content = $("link-input").value.trim();
    if (!content) {
      $("link-input").focus();
      return;
    }
    const generation = ++state.parseGeneration;
    clearMedia();
    state.parsing = true;
    $("parse-form").setAttribute("aria-busy", "true");
    $("parse-button").disabled = true;
    $("parse-button").classList.add("busy");
    text($("parse-button"), "解析中");
    text($("parse-empty-title"), "正在读取视频信息…");
    text($("parse-empty-detail"), "解析完成后即可选择下载内容和画质");
    text($("parse-feedback"), "正在解析链接…");
    $("parse-feedback").classList.remove("error-text");
    try {
      const media = await call("parse", { text: content });
      if (
        generation !== state.parseGeneration ||
        content !== $("link-input").value.trim()
      )
        return;
      acceptMedia(media);
      text($("parse-feedback"), "已解析，可更换链接继续添加");
    } catch (error) {
      if (generation === state.parseGeneration) {
        text($("parse-feedback"), error.message);
        $("parse-feedback").classList.add("error-text");
        text($("parse-empty-title"), "链接暂时没有解析成功");
        text($("parse-empty-detail"), "检查上方提示后重试，或换一个链接");
      }
    } finally {
      state.parsing = false;
      if (generation !== state.parseGeneration) {
        if (!state.media) {
          text($("parse-empty-title"), "复制链接，粘贴到上方");
          text($("parse-empty-detail"), "支持 B 站视频、番剧、合集和分享文本");
        }
        text(
          $("parse-feedback"),
          $("link-input").value.trim()
            ? "链接已更改，请重新解析"
            : "粘贴后自动解析，也可以按 Enter 解析",
        );
      }
      $("parse-button").disabled = false;
      $("parse-button").classList.remove("busy");
      text($("parse-button"), "解析链接");
      $("parse-form").setAttribute("aria-busy", "false");
    }
  }
  function acceptMedia(media) {
    setDownloadMode(
      media.download_mode || state.config?.default_download_mode || "combined",
      false,
    );
    state.media = media;
    state.episodePage = Math.floor(
      (media.default_episode_index || 0) / EPISODE_SIZE,
    );
    document.body.classList.add("has-media");
    const single = media.episodes.length === 1;
    document.body.classList.toggle("single-media", single);
    $("episode-panel").hidden = single;
    state.activeEpisode = media.default_episode_index || 0;
    state.selected = new Set([state.activeEpisode]);
    if (state.downloadMode === "audio")
      state.preferredAudioQn = media.preferred_qn ?? state.preferredAudioQn;
    else state.preferredQn = media.preferred_qn ?? state.preferredQn;
    $("media-result").hidden = false;
    $("parse-empty").hidden = true;
    text($("media-title"), media.title);
    $("media-title").title = media.title;
    text(
      $("media-kind"),
      media.media_type === "bangumi"
        ? "番剧 / 合集"
        : media.source_name || "Bilibili 视频",
    );
    text(
      $("episode-total"),
      single ? "单个视频" : `${media.episodes.length} 个分集`,
    );
    renderEpisodes(true);
    requestQualities();
  }
  listen("parse-form", "submit", (e) => {
    e.preventDefault();
    return parseLink();
  });
  listen("clear-input", "click", () => {
    $("link-input").value = "";
    state.parseGeneration++;
    clearMedia();
    text($("parse-feedback"), "粘贴后自动解析，也可以按 Enter 解析");
    $("parse-feedback").classList.remove("error-text");
    $("link-input").focus();
  });
  listen("link-input", "input", () => {
    state.parseGeneration++;
    if (state.media) clearMedia();
  });
  listen("link-input", "paste", () => setTimeout(() => parseLink(), 120));
  listen("link-input", "keydown", (event) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.ctrlKey &&
      !event.isComposing
    ) {
      event.preventDefault();
      return parseLink();
    }
  });
  function renderEpisodes(resetScroll = false) {
    if (!state.media) return;
    const scrollTop = resetScroll ? 0 : $("episode-list").scrollTop;
    const episodes = state.media.episodes,
      start = state.episodePage * EPISODE_SIZE;
    const fragment = document.createDocumentFragment();
    episodes.slice(start, start + EPISODE_SIZE).forEach((episode, offset) => {
      const index = start + offset,
        row = element(
          "div",
          `episode-row${index === state.activeEpisode ? " current" : ""}`,
        );
      const check = element("input");
      check.type = "checkbox";
      check.id = `episode-${index}`;
      check.checked = state.selected.has(index);
      check.disabled = state.adding;
      const title = element("label", "episode-title", episode.title);
      title.htmlFor = check.id;
      check.addEventListener("change", () => {
        if (check.checked) state.selected.add(index);
        else state.selected.delete(index);
        text($("selected-count"), `已选 ${state.selected.size}`);
        updateAddButton();
        schedulePreview();
        if (check.checked && state.selected.size === 1) {
          state.activeEpisode = index;
          renderEpisodes();
          requestQualities();
        }
      });
      const inspect = element(
        "button",
        "episode-quality",
        index === state.activeEpisode
          ? state.downloadMode === "audio"
            ? "当前音质"
            : "当前画质"
          : state.downloadMode === "audio"
            ? "查看音质"
            : "查看画质",
      );
      inspect.setAttribute(
        "aria-pressed",
        String(index === state.activeEpisode),
      );
      inspect.disabled = state.adding;
      inspect.addEventListener("click", () => {
        state.activeEpisode = index;
        renderEpisodes();
        requestQualities();
      });
      row.append(
        check,
        element("span", "episode-number", String(index + 1).padStart(2, "0")),
        title,
        inspect,
      );
      fragment.append(row);
    });
    $("episode-list").replaceChildren(fragment);
    $("episode-list").scrollTop = scrollTop;
    text($("selected-count"), `已选 ${state.selected.size}`);
    const pages = Math.max(1, Math.ceil(episodes.length / EPISODE_SIZE));
    $("episode-pagination").hidden = pages === 1;
    text($("episode-page-info"), `${state.episodePage + 1} / ${pages}`);
    $("episodes-prev").disabled = state.adding || !state.episodePage;
    $("episodes-next").disabled =
      state.adding || state.episodePage >= pages - 1;
  }
  for (const [id, action] of [
    ["select-all", "all"],
    ["select-invert", "invert"],
    ["select-none", "none"],
  ])
    listen(id, "click", () => {
      if (!state.media || state.adding) return;
      state.media.episodes.forEach((_, index) => {
        if (
          action === "all" ||
          (action === "invert" && !state.selected.has(index))
        )
          state.selected.add(index);
        else state.selected.delete(index);
      });
      renderEpisodes();
      updateAddButton();
      schedulePreview();
    });
  for (const [id, step] of [
    ["episodes-prev", -1],
    ["episodes-next", 1],
  ])
    listen(id, "click", () => {
      state.episodePage += step;
      renderEpisodes(true);
    });
  const modeLabels = {
    combined: "视频＋音频",
    audio: "仅音频",
    video: "仅视频（无声）",
  };
  function setDownloadMode(mode, reload = true) {
    state.downloadMode = modeLabels[mode] ? mode : "combined";
    document.querySelectorAll("[data-download-mode]").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.downloadMode === state.downloadMode),
      );
    });
    text(
      $("quality-label"),
      state.downloadMode === "audio" ? "音频品质" : "视频画质",
    );
    text($("refresh-quality"), "刷新");
    text(
      $("download-result-note"),
      {
        combined: "有声视频 · MP4",
        audio: "M4A 音频 · 保留源编码",
        video: "无声视频 · MP4",
      }[state.downloadMode],
    );
    if (reload && state.media) {
      renderEpisodes();
      requestQualities();
    }
  }
  document.querySelectorAll("[data-download-mode]").forEach((button) =>
    button.addEventListener("click", () => {
      if (!state.adding && button.dataset.downloadMode !== state.downloadMode)
        setDownloadMode(button.dataset.downloadMode);
    }),
  );
  function requestQualities(refresh = false) {
    if (!state.media) return;
    const generation = ++state.qualityGeneration;
    state.qualityWanted = {
      media_id: state.media.id,
      index: state.activeEpisode,
      refresh,
      generation,
      download_mode: state.downloadMode,
    };
    state.qn = null;
    state.qualities = [];
    $("quality-select").replaceChildren(new Option("正在读取品质…", ""));
    $("quality-select").disabled = true;
    text(
      $("quality-note"),
      state.downloadMode === "audio"
        ? "正在读取可下载音轨…"
        : "正在确认当前分集的可下载画质…",
    );
    schedulePreview();
    updateAddButton();
    clearTimeout(qualityTimer);
    qualityTimer = setTimeout(drainQualities, 120);
  }
  async function drainQualities() {
    if (state.qualityRunning) return;
    state.qualityRunning = true;
    try {
      while (state.qualityWanted) {
        const request = state.qualityWanted;
        state.qualityWanted = null;
        try {
          const result = await call("qualities", request);
          if (
            request.generation !== state.qualityGeneration ||
            result.auth_version !== state.authVersion
          )
            continue;
          state.qualities = result.options;
          $("quality-select").replaceChildren(
            ...result.options.map(
              (option) => new Option(option.label, option.qn),
            ),
          );
          if (!result.options.length)
            throw new Error("当前分集没有可下载画质，可登录后刷新");
          const preferred =
            result.options.find(
              (option) =>
                option.qn ===
                (state.downloadMode === "audio"
                  ? state.preferredAudioQn
                  : state.preferredQn),
            ) || result.options[0];
          state.qn = preferred.qn;
          $("quality-select").value = preferred.qn;
          $("quality-select").disabled = false;
          text(
            $("quality-note"),
            preferred.availability_note ||
              (state.media.episodes.length > 1
                ? `最高可下载 ${result.options[0].label}，其余分集逐一校验`
                : `最高可下载 ${result.options[0].label}`),
          );
          schedulePreview();
          updateAddButton();
        } catch (error) {
          if (request.generation === state.qualityGeneration) {
            text($("quality-note"), error.message);
            $("quality-select").replaceChildren(new Option("画质读取失败", ""));
          }
        }
      }
    } finally {
      state.qualityRunning = false;
    }
  }
  const selectionPayload = () => ({
    media_id: state.media?.id,
    indices: [...state.selected].sort((a, b) => a - b),
    quality_index: state.activeEpisode,
    qn: state.qn,
    download_mode: state.downloadMode,
  });
  function updateAddButton() {
    $("link-input").disabled = state.adding;
    $("clear-input").disabled = state.adding;
    $("parse-button").disabled = !state.ready || state.parsing || state.adding;
    $("destination-button").disabled = state.adding || state.choosingDirectory;
    $("change-destination").disabled = state.adding || state.choosingDirectory;
    $("quality-select").disabled = state.adding || !state.qn;
    $("refresh-quality").disabled = state.adding;
    for (const id of ["select-all", "select-invert", "select-none"])
      $(id).disabled = state.adding;
    document
      .querySelectorAll("[data-download-mode]")
      .forEach((button) => (button.disabled = state.adding));
    $("add-button").disabled =
      !state.media ||
      !state.qn ||
      !state.selected.size ||
      state.adding ||
      state.choosingDirectory;
    text(
      $("add-button"),
      state.adding
        ? "正在加入队列…"
        : state.selected.size
          ? state.media?.episodes.length === 1
            ? "开始下载"
            : `下载 ${state.selected.size} 个分集`
          : "请选择分集",
    );
  }
  function schedulePreview() {
    clearTimeout(previewTimer);
    const generation = ++state.previewGeneration;
    if (!state.media || !state.qn || !state.selected.size) {
      text($("filename-preview"), "选择分集和画质后显示");
      return;
    }
    const payload = selectionPayload(),
      key = JSON.stringify(payload);
    previewTimer = setTimeout(async () => {
      try {
        const result = await call("preview", payload);
        if (
          generation !== state.previewGeneration ||
          key !== JSON.stringify(selectionPayload())
        )
          return;
        text(
          $("filename-preview"),
          result.filename +
            (result.count > 1 ? ` 等 ${result.count} 个文件` : ""),
        );
        text($("destination-button"), result.directory);
        $("destination-button").title = `${result.directory}\n更改本次下载目录`;
        state.previewDirectory = result.directory;
      } catch (error) {
        if (
          generation === state.previewGeneration &&
          key === JSON.stringify(selectionPayload())
        )
          text($("filename-preview"), error.message);
      }
    }, 120);
  }
  listen("refresh-quality", "click", () => requestQualities(true));
  listen("quality-select", "change", () => {
    state.qn = Number($("quality-select").value);
    if (state.downloadMode === "audio") state.preferredAudioQn = state.qn;
    else state.preferredQn = state.qn;
    updateAddButton();
    schedulePreview();
  });
  async function chooseDestination() {
    if (!state.media || state.adding || state.choosingDirectory) return;
    const mediaId = state.media.id;
    state.choosingDirectory = true;
    state.previewGeneration++;
    $("change-destination").classList.add("busy");
    updateAddButton();
    try {
      const directory = await call("choose_destination", { media_id: mediaId });
      if (directory && state.media?.id === mediaId) {
        state.previewDirectory = directory;
        text($("destination-button"), directory);
        $("destination-button").title = `${directory}\n更改本次下载目录`;
        schedulePreview();
      }
    } finally {
      state.choosingDirectory = false;
      $("change-destination").classList.remove("busy");
      updateAddButton();
    }
  }
  listen("destination-button", "click", chooseDestination);
  listen("change-destination", "click", chooseDestination);
  listen("add-button", "click", async () => {
    if (state.adding) return;
    const payload = selectionPayload();
    state.adding = true;
    updateAddButton();
    renderEpisodes();
    try {
      let result = await call("add", payload);
      if (result.duplicates) {
        if (
          !(await confirmAction(
            "已有相同的下载任务",
            `已有 ${result.duplicates} 个相同分集、相同品质和下载内容的任务。是否再下载一份？`,
            "再下载一份",
          ))
        )
          return;
        result = await call("add", { ...payload, allow_duplicates: true });
      }
      notify(`已加入 ${result.created} 个下载任务`);
      state.taskPage = 0;
      $("queue-search").value = "";
      setQueueFilter("all");
      showPage("queue");
      $("page-queue").scrollTop = 0;
      await refresh();
    } finally {
      state.adding = false;
      updateAddButton();
      renderEpisodes();
    }
  });

  const statusLabels = {
    queued: "排队中",
    preparing: "准备中",
    downloading: "下载中",
    merging: "合并中",
    completed: "已完成",
    failed: "失败",
    canceled: "已取消",
  };
  function bytes(value) {
    if (!value) return "0 B";
    const units = ["B", "KiB", "MiB", "GiB"];
    const i = Math.min(
      3,
      Math.floor(Math.log(Math.max(1, value)) / Math.log(1024)),
    );
    return `${(value / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
  }
  function speed(value) {
    return `${bytes(value)}/s`;
  }
  function visibleTasks() {
    const keyword = $("queue-search").value.trim().toLocaleLowerCase();
    return state.tasks.filter(
      (task) =>
        (state.filter === "all" ||
          (state.filter === "active"
            ? ["queued", "preparing", "downloading", "merging"].includes(
                task.status,
              )
            : state.filter === "failed"
              ? ["failed", "canceled"].includes(task.status)
              : task.status === state.filter)) &&
        (!keyword ||
          `${task.title} ${task.output_path}`
            .toLocaleLowerCase()
            .includes(keyword)),
    );
  }
  function createTaskRow(task) {
    const row = element("article", "task-row");
    // This template is static; all remote titles, paths and errors use textContent.
    row.innerHTML =
      '<input type="checkbox"><div class="task-main"><div class="task-title"></div><div class="task-meta"></div><div class="task-path"></div><div class="task-progress"><span></span></div><div class="task-stats"><span></span><span></span></div><div class="task-detail"></div></div><div class="task-side"><span class="task-status"></span><div class="task-actions" role="group" aria-label="任务操作"></div></div>';
    row.querySelector("input").addEventListener("change", (e) => {
      if (e.target.checked) state.selectedTasks.add(task.task_id);
      else state.selectedTasks.delete(task.task_id);
      updateBatch();
    });
    row._actions = new Map();
    return row;
  }
  const actionIcons = {
    directory:
      '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    play: '<path d="m9 5 10 7-10 7Z"/>',
    retry: '<path d="M3 10a9 9 0 1 1 2.6 8.4M3 4v6h6"/>',
    reselect:
      '<path d="M4 7h6m4 0h6M4 17h10m4 0h2"/><circle cx="12" cy="7" r="2"/><circle cx="16" cy="17" r="2"/>',
    cancel: '<path d="m6 6 12 12M6 18 18 6"/>',
    remove: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"/>',
  };
  function updateTaskRow(row, task) {
    const set = (selector, value) => text(row.querySelector(selector), value);
    const check = row.querySelector("input");
    check.checked = state.selectedTasks.has(task.task_id);
    row.classList.toggle("selected", check.checked);
    row.dataset.status = task.status;
    check.setAttribute("aria-label", `选择 ${task.title}`);
    set(".task-title", task.title);
    row.querySelector(".task-title").title = task.title;
    set(
      ".task-meta",
      `${task.download_mode_label || modeLabels[task.download_mode || "combined"]} · ${task.quality_label}${task.status === "completed" ? ` · ${bytes(task.downloaded_bytes || task.total_bytes)}` : ""}`,
    );
    set(".task-path", task.output_path);
    row.querySelector(".task-path").title = task.output_path;
    const percent = Math.min(
      100,
      Math.max(
        0,
        task.status === "completed" ? 100 : Number(task.percent) || 0,
      ),
    );
    const indeterminate =
      ["preparing", "merging"].includes(task.status) ||
      (task.status === "downloading" && !task.total_bytes);
    const progress = row.querySelector(".task-progress");
    progress.hidden = task.status === "completed";
    progress.classList.toggle("indeterminate", indeterminate);
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-label", `${task.title} 下载进度`);
    if (indeterminate) progress.removeAttribute("aria-valuenow");
    else progress.setAttribute("aria-valuenow", String(percent));
    row.querySelector(".task-progress span").style.transform = indeterminate
      ? ""
      : `scaleX(${percent / 100})`;
    set(
      ".task-stats span:first-child",
      task.status === "completed"
        ? "已保存到本地"
        : `${percent.toFixed(1)}% · ${task.message}`,
    );
    row.querySelector(".task-stats").hidden = task.status === "completed";
    set(
      ".task-stats span:last-child",
      task.status === "completed"
        ? bytes(task.downloaded_bytes || task.total_bytes)
        : `${bytes(task.downloaded_bytes)} / ${bytes(task.total_bytes)}${task.status === "downloading" ? ` · ${speed(task.speed_bps)}` : ""}`,
    );
    set(".task-detail", task.error || "");
    row.querySelector(".task-detail").classList.toggle("error", !!task.error);
    set(
      ".task-status",
      task.status === "merging" &&
        task.download_mode &&
        task.download_mode !== "combined"
        ? "保存中"
        : statusLabels[task.status] || task.status,
    );
    row.querySelector(".task-status").className = `task-status ${task.status}`;
    const actions = [
      ["play", "打开", task.status === "completed"],
      ["directory", task.status === "completed" ? "定位" : "目录", true],
      ["retry", "重试", task.can_retry],
      [
        "reselect",
        task.download_mode === "audio" ? "重选音质" : "重选画质",
        task.can_retry,
      ],
      ["cancel", task.cancel_requested ? "取消中" : "取消", task.can_cancel],
      ["remove", "移除", task.can_remove],
    ];
    actions.forEach(([action, label, show], index) => {
      let button = row._actions.get(action);
      if (!button && show) {
        button = element(
          "button",
          `task-action${action === "play" || action === "retry" ? " task-action-prominent" : ""}${action === "remove" ? " task-action-remove" : ""}`,
        );
        button.type = "button";
        button.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${actionIcons[action]}</svg><span class="action-label"></span>`;
        row._actions.set(action, button);
        row.querySelector(".task-actions").append(button);
        button.addEventListener("click", () =>
          busy(button, async () => {
            if (action === "directory" || action === "play")
              await call("open_location", {
                kind:
                  action === "directory" && row._task.status === "completed"
                    ? "reveal"
                    : action,
                task_id: task.task_id,
              });
            else if (action === "reselect") {
              const media = await call("reselect", { task_id: task.task_id });
              $("link-input").value = media.source_url;
              state.parseGeneration++;
              acceptMedia(media);
              showPage("parse");
            } else {
              await call("task_action", { action, ids: [task.task_id] });
              await refresh();
            }
          }).catch((error) => notify(error.message, true)),
        );
      }
      if (button) {
        button.style.order = index;
        button.hidden = !show;
        text(button.querySelector(".action-label"), label);
        button.title =
          action === "remove"
            ? "仅移除任务记录，保留下载文件"
            : action === "directory" && task.status === "completed"
              ? "在资源管理器中定位文件"
              : label;
        if (action === "cancel")
          button.disabled =
            !!task.cancel_requested || button.classList.contains("busy");
      }
    });
    row._task = task;
  }
  function renderQueue() {
    const filtered = visibleTasks(),
      pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    state.taskPage = Math.min(state.taskPage, pages - 1);
    const visible = filtered.slice(
        state.taskPage * PAGE_SIZE,
        (state.taskPage + 1) * PAGE_SIZE,
      ),
      ids = new Set(visible.map((task) => task.task_id));
    for (const [id, row] of taskRows)
      if (!ids.has(id)) {
        row.remove();
        taskRows.delete(id);
      }
    visible.forEach((task, index) => {
      let row = taskRows.get(task.task_id);
      if (!row) {
        row = createTaskRow(task);
        taskRows.set(task.task_id, row);
      }
      updateTaskRow(row, task);
      const current = $("task-list").children[index];
      if (current !== row) $("task-list").insertBefore(row, current || null);
    });
    text(
      $("queue-summary"),
      state.filter === "all" && !$("queue-search").value.trim()
        ? `共 ${state.tasks.length} 个任务`
        : `${filtered.length} 个结果 · 共 ${state.tasks.length} 个任务`,
    );
    $("batch-bar").hidden = !filtered.length && !state.selectedTasks.size;
    $("task-list").hidden = !filtered.length;
    $("task-pagination").hidden = pages === 1;
    $("queue-empty").hidden = !!filtered.length;
    text(
      $("queue-empty-title"),
      state.tasks.length ? "没有匹配的任务" : "这里还没有任务",
    );
    text(
      $("queue-empty-detail"),
      state.tasks.length
        ? "试试其他状态，或修改搜索内容。"
        : "添加一个视频，下载进度会显示在这里。",
    );
    text($("task-page-info"), `${state.taskPage + 1} / ${pages}`);
    $("tasks-prev").disabled = !state.taskPage;
    $("tasks-next").disabled = state.taskPage >= pages - 1;
    updateQueueActions();
    updateBatch();
  }
  function updateQueueActions() {
    const retryable = state.tasks.some((task) =>
      ["failed", "canceled"].includes(task.status),
    );
    const finished = state.tasks.some((task) =>
      ["completed", "failed", "canceled"].includes(task.status),
    );
    $("retry-all").disabled =
      !retryable || $("retry-all").classList.contains("busy");
    $("clear-finished").disabled =
      !finished || $("clear-finished").classList.contains("busy");
  }
  function updateBatch() {
    for (const [id, row] of taskRows)
      row.classList.toggle("selected", state.selectedTasks.has(id));
    text($("batch-count"), `已选 ${state.selectedTasks.size} 项`);
    $("batch-actions").hidden = !state.selectedTasks.size;
    const ids = [...taskRows.keys()],
      count = ids.filter((id) => state.selectedTasks.has(id)).length;
    $("select-page").checked = !!ids.length && count === ids.length;
    $("select-page").indeterminate = count > 0 && count < ids.length;
    $("select-page").disabled = !ids.length;
  }
  function setQueueFilter(filter) {
    state.filter = filter;
    state.taskPage = 0;
    document.querySelectorAll("[data-filter]").forEach((node) => {
      const active = node.dataset.filter === filter;
      node.classList.toggle("active", active);
      node.setAttribute("aria-pressed", String(active));
    });
    renderQueue();
    $("page-queue").scrollTop = 0;
  }
  document
    .querySelectorAll("[data-filter]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        setQueueFilter(button.dataset.filter),
      ),
    );
  listen("queue-search", "input", () => {
    state.taskPage = 0;
    renderQueue();
  });
  for (const [id, step] of [
    ["tasks-prev", -1],
    ["tasks-next", 1],
  ])
    listen(id, "click", () => {
      state.taskPage += step;
      renderQueue();
      $("page-queue").scrollTop = 0;
    });
  listen("select-page", "change", () => {
    for (const id of taskRows.keys()) {
      if ($("select-page").checked) state.selectedTasks.add(id);
      else state.selectedTasks.delete(id);
    }
    renderQueue();
  });
  listen("clear-task-selection", "click", () => {
    state.selectedTasks.clear();
    renderQueue();
  });
  document.querySelectorAll("[data-batch]").forEach((button) =>
    button.addEventListener("click", () =>
      busy(button, async () => {
        const result = await call("task_action", {
          action: button.dataset.batch,
          ids: [...state.selectedTasks],
        });
        notify(`已处理 ${result.count} 个任务`);
        await refresh();
      }).catch((error) => notify(error.message, true)),
    ),
  );
  listen("retry-all", "click", async () => {
    try {
      await busy($("retry-all"), async () => {
        const result = await call("task_action", { action: "retry_all" });
        notify(`已加入 ${result.count} 个重试任务，将从头下载`);
        await refresh();
      });
    } finally {
      updateQueueActions();
    }
  });
  listen("clear-finished", "click", async () => {
    try {
      await busy($("clear-finished"), async () => {
        if (
          await confirmAction(
            "清理已结束的任务？",
            "只移除任务记录，已下载的视频会保留。",
            "清理记录",
          )
        ) {
          const result = await call("task_action", {
            action: "clear_finished",
          });
          notify(`已清理 ${result.count} 条记录`);
          await refresh();
        }
      });
    } finally {
      updateQueueActions();
    }
  });

  listen("settings-form", "input", () => {
    state.settingsDirty = true;
    text($("settings-feedback"), "有未保存的更改");
  });
  listen("settings-form", "submit", (e) => {
    e.preventDefault();
    return busy($("save-settings"), async () => {
      const values = {};
      for (const field of $("settings-form").elements)
        if (field.name)
          values[field.name] =
            field.type === "checkbox"
              ? field.checked
              : field.type === "number"
                ? Number(field.value)
                : field.value;
      state.config = await call("save_settings", values);
      if (!state.media)
        setDownloadMode(
          state.config.default_download_mode || "combined",
          false,
        );
      state.settingsDirty = false;
      theme(state.config);
      fillSettings(state.config);
      text($("settings-feedback"), "设置已保存");
      notify("设置已保存");
      schedulePreview();
    });
  });
  for (const [id, command, field] of [
    ["pick-directory", "pick_directory", "save-path"],
    ["pick-ffmpeg", "pick_ffmpeg", "ffmpeg-path"],
  ])
    listen(id, "click", () =>
      busy($(id), async () => {
        const path = await call(command);
        if (path) {
          $(field).value = path;
          state.settingsDirty = true;
          text($("settings-feedback"), "有未保存的更改");
        }
      }),
    );
  for (const [id, kind] of [
    ["open-save", "save"],
    ["open-logs", "logs"],
  ])
    listen(id, "click", () => call("open_location", { kind }));
  listen("save-account", "click", () =>
    busy($("save-account"), async () => {
      await call("account", { sessdata: $("sessdata-input").value });
      $("sessdata-input").value = "";
      notify("登录凭证已更新");
      await refresh();
    }),
  );
  listen("logout", "click", async () => {
    if (
      await confirmAction(
        "退出登录？",
        "后续解析和下载将使用未登录状态。",
        "退出登录",
      )
    ) {
      await call("account", { sessdata: "" });
      await refresh();
    }
  });
  listen("check-account", "click", () =>
    busy($("check-account"), async () => {
      const result = await call("check_account");
      notify(result.logged_in ? "登录状态有效" : "当前未登录");
      await refresh();
    }),
  );
  async function startQr() {
    clearTimeout(qrTimer);
    const generation = ++state.qrGeneration;
    $("qr-image").hidden = true;
    $("qr-loading").hidden = false;
    text($("qr-loading"), "正在生成二维码…");
    text($("qr-status"), "");
    try {
      const qr = await call("qr_start");
      if (generation !== state.qrGeneration || !$("qr-dialog").open) return;
      $("qr-image").src = qr.image;
      $("qr-image").hidden = false;
      $("qr-loading").hidden = true;
      text($("qr-status"), "等待扫码");
      async function pollQr() {
        try {
          const result = await call("qr_poll", { version: qr.version });
          if (generation !== state.qrGeneration) return;
          text(
            $("qr-status"),
            result.code === 86101
              ? "等待扫码"
              : result.code === 86090
                ? "已扫码，请在手机上确认"
                : result.message,
          );
          if (result.code === 0) {
            $("qr-dialog").close();
            notify("登录成功");
            refresh();
          } else if (result.code !== 86038) qrTimer = setTimeout(pollQr, 2000);
        } catch (error) {
          if (generation === state.qrGeneration)
            text($("qr-status"), error.message);
        }
      }
      qrTimer = setTimeout(pollQr, 2000);
    } catch (error) {
      if (generation === state.qrGeneration) {
        text($("qr-loading"), "二维码生成失败");
        text($("qr-status"), error.message);
      }
    }
  }
  listen("qr-login", "click", () => {
    $("qr-dialog").showModal();
    return startQr();
  });
  listen("account-button", "click", () => {
    if (!$("logout").hidden) {
      showPage("settings");
      $("page-settings").scrollTop = 0;
      return;
    }
    $("qr-dialog").showModal();
    return startQr();
  });
  listen("qr-refresh", "click", startQr);
  listen("qr-close", "click", () => $("qr-dialog").close());
  listen("qr-dialog", "close", () => {
    state.qrGeneration++;
    clearTimeout(qrTimer);
    return call("qr_cancel");
  });
  function renderLogs() {
    const keyword = $("log-filter").value.toLocaleLowerCase(),
      node = $("log-content"),
      atEnd = node.scrollHeight - node.scrollTop - node.clientHeight < 35;
    text(
      node,
      state.logs
        .filter(
          (item) => !keyword || item.text.toLocaleLowerCase().includes(keyword),
        )
        .map((item) => item.text)
        .join("\n"),
    );
    if (atEnd) node.scrollTop = node.scrollHeight;
  }
  listen("log-filter", "input", renderLogs);
  window.desktopRequestClose = () => {
    if (!$("close-dialog").open) $("close-dialog").showModal();
  };
  listen("close-cancel", "click", () => $("close-dialog").close());
  listen("close-minimize", "click", async () => {
    $("close-dialog").close();
    await call("close", { mode: "minimize" });
  });
  listen("close-exit", "click", () =>
    busy($("close-exit"), () => call("close", { mode: "exit" })),
  );
  document.addEventListener("keydown", (e) => {
    if (!e.ctrlKey || document.querySelector("dialog[open]")) return;
    const key = e.key.toLowerCase();
    if (key === "l") {
      e.preventDefault();
      showPage("parse");
      $("link-input").focus();
    }
    if (key === "f") {
      e.preventDefault();
      showPage(state.page === "logs" ? "logs" : "queue");
      $(state.page === "logs" ? "log-filter" : "queue-search").focus();
    }
    if (key === "enter" && state.page === "parse") {
      e.preventDefault();
      parseLink();
    }
    if (["1", "2", "3", "4"].includes(key)) {
      e.preventDefault();
      showPage(Object.keys(titles)[Number(key) - 1]);
    }
  });
})();
