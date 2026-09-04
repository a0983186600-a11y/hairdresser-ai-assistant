/*
 * 五個頁面共用的外框：導覽的目前位置、右上角資料來源徽章、還有 toast。
 *
 * 徽章是影片裡全程可見的那一顆，讀的是 /health 的 mode，不是前端自己記的狀態——
 * 前端記的會跟伺服器不同步，而「畫面說 DEMO、其實在讀正式資料」是這支作品
 * 最不能出的錯。按下去切換模式，切不過去時把伺服器給的理由原話顯示出來。
 */
(function (global) {
  "use strict";

  var LABEL = { demo: "DEMO", production: "PRODUCTION" };
  var HINT = {
    demo: "固定 seed 假資料",
    production: "唯讀正式資料"
  };

  function markNav() {
    var here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
    Array.prototype.forEach.call(document.querySelectorAll(".nav a"), function (link) {
      var target = (link.getAttribute("href") || "").toLowerCase();
      if (target === here) {
        link.setAttribute("aria-current", "page");
      }
    });
  }

  var toastTimer = null;

  function toast(text) {
    var existing = document.querySelector(".toast");
    if (existing) {
      existing.remove();
    }
    var node = document.createElement("div");
    node.className = "toast fade";
    node.setAttribute("role", "status");
    node.textContent = text;
    document.body.appendChild(node);
    global.clearTimeout(toastTimer);
    toastTimer = global.setTimeout(function () {
      node.remove();
    }, 3600);
  }

  function paint(badge, mode, note, pagesLabel) {
    var known = LABEL[mode] ? mode : "demo";
    badge.dataset.mode = known;
    badge.innerHTML = "";
    var pip = document.createElement("span");
    pip.className = "pip";
    badge.appendChild(pip);
    var name = document.createElement("span");
    name.textContent = LABEL[known];
    badge.appendChild(name);
    var hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = HINT[known];
    badge.appendChild(hint);
    if (pagesLabel) {
      // 資料頁（預約／班表／客人／設定）的來源可能跟聊天的資料來源不同：
      // production 模式沒設後台位址時四頁仍是示範 fixture，/health 會用 data_source_label 講出來。
      var pages = document.createElement("span");
      pages.className = "pages";
      pages.textContent = pagesLabel;
      badge.appendChild(pages);
    }
    badge.title = note || HINT[known];
    global.AssistantShell.mode = known;
  }

  function wireBadge() {
    var badge = document.querySelector("[data-source-badge]");
    if (!badge) {
      return;
    }
    global.AssistantApi.health().then(function (health) {
      paint(badge, health.mode, health.data_source_note || health.production_note || health.replay_note, health.data_source_label);
    }).catch(function () {
      paint(badge, "demo", "連不上伺服器，先當作示範模式");
    });

    if (!badge.hasAttribute("data-mode-switch")) {
      return;
    }
    badge.addEventListener("click", function () {
      var next = badge.dataset.mode === "production" ? "demo" : "production";
      badge.disabled = true;
      global.AssistantApi.switchMode(next).then(function (result) {
        paint(badge, result.mode, result.data_source_note || result.production_note, result.data_source_label);
        toast(next === "production" ? "已切到正式唯讀資料" : "已切回示範資料");
      }).catch(function (error) {
        toast("切不過去：" + error.message);
      }).then(function () {
        badge.disabled = false;
      });
    });
  }

  global.AssistantShell = {
    mode: "demo",
    toast: toast,
    money: function (amount) {
      return "NT$" + Number(amount || 0).toLocaleString("zh-Hant-TW");
    },
    node: function (tag, className, text) {
      var element = document.createElement(tag);
      if (className) {
        element.className = className;
      }
      if (text !== undefined && text !== null) {
        element.textContent = text;
      }
      return element;
    }
  };

  document.addEventListener("DOMContentLoaded", function () {
    markNav();
    wireBadge();
  });
})(window);
