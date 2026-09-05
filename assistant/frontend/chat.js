/* One conversation shared by the home composer and the floating assistant.
 *
 * 卡片上的東西全是伺服器**已經跑完**的 trace：一次回傳 reply 與 tool_calls，
 * 沒有 streaming、沒有第二條連線。一張一張出現是前端排的呈現節奏，不是即時串流——
 * 排它是因為「AI 查了什麼」要看得見，同時出現等於只看得到結果。
 * 節奏只決定「什麼時候畫」，畫的內容一律是伺服器回的那幾張，不會多一張。
 *
 * 畫面用增量的方式長：新的一則才建節點，已經在畫面上的 turn 不重建
 * （整串重建會讓每一輪的淡入重播一次，截圖會拍到整頁在變淡）。
 */
(function (g) {
  "use strict";
  var S = g.AssistantShell,
    n = S.node,
    b = S.button,
    DRAFT_TOOL = "draft_follow_up_message",
    CARD_WORK_MS = 520, // 一張卡從「正在查…」翻成「查完了」
    CARD_GAP_MS = 420, // 翻完到下一張出現
    messages = [],
    serial = 0,
    session = null,
    busy = false,
    generation = 0;
  function signature(call) {
    return call.name + "(" + JSON.stringify(call.arguments) + ")";
  }
  function card(call) {
    var wrap = n("div", "toolcard fade"),
      state = n("div", "state", "正在查…");
    wrap.append(state, n("div", "call", signature(call)));
    return { node: wrap, state: state, done: false };
  }
  function flip(item, call) {
    item.done = true;
    item.node.classList.add("done");
    item.state.textContent = "查完了";
    item.node.append(n("div", "state", call.result_summary || "沒有摘要"));
  }
  function draft(reply) {
    var wrap = n("div", "draft"),
      bar = n("div", "actions");
    wrap.append(n("div", "cap", "回訪草稿 · 尚未送出"), n("div", "body", reply));
    bar.append(
      b(
        "複製",
        function () {
          S.copy(reply).catch(function (e) {
            S.toast(e.message);
          });
        },
        "secondary",
      ),
      b(
        "送出到 LINE",
        function () {
          S.open(
            "草稿尚未送出",
            function (host) {
              host.append(
                n(
                  "p",
                  "warning",
                  "示範環境不會真的送出。請自行確認內容；這裡沒有 LINE 發送權限。",
                ),
                b(
                  "複製草稿",
                  function () {
                    S.copy(reply).catch(function (e) {
                      S.fail(host, e);
                    });
                  },
                  "primary full",
                ),
              );
            },
            true,
          );
        },
        "primary",
      ),
    );
    wrap.append(bar);
    return wrap;
  }
  function build(m) {
    var wrap = n("div", "turn " + (m.role === "user" ? "mine" : "theirs"));
    wrap.append(n("div", "speaker", m.role === "user" ? "你" : "助理"));
    if (m.role === "user") wrap.append(n("div", "bubble", m.text));
    return { wrap: wrap, cards: [], steps: null, waiting: null, tail: null };
  }
  function update(m, it) {
    if (m.role === "user") return;
    if (m.pending && !it.waiting) {
      it.waiting = n("div", "bubble waiting");
      it.waiting.setAttribute("aria-label", "正在查資料");
      it.waiting.append(n("span"), n("span"), n("span"));
      it.wrap.append(it.waiting);
    } else if (!m.pending && it.waiting) {
      it.waiting.remove();
      it.waiting = null;
    }
    if (m.error) {
      if (!it.tail) {
        it.tail = n("div", "error", m.text);
        it.wrap.append(
          it.tail,
          b(
            "重試這一題",
            function () {
              ask(m.question);
            },
            "text-button",
          ),
        );
      }
      return;
    }
    var calls = m.tool_calls || [];
    if (calls.length && !it.steps) {
      var used = n("div", "used");
      it.steps = n("div", "steps");
      used.append(
        n("div", "cap", "用了哪些工具（" + calls.length + "）"),
        it.steps,
      );
      it.wrap.append(used);
    }
    while (it.cards.length < (m.shown || 0)) {
      var made = card(calls[it.cards.length]);
      it.cards.push(made);
      it.steps.append(made.node);
    }
    it.cards.forEach(function (one, i) {
      if (!one.done && i < (m.done || 0)) flip(one, calls[i]);
    });
    if (m.answered && !it.tail) {
      it.tail = calls.some(function (x) {
        return x.name === DRAFT_TOOL;
      })
        ? draft(m.text)
        : n("div", "bubble", m.text);
      it.wrap.append(it.tail);
    }
  }
  function render(host) {
    if (!host.chatView || host.chatView.generation !== generation) {
      host.replaceChildren();
      host.chatView = { generation: generation, items: {} };
    }
    var view = host.chatView;
    messages.forEach(function (m) {
      var it = view.items[m.id];
      if (!it) {
        it = view.items[m.id] = build(m);
        host.append(it.wrap);
      }
      update(m, it);
    });
  }
  function paint() {
    document
      .querySelectorAll("[data-thread],[data-mini-thread]")
      .forEach(render);
    document
      .querySelectorAll("[data-send],[data-mini-send],[data-quick-prompt]")
      .forEach(function (x) {
        x.disabled = busy;
      });
  }
  function scrollToLatest() {
    requestAnimationFrame(function () {
      var host = document.querySelector("[data-mini-thread]");
      if (!host && !document.querySelector(".sheet"))
        host = document.querySelector("[data-thread]");
      if (host && host.lastChild)
        host.lastChild.scrollIntoView({ block: "end" });
    });
  }
  function wait(ms) {
    return new Promise(function (done) {
      setTimeout(done, ms);
    });
  }
  async function playSteps(m, version) {
    var calls = m.tool_calls || [];
    for (var i = 0; i < calls.length; i++) {
      m.shown = i + 1;
      paint();
      scrollToLatest();
      await wait(CARD_WORK_MS);
      if (version !== generation) return;
      m.done = i + 1;
      paint();
      scrollToLatest();
      await wait(CARD_GAP_MS);
      if (version !== generation) return;
    }
  }
  async function ask(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true;
    var version = generation;
    messages.push({ id: ++serial, role: "user", text: text });
    var pending = {
      id: ++serial,
      role: "assistant",
      pending: true,
      shown: 0,
      done: 0,
    };
    messages.push(pending);
    document
      .querySelectorAll("[data-say],[data-mini-say]")
      .forEach(function (x) {
        x.value = "";
      });
    paint();
    scrollToLatest();
    try {
      var result = await g.AssistantApi.ask(text, session);
      if (version !== generation) return;
      session = result.session_id || session;
      Object.assign(pending, {
        pending: false,
        text: result.reply,
        tool_calls: result.tool_calls || [],
      });
      paint();
      await playSteps(pending, version);
      if (version !== generation) return;
      pending.answered = true;
    } catch (e) {
      if (version !== generation) return;
      Object.assign(pending, {
        pending: false,
        error: true,
        text:
          "這題沒有完成，沒有改動任何預約。" +
          (e.status ? " 請稍後重試。" : " 請確認網路或服務已啟動。"),
        question: text,
      });
    } finally {
      if (version === generation) {
        busy = false;
        paint();
        scrollToLatest();
      }
    }
  }
  function wire(form, input) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      ask(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        ask(input.value);
      }
    });
  }
  function open() {
    S.open(
      "你的助理",
      function (host) {
        var thread = n("div", "thread");
        thread.dataset.miniThread = "";
        host.append(thread);
        if (!messages.length)
          host.append(n("p", "note", "這裡和首頁是同一段對話。"));
        var form = n("form", "composer"),
          input = n("textarea"),
          send = b("↑", null, "primary icon");
        input.rows = 1;
        input.dataset.miniSay = "";
        input.setAttribute("aria-label", "在小視窗問助理");
        input.placeholder = "接著問就好…";
        send.type = "submit";
        send.dataset.miniSend = "";
        send.setAttribute("aria-label", "送出給助理");
        form.append(input, send);
        host.append(form);
        wire(form, input);
        paint();
      },
      true,
    );
  }
  g.AssistantChat = {
    ask: ask,
    open: open,
    reset: function () {
      generation++;
      messages = [];
      session = null;
      busy = false;
      paint();
    },
  };
  document.addEventListener("DOMContentLoaded", function () {
    wire(
      document.querySelector("[data-chat-form]"),
      document.querySelector("[data-say]"),
    );
    document.querySelectorAll("[data-quick-prompt]").forEach(function (x) {
      x.addEventListener("click", function () {
        ask(x.dataset.quickPrompt);
      });
    });
  });
})(window);
