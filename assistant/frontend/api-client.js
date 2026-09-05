/*
 * 前端唯一對外講話的地方。
 *
 * base URL 不准寫死：這一份會跟著 assistant/ 匯出到公開 repo，任何被硬編進來的
 * 位址都會變成「這份程式其實綁著某一台主機」的證據。所以順序是
 *   window.ASSISTANT_API_BASE  →  <meta name="assistant-api-base">  →  同源
 * 三段都沒設就打同源，也就是 uvicorn 自己——評審 clone 下來什麼都不用改。
 */
(function (global) {
  "use strict";

  function trimTail(value) {
    return String(value).trim().replace(/\/+$/, "");
  }

  function resolveBase() {
    if (
      typeof global.ASSISTANT_API_BASE === "string" &&
      global.ASSISTANT_API_BASE.trim()
    ) {
      return trimTail(global.ASSISTANT_API_BASE);
    }
    var tag = document.querySelector('meta[name="assistant-api-base"]');
    var declared = tag && tag.getAttribute("content");
    return declared && declared.trim() ? trimTail(declared) : "";
  }

  var BASE = resolveBase();

  function at(path) {
    return BASE + path;
  }

  function readBody(response) {
    return response.text().then(function (raw) {
      try {
        return raw ? JSON.parse(raw) : null;
      } catch (error) {
        return { detail: raw };
      }
    });
  }

  function send(path, options) {
    return fetch(at(path), options || {}).then(function (response) {
      return readBody(response).then(function (body) {
        if (!response.ok) {
          var detail =
            (body && body.detail) ||
            response.status + " " + response.statusText;
          var failure = new Error(
            typeof detail === "string" ? detail : JSON.stringify(detail),
          );
          failure.status = response.status;
          throw failure;
        }
        return body;
      });
    });
  }

  function post(path, payload) {
    return send(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  global.AssistantApi = {
    base: BASE,
    at: at,
    health: function () {
      return send("/health");
    },
    readMode: function () {
      return send("/api/mode");
    },
    switchMode: function (mode) {
      return post("/api/mode", { mode: mode });
    },
    ask: function (message, sessionId) {
      return post("/api/chat", {
        message: message,
        session_id: sessionId || null,
      });
    },
    demo: function (page) {
      return send("/api/demo/" + page);
    },
    workbench: function () {
      return send("/api/workbench");
    },
    action: function (kind, data) {
      return post("/api/workbench/actions", { kind: kind, data: data || {} });
    },
    customer: function (ref) {
      return send("/api/workbench/customers/" + encodeURIComponent(ref));
    },
    conversations: function () {
      return send("/api/workbench/conversations");
    },
    transcript: function (ref) {
      return send("/api/workbench/conversations/" + encodeURIComponent(ref));
    },
    draft: function (ref) {
      return send("/api/workbench/draft/" + encodeURIComponent(ref));
    },
  };
})(window);
