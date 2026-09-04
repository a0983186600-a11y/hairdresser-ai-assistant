/*
 * 首頁的對話串。
 *
 * 這裡最重要的不是把答案印出來，是**把過程印出來**。評審看到的如果只有一段
 * 漂亮的中文，他沒辦法分辨那是查出來的還是編出來的；所以每一次工具呼叫都變成
 * 一張卡片，工具名、參數、結果摘要三樣都露在畫面上，一張一張淡入。
 *
 * 伺服器是一次把 reply 與 tool_calls 一起回來的（不做 streaming，少一層可能出錯的
 * 東西）。逐張出現是前端排的節奏：先「正在查 …」，再翻成結果摘要，全部翻完才
 * 讓助理開口。看起來跟真的在查一樣，而且不需要 SSE。
 *
 * 回訪草稿另外處理：`draft_follow_up_message` 出現時，助理的回覆包成一張訊息
 * 預覽卡，上面有「送出到 LINE」。**那顆按鈕按下去不會送**——示範環境沒有任何
 * 真的送出路徑，按了只跳一句提示。決定送不送的是設計師本人，不是助理。
 */
(function (global) {
  "use strict";

  var DRAFT_TOOL = "draft_follow_up_message";
  var CARD_GAP_MS = 420;
  var CARD_WORK_MS = 520;

  var thread;
  var box;
  var sendButton;
  var sessionId = null;
  var busy = false;

  function el(tag, className, text) {
    return global.AssistantShell.node(tag, className, text);
  }

  function scrollDown() {
    thread.scrollTop = thread.scrollHeight;
  }

  function describeArguments(args) {
    if (!args || typeof args !== "object") {
      return "";
    }
    return Object.keys(args).map(function (key) {
      var value = args[key];
      if (typeof value === "string") {
        return key + '="' + value + '"';
      }
      if (Array.isArray(value)) {
        return key + "=[" + value.join(", ") + "]";
      }
      return key + "=" + JSON.stringify(value);
    }).join(", ");
  }

  function signature(call) {
    return call.name + "(" + describeArguments(call.arguments) + ")";
  }

  function toolCard(call, finished) {
    var card = el("div", "toolcard fade" + (finished ? " done" : ""));
    card.appendChild(el("div", "state", finished ? "查完了" : "正在查…"));
    card.appendChild(el("div", "call", signature(call)));
    if (finished) {
      card.appendChild(el("div", "state", call.result_summary || "（沒有摘要）"));
    }
    return card;
  }

  function turn(kind, speaker) {
    var wrap = el("div", "turn " + kind + " fade");
    wrap.appendChild(el("div", "speaker", speaker));
    thread.appendChild(wrap);
    return wrap;
  }

  function saidByDesigner(text) {
    var wrap = turn("mine", "我");
    wrap.appendChild(el("div", "bubble", text));
    scrollDown();
  }

  function usedToolsBlock(calls) {
    var box_ = el("details", "used");
    var summary = el("summary", null, "用了哪些工具（" + calls.length + "）");
    box_.appendChild(summary);
    var steps = el("div", "steps");
    calls.forEach(function (call) {
      var line = el("div", "toolcard done");
      line.appendChild(el("div", "call", signature(call)));
      line.appendChild(el("div", "state", call.result_summary || "（沒有摘要）"));
      steps.appendChild(line);
    });
    box_.appendChild(steps);
    return box_;
  }

  function copyText(text) {
    if (global.navigator && global.navigator.clipboard) {
      return global.navigator.clipboard.writeText(text);
    }
    return Promise.reject(new Error("這個瀏覽器不給複製"));
  }

  function draftCard(text) {
    var card = el("div", "draft fade");
    var cap = el("div", "cap");
    cap.appendChild(el("span", null, "回訪訊息草稿"));
    cap.appendChild(el("span", null, "AI 擬稿 · 你按送出"));
    card.appendChild(cap);
    card.appendChild(el("div", "body", text));

    var bar = el("div", "bar");
    var send = el("button", "primary", "送出到 LINE");
    send.type = "button";
    send.addEventListener("click", function () {
      // 示範版刻意沒有送出路徑：這顆按鈕存在是為了說明「決定的是人」，
      // 不是為了真的發訊息給客人。
      global.AssistantShell.toast("示範環境不會真的送出");
    });
    var copy = el("button", "quiet", "複製");
    copy.type = "button";
    copy.addEventListener("click", function () {
      copyText(text).then(function () {
        global.AssistantShell.toast("草稿已複製");
      }).catch(function () {
        global.AssistantShell.toast("複製失敗，請手動選取");
      });
    });
    bar.appendChild(send);
    bar.appendChild(copy);
    bar.appendChild(el("span", "why", "送出前請自己讀一遍"));
    card.appendChild(bar);
    return card;
  }

  function wait(ms) {
    return new Promise(function (resolve) {
      global.setTimeout(resolve, ms);
    });
  }

  function playSteps(host, calls) {
    var steps = el("div", "steps");
    host.appendChild(steps);
    return calls.reduce(function (chain, call) {
      return chain.then(function () {
        var card = toolCard(call, false);
        steps.appendChild(card);
        scrollDown();
        return wait(CARD_WORK_MS).then(function () {
          steps.replaceChild(toolCard(call, true), card);
          scrollDown();
          return wait(CARD_GAP_MS);
        });
      });
    }, Promise.resolve());
  }

  function answer(reply, calls) {
    var wrap = turn("theirs", "助理");
    var pending = el("div", "toolcard", "正在想…");
    wrap.appendChild(pending);
    scrollDown();

    return wait(260).then(function () {
      pending.remove();
      return calls.length ? playSteps(wrap, calls) : null;
    }).then(function () {
      var drafted = calls.some(function (call) {
        return call.name === DRAFT_TOOL;
      });
      wrap.appendChild(drafted ? draftCard(reply) : el("div", "bubble", reply));
      if (calls.length) {
        wrap.appendChild(usedToolsBlock(calls));
      }
      scrollDown();
    });
  }

  function complain(message) {
    var wrap = turn("theirs", "助理");
    var bubble = el("div", "bubble", "這一題沒問成：" + message);
    wrap.appendChild(bubble);
    scrollDown();
  }

  function ask(text) {
    if (busy || !text.trim()) {
      return;
    }
    busy = true;
    sendButton.disabled = true;
    saidByDesigner(text.trim());
    box.value = "";
    global.AssistantApi.ask(text.trim(), sessionId).then(function (result) {
      sessionId = result.session_id || sessionId;
      return answer(result.reply, result.tool_calls || []);
    }).catch(function (error) {
      complain(error.message);
    }).then(function () {
      busy = false;
      sendButton.disabled = false;
      box.focus();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    thread = document.querySelector("[data-thread]");
    box = document.querySelector("[data-say]");
    sendButton = document.querySelector("[data-send]");
    if (!thread || !box || !sendButton) {
      return;
    }

    sendButton.addEventListener("click", function () {
      ask(box.value);
    });

    box.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        ask(box.value);
      }
    });

    Array.prototype.forEach.call(
      document.querySelectorAll("[data-quick-prompt]"),
      function (button) {
        button.addEventListener("click", function () {
          ask(button.getAttribute("data-quick-prompt"));
        });
      }
    );
  });
})(window);
