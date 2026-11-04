(() => {
  const errors = [],
    passed = [],
    $ = (id) => document.getElementById(id);
  window.addEventListener("error", (event) => errors.push(event.message));
  window.addEventListener("unhandledrejection", (event) =>
    errors.push(String(event.reason)),
  );
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const wait = async (predicate, label) => {
    for (let count = 0; count < 120; count++) {
      if (predicate()) return;
      await sleep(40);
    }
    throw new Error(`Timeout: ${label}`);
  };
  const assert = (condition, label) => {
    if (!condition) throw new Error(label);
    passed.push(label);
  };
  const input = (id, value) => {
    $(id).value = value;
    $(id).dispatchEvent(new Event("input", { bubbles: true }));
  };
  const inViewport = (id) => {
    const node = $(id),
      rect = node.getBoundingClientRect();
    const hit = document.elementFromPoint(
      rect.left + rect.width / 2,
      rect.top + rect.height / 2,
    );
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      rect.top >= 0 &&
      rect.left >= 0 &&
      rect.bottom <= innerHeight &&
      rect.right <= innerWidth &&
      (node === hit || node.contains(hit))
    );
  };
  const capture = async (phase) => {
    if (!window.__captureEnabled) return;
    await sleep(250);
    window.__capturePhase = phase;
    await wait(() => !window.__capturePhase, `capture ${phase}`);
  };
  (async () => {
    try {
      await wait(() => !$("parse-button").disabled, "service startup");
      const inputHeight = $("link-input").getBoundingClientRect().height;
      await capture("empty");
      input("link-input", "BV1xx411c7mD");
      $("parse-form").requestSubmit();
      await wait(
        () => !$("quality-select").disabled && !$("add-button").disabled,
        "parse and quality",
      );
      assert(
        $("media-title").textContent.includes("自然纪录片"),
        "parse displays media",
      );
      assert(
        $("episode-list").children.length === 12,
        "episode DOM is bounded",
      );
      assert(
        inViewport("add-button") && inViewport("change-destination"),
        "download and folder controls stay in view",
      );
      assert(
        inViewport("quality-select") && inViewport("download-mode"),
        "collection quality and mode controls remain accessible",
      );
      assert(
        $("link-input").getBoundingClientRect().height === inputHeight,
        "parse keeps input layout stable",
      );
      $("change-destination").click();
      await wait(
        () =>
          $("destination-button").textContent.endsWith("Chosen") &&
          !$("add-button").disabled,
        "choose download folder inline",
      );
      assert(
        !$("page-parse").hidden,
        "folder picker keeps current download page",
      );
      await capture("parse");
      $("episode-list").scrollTop = 80;
      const episodeScroll = $("episode-list").scrollTop;
      $("select-all").click();
      assert(
        $("episode-list").scrollTop === episodeScroll,
        "episode selection preserves scroll position",
      );
      $("episodes-next").click();
      assert(
        [...$("episode-list").querySelectorAll("input")].every(
          (node) => node.checked,
        ),
        "selection survives paging",
      );
      document.querySelector('[data-page="queue"]').click();
      document.querySelector('[data-filter="completed"]').click();
      input("queue-search", "no-such-title");
      document.querySelector('[data-page="parse"]').click();
      $("add-button").click();
      assert(
        $("quality-select").disabled &&
          $("link-input").disabled &&
          $("select-all").disabled,
        "adding freezes the current download selection",
      );
      await wait(
        () => !$("page-queue").hidden && $("task-list").children.length === 30,
        "batch add and queue",
      );
      assert(
        $("queue-summary").textContent.includes("100"),
        "batch creates 25 tasks",
      );
      assert(
        $("queue-search").value === "" &&
          document
            .querySelector('[data-filter="all"]')
            .getAttribute("aria-pressed") === "true",
        "new downloads reset old queue filters",
      );
      await capture("queue");
      document.querySelector('[data-filter="completed"]').click();
      await capture("queue-completed");
      document.querySelector('[data-filter="all"]').click();
      $("page-queue").scrollTop = 120;
      const queueScroll = $("page-queue").scrollTop;
      document.querySelector('[data-page="settings"]').click();
      document.querySelector('[data-page="queue"]').click();
      assert(
        $("page-queue").scrollTop === queueScroll,
        "page switching restores queue scroll position",
      );
      $("page-queue").scrollTop = 0;
      const row = $("task-list").firstElementChild;
      await sleep(1200);
      assert(
        row === $("task-list").firstElementChild,
        "queue polling preserves row DOM",
      );
      $("select-page").click();
      $("tasks-next").click();
      assert(
        $("batch-count").textContent.includes("30"),
        "task selection survives paging",
      );
      input("queue-search", "设计与生活");
      assert(
        $("task-list").children.length === 1,
        "queue search returns match",
      );
      document.querySelector('[data-page="settings"]').click();
      input("save-path", $("save-path").value);
      input("speed-limit", "2.5");
      $("appearance").value = "dark";
      $("settings-form").requestSubmit();
      await wait(
        () => $("settings-feedback").textContent === "设置已保存",
        "settings save",
      );
      assert(
        document.documentElement.dataset.theme === "dark",
        "theme applies after save",
      );
      await capture("settings-dark");
      $("settings-form").elements.animations_enabled.checked = false;
      $("settings-form").requestSubmit();
      await wait(
        () => document.documentElement.dataset.motion === "off",
        "motion setting",
      );
      assert(true, "reduced motion setting applies");
      document.querySelector('[data-page="logs"]').click();
      assert(
        $("log-content").textContent.includes("演示服务"),
        "logs arrive through bridge",
      );
      document.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "l",
          ctrlKey: true,
          bubbles: true,
        }),
      );
      assert(
        document.activeElement === $("link-input"),
        "Ctrl+L focuses link field",
      );
      $("clear-input").click();
      assert(
        $("media-result").hidden && $("link-input").value === "",
        "clear resets parsed selection",
      );
      input("link-input", "BV1xx411c7mD");
      $("parse-form").requestSubmit();
      await wait(
        () => !$("quality-select").disabled && !$("add-button").disabled,
        "parse before mode changes",
      );
      document.querySelector('[data-download-mode="audio"]').click();
      await wait(
        () =>
          !$("quality-select").disabled &&
          $("quality-select").value === "30280",
        "audio quality options",
      );
      await wait(
        () => $("filename-preview").textContent.endsWith(".m4a"),
        "audio filename preview",
      );
      assert(
        $("quality-label").textContent === "音频品质",
        "audio mode has independent audio qualities and M4A preview",
      );
      $("add-button").scrollIntoView({ block: "center" });
      await capture("audio-mode");
      $("add-button").click();
      await wait(
        () =>
          !$("page-queue").hidden &&
          $("queue-summary").textContent.includes("101"),
        "audio added to queue",
      );
      input("queue-search", ".m4a");
      assert(
        $("task-list").children.length === 1 &&
          $("task-list").textContent.includes("仅音频"),
        "audio queue row retains mode and output format",
      );
      document.querySelector('[data-page="parse"]').click();
      $("parse-form").requestSubmit();
      await wait(
        () =>
          !$("quality-select").disabled && $("quality-select").value === "80",
        "new parse resets temporary audio mode",
      );
      assert(
        document
          .querySelector('[data-download-mode="combined"]')
          .getAttribute("aria-pressed") === "true",
        "temporary mode does not change default",
      );
      document.querySelector('[data-download-mode="video"]').click();
      await wait(
        () => !$("quality-select").disabled && !$("add-button").disabled,
        "video-only options",
      );
      await wait(
        () => $("filename-preview").textContent.endsWith(".mp4"),
        "video filename preview",
      );
      assert(
        $("download-result-note").textContent.includes("无声"),
        "video-only mode warns there is no audio",
      );
      $("add-button").click();
      await wait(
        () =>
          !$("page-queue").hidden &&
          $("queue-summary").textContent.includes("102"),
        "video-only added",
      );
      input("queue-search", "");
      assert(
        $("task-list").textContent.includes("仅视频（无声）"),
        "video-only mode survives queue update",
      );
      document.querySelector('[data-page="parse"]').click();
      input("link-input", "single-video");
      $("link-input").dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
          cancelable: true,
        }),
      );
      await wait(
        () => !$("quality-select").disabled && !$("add-button").disabled,
        "single video Enter shortcut",
      );
      assert(
        $("episode-panel").hidden &&
          document.body.classList.contains("single-media"),
        "single video skips episode selection",
      );
      assert(
        inViewport("add-button") &&
          inViewport("quality-select") &&
          inViewport("download-mode"),
        "single video options and download stay visible",
      );
      await wait(
        () => !$("destination-button").textContent.endsWith("Chosen"),
        "new parse restores default folder",
      );
      assert(
        !$("destination-button").textContent.endsWith("Chosen"),
        "per-download folder does not change default",
      );
      await capture("single-video-dark");
      $("appearance").value = "light";
      $("settings-form").requestSubmit();
      await wait(
        () => document.documentElement.dataset.theme === "light",
        "return to light theme",
      );
      await capture("single-video");
      document.querySelector('[data-page="queue"]').click();
      $("header-add").click();
      assert(
        !$("page-parse").hidden &&
          $("media-result").hidden &&
          document.activeElement === $("link-input"),
        "new download opens a fresh focused input",
      );
      assert(
        document.documentElement.scrollWidth <= innerWidth,
        "no horizontal viewport overflow",
      );
      if (window.__nativeCloseCheck) {
        window.__capturePhase = "native-close";
        await wait(() => $("close-dialog").open, "native close confirmation");
        $("close-cancel").click();
        assert(
          !$("close-dialog").open,
          "native close can be cancelled with active downloads",
        );
      }
      assert(errors.length === 0, `no JavaScript errors: ${errors.join("; ")}`);
      window.__smokeResult = {
        ok: true,
        passed,
        renderer: navigator.userAgent,
      };
    } catch (error) {
      window.__smokeResult = {
        ok: false,
        error: String(error.stack || error),
        passed,
        errors,
      };
    }
  })();
})();
