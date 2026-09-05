/* Shared navigation and one server-backed source for the rehearsal. */
(function (g) {
  "use strict";
  var stack = [],
    serial = 0,
    timer;
  function node(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function button(label, fn, cls) {
    var b = node("button", cls || "secondary", label);
    b.type = "button";
    if (fn) b.addEventListener("click", fn);
    return b;
  }
  function toast(text) {
    var box = document.querySelector("[data-toast]");
    box.textContent = text;
    box.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(function () {
      box.hidden = true;
    }, 4500);
  }
  function field(label, type, value) {
    var wrap = node("label", "field", label),
      input = node(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") input.type = type || "text";
    input.value = value == null ? "" : value;
    wrap.append(input);
    return { wrap: wrap, input: input };
  }
  function close() {
    if (stack.length) {
      stack.pop();
      paint();
    }
  }
  function closeAll() {
    stack = [];
    paint();
  }
  function paint() {
    var root = document.querySelector("[data-sheet-root]");
    root.replaceChildren();
    var active = stack[stack.length - 1];
    document.querySelector(".workbench").inert = !!active;
    document.body.style.overflow = active ? "hidden" : "";
    document.querySelector("[data-assistant-ball]").hidden =
      !active || active.noBall;
    if (!active) return;
    var layer = node("div", "sheet-layer"),
      sheet = node("section", "sheet");
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-label", active.title);
    sheet.tabIndex = -1;
    sheet.append(node("div", "sheet-handle"));
    var head = node("header", "sheet-header");
    var back = button("‹", close, "icon");
    back.setAttribute("aria-label", "回上一層");
    head.append(back, node("h2", null, active.title));
    var x = button("×", close, "icon");
    x.setAttribute("aria-label", "關閉視窗");
    head.append(x);
    sheet.append(head, active.host);
    layer.append(sheet);
    root.append(layer);
    layer.addEventListener("click", function (e) {
      if (e.target === layer) close();
    });
    sheet.focus();
  }
  function open(title, render, noBall) {
    var host = node("div", "sheet-content");
    host.dataset.view = ++serial;
    stack.push({ title: title, host: host, noBall: !!noBall });
    paint();
    Promise.resolve()
      .then(function () {
        return render(host);
      })
      .catch(function (e) {
        fail(host, e);
      });
    return host;
  }
  function fail(host, error, retry) {
    var box = node(
      "div",
      "error",
      error.message || "這次讀取沒有完成，請重試。",
    );
    box.setAttribute("role", "alert");
    host.append(box);
    if (retry) host.append(button("重新讀取", retry));
  }
  function confirmAction(title, text, fn) {
    return open(
      title,
      function (host) {
        host.append(node("p", "note", text));
        var yes = button(
          "確認（僅示範）",
          async function () {
            yes.disabled = true;
            try {
              await fn();
              close();
            } catch (e) {
              fail(host, e);
              yes.disabled = false;
            }
          },
          "primary full",
        );
        host.append(yes, button("先不要", close, "text-button full"));
      },
      true,
    );
  }
  async function refresh() {
    var data = await g.AssistantApi.workbench();
    S.state = data;
    document.querySelector("[data-workbench-note]").textContent = data.notice;
    document.querySelector("[data-greeting]").textContent =
      "你好，今天想先處理什麼？我可以查客人、回訪與消費紀錄，也能幫你擬訊息。\n\n班表與開單在上面；這裡的操作只作示範，不會真的送出。";
    g.dispatchEvent(new CustomEvent("workbench-updated"));
    return data;
  }
  async function mutate(kind, data) {
    if (S.state && S.state.read_only)
      throw new Error("目前是正式唯讀模式，不能更動資料。");
    var result = await g.AssistantApi.action(kind, data);
    await refresh();
    toast(result.notice);
    return result;
  }
  async function health() {
    var b = document.querySelector("[data-source-badge]");
    try {
      var h = await g.AssistantApi.health();
      S.health = h;
      b.textContent = h.mode === "production" ? "正式唯讀" : "DEMO · 示範";
      b.dataset.mode = h.mode;
      b.title = (h.data_source_label || "") + "。" + (h.data_source_note || "");
      document.querySelector("[data-replay-label]").textContent =
        (h.replay_available
          ? "錄音重播"
          : (h.chat_model || "模型").split("/").pop()) + " · 工作台：示範";
    } catch (e) {
      b.textContent = "未連線";
      b.dataset.mode = "unknown";
      throw e;
    }
  }
  function copy(text) {
    if (!navigator.clipboard)
      return Promise.reject(new Error("瀏覽器不允許複製，請手動選取文字。"));
    return navigator.clipboard.writeText(text).then(function () {
      toast("已複製");
    });
  }
  var S = (g.AssistantShell = {
    node: node,
    button: button,
    field: field,
    toast: toast,
    open: open,
    close: close,
    closeAll: closeAll,
    fail: fail,
    confirm: confirmAction,
    refresh: refresh,
    mutate: mutate,
    copy: copy,
    state: null,
    health: null,
    money: function (n) {
      return n == null ? "未記錄" : "NT$" + Number(n).toLocaleString("zh-TW");
    },
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !document.querySelector(".tour-shade")) close();
    if (e.key !== "Tab" || !stack.length) return;
    var items = Array.from(
      document.querySelectorAll(
        ".sheet button:not(:disabled),.sheet input,.sheet select,.sheet textarea,.sheet a,.sheet summary",
      ),
    ).filter(function (x) {
      return x.getClientRects().length;
    });
    if (!items.length) return;
    if (
      e.shiftKey &&
      (document.activeElement === items[0] ||
        document.activeElement.classList.contains("sheet"))
    ) {
      e.preventDefault();
      items[items.length - 1].focus();
    } else if (
      !e.shiftKey &&
      document.activeElement === items[items.length - 1]
    ) {
      e.preventDefault();
      items[0].focus();
    }
  });
  document.addEventListener("DOMContentLoaded", async function () {
    document
      .querySelector("[data-source-badge]")
      .addEventListener("click", async function () {
        var b = this;
        b.disabled = true;
        try {
          var desired = b.dataset.mode === "production" ? "demo" : "production";
          await g.AssistantApi.switchMode(desired);
          closeAll();
          if (g.AssistantChat) g.AssistantChat.reset();
          await health();
          await refresh();
          toast(
            desired === "production"
              ? "助理已切為正式唯讀；工作台仍為示範。"
              : "已切回示範資料",
          );
        } catch (e) {
          toast("未切換：" + e.message);
        } finally {
          b.disabled = false;
        }
      });
    try {
      await health();
      await refresh();
      g.dispatchEvent(new CustomEvent("workbench-ready"));
    } catch (e) {
      document.querySelector("[data-greeting]").textContent =
        "工作台暫時連不上，請確認服務已開啟。";
      toast(e.message);
    }
  });
})(window);
