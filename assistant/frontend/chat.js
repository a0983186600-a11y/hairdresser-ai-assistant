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
  // Text nodes only: generated HTML is never interpreted and code never executes.
  function inline(host, text) {
    String(text).split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g).forEach(function (part) {
      host.append(part.startsWith("**") && part.endsWith("**")
        ? n("strong", null, part.slice(2, -2))
        : part.startsWith("`") && part.endsWith("`")
          ? n("code", null, part.slice(1, -1)) : document.createTextNode(part));
    });
  }
  function answer(text) {
    var wrap = n("div", "bubble answer"), lines = String(text).split("\n"), i = 0;
    function cells(line) { return line.trim().replace(/^\||\|$/g, "").split("|"); }
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (/^\s*```/.test(line)) {
        var code = [], pre = n("pre"); i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        pre.append(n("code", null, code.join("\n"))); wrap.append(pre); i++; continue;
      }
      if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
        var table = n("table"), head = n("thead"), body = n("tbody"), scroller = n("div", "answer-table");
        function tableRow(values, tag, parent) {
          var tr = n("tr"); values.forEach(function (value) {
            var cell = n(tag); inline(cell, value.trim()); tr.append(cell);
          }); parent.append(tr);
        }
        tableRow(cells(line), "th", head); i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) tableRow(cells(lines[i++]), "td", body);
        table.append(head, body); scroller.append(table); wrap.append(scroller); continue;
      }
      if (/^\s*(?:[-*+] |\d+[.)] )/.test(line)) {
        var ordered = /^\s*\d/.test(line), list = n(ordered ? "ol" : "ul");
        while (i < lines.length && (ordered ? /^\s*\d+[.)] / : /^\s*[-*+] /).test(lines[i])) {
          var item = n("li"); inline(item, lines[i++].replace(/^\s*(?:[-*+] |\d+[.)] )/, "")); list.append(item);
        }
        wrap.append(list); continue;
      }
      var heading = line.match(/^#{1,6}\s+(.+)/), paragraph = n(heading ? "h3" : "p");
      inline(paragraph, heading ? heading[1] : line); wrap.append(paragraph); i++;
    }
    return wrap;
  }
  function failureText(e) {
    var codes = {
      model_timeout: "模型回覆逾時，這題沒有完成。你可以重試，不必重打問題。",
      model_auth: "模型連線憑證需要檢查，請先處理設定；重試同一句暫時無法解決。",
      model_busy: "模型目前忙碌或達到額度限制，請稍後再試。",
      model_request: "模型未接受這個請求，需要檢查相容性設定。",
      model_unavailable: "模型連線暫時中斷，請稍後重試。"
    };
    return (codes[e.code] || ({
      401: "預覽登入已失效，請重新登入。", 410: "臨時預覽已到期，需要重新開啟入口。",
      429: "預覽請求太頻繁或已達測試上限，請稍後再試。",
      502: "模型或預覽連線暫時中斷，請稍後重試。",
      504: "模型回覆逾時，請稍後重試。"
    })[e.status] || (e.status ? "這題沒有完成，請稍後重試。" : "連線中斷，請確認網路或服務已啟動。")) +
      " 這裡的助理只有查詢與草稿工具，不會代送預約或 LINE。";
  }
  function signature(call) {
    return call.name + "(" + JSON.stringify(call.arguments) + ")";
  }
  function card(call) {
    var labels = {
      rank_customers_by_spend: "消費排行", list_inactive_customers: "久未回訪的客人",
      search_customer_segment: "篩選客人", get_customer_history: "客人消費紀錄",
      list_recent_conversations: "近期對話", get_conversation_transcript: "讀取對話內容",
      get_retention_watchlist: "回訪關心名單", get_service_metrics: "項目統計",
      draft_follow_up_message: "準備訊息草稿",
      propose_booking: "整理成一筆排單", propose_service_price: "整理成一筆價目修改"
    };
    var wrap = n("div", "toolcard fade"),
      state = n("div", "state", "正在查…"), parameters = n("div", "tool-details");
    parameters.append(n("div", "call", signature(call)));
    wrap.append(n("b", "tool-title", labels[call.name] || call.name), state, parameters);
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
    wrap.append(n("div", "cap", "回訪草稿 · 尚未送出"), answer(reply));
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
  /* 確認卡：模型講、程式驗、人按同意才動。
   *
   * 這張卡上的每一格都來自伺服器算好的提案（call.proposal），畫面自己不推算任何
   * 欄位。唯一的例外是工時與價格：那兩格以**工作台目前的設定**為準，因為等一下
   * 真的寫進去時用的就是那份設定——卡片答應的事必須跟寫進去的一樣。
   *
   * 寫入只有一條路：confirmProposal 裡的 S.mutate（就是排單表單在用的那一支，
   * 打既有的 POST /api/workbench/actions）。聊天的送出流程一個字都不寫。
   */
  var MISSING_LABELS = {
    customer: "客人",
    start: "日期與時間",
    service: "項目",
    price_twd: "價格",
    duration_minutes: "工時",
  };
  function liveService(id) {
    var settings = S.state && S.state.settings;
    return (
      (settings &&
        settings.services.find(function (x) {
          return x.id === id;
        })) ||
      null
    );
  }
  function liveDuration(id, fallback) {
    var settings = S.state && S.state.settings,
      live = liveService(id);
    // 設定成「每位一樣久」時，排進去的就是那個固定值——卡片不能還在報項目工時。
    if (settings && settings.duration_mode === "fixed") return settings.fixed_duration;
    return live ? live.duration : fallback;
  }
  function money(value) {
    return value == null ? "未設定" : "NT$" + Number(value).toLocaleString("zh-TW");
  }
  function writePayload(proposal) {
    var act = proposal.action;
    if (act.merge !== "service") return { kind: act.kind, data: act.data };
    // settings 端點收的是整份設定，所以在這裡把要改的那一格併進畫面上這份現行
    // 設定再送出——用伺服器那邊的預設值湊一份完整 payload，會把設計師剛剛改過的
    // 其他設定一起洗掉。
    var draft = JSON.parse(JSON.stringify(S.state.settings)),
      wanted = act.data.service,
      target = draft.services.find(function (x) {
        return x.id === wanted.id;
      });
    if (!target) throw new Error("項目表上已經沒有這個項目了，請重新選一次。");
    if (wanted.duration != null) target.duration = wanted.duration;
    if (wanted.price != null) target.price = wanted.price;
    return { kind: "settings", data: draft };
  }
  async function confirmProposal(proposal, state, bar) {
    bar.remove();
    state.textContent = "寫入中…";
    try {
      var payload = writePayload(proposal);
      await S.mutate(payload.kind, payload.data);
      state.textContent =
        (proposal.kind === "book" ? "已排入 " : "已更新 ") + proposal.summary;
      state.className = "cap done";
    } catch (e) {
      // 伺服器說不行就照它的話寫（正式唯讀是 403「正式資料只供唯讀…」）。
      // 這裡不准自己講成功，也不准把錯誤吞掉。
      state.textContent = "未寫入：" + e.message;
      state.className = "cap failed";
      bar.hidden = false;
      state.after(bar);
    }
  }
  function proposal(call) {
    var p = call.proposal,
      wrap = n("div", "proposal"),
      live = p.fields.service_id ? liveService(p.fields.service_id) : null,
      duration = p.fields.service_id
        ? liveDuration(p.fields.service_id, p.fields.duration_minutes)
        : p.fields.duration_minutes,
      price = live && p.fields.price_twd == null ? live.price : p.fields.price_twd,
      rows =
        p.kind === "book"
          ? [
              ["客人", p.fields.customer_label],
              ["時間", p.fields.date && p.fields.time
                ? p.fields.date + " " + p.fields.time : null],
              ["項目", p.fields.service_label],
              ["工時", duration == null ? null : duration + " 分鐘"],
              ["標價", p.fields.service_id ? money(price) : null],
            ]
          : [
              ["項目", p.fields.service_label],
              ["工時", p.fields.duration_minutes == null
                ? "不變" : p.fields.duration_minutes + " 分鐘"],
              ["價格", p.fields.price_twd == null ? "不變" : money(p.fields.price_twd)],
            ],
      state = n("div", "cap", "助理整理的動作 · 尚未寫入"),
      list = n("dl", "proposal-fields");
    wrap.append(state);
    rows.forEach(function (pair) {
      var line = n("div", "proposal-row");
      line.append(n("dt", null, pair[0]), n("dd", pair[1] ? null : "gap", pair[1] || "還缺"));
      list.append(line);
    });
    wrap.append(list);
    // 「這個項目還沒設定價格」只有在畫面上那份設定也真的沒填時才成立——
    // 伺服器查的是開場那份項目表，設計師剛剛在設定頁填過就不算數了。
    // 有缺欄位時不畫這一句：下面那塊已經把 note 一起講掉了，講兩次反而看不出重點。
    if (
      !p.missing.length &&
      p.unresolved &&
      p.unresolved.indexOf("price_twd") >= 0 &&
      price == null &&
      p.note
    )
      wrap.append(n("p", "note", p.note));
    if (p.missing.length) {
      wrap.append(
        n(
          "p",
          "warning",
          "還缺 " +
            p.missing
              .map(function (x) {
                return MISSING_LABELS[x] || x;
              })
              .join("、") +
            "，直接回我補上。" +
            (p.note ? " " + p.note : ""),
        ),
      );
      return wrap;
    }
    var bar = n("div", "actions");
    bar.append(
      b("取消", function () {
        bar.remove();
        state.textContent = "已取消，沒有排入。";
        state.className = "cap failed";
      }, "secondary"),
      b(p.kind === "book" ? "確認排入" : "確認改設定", function () {
        confirmProposal(p, state, bar);
      }, "primary"),
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
      it.elapsed = n("small", "waiting-label");
      it.waiting.append(it.elapsed);
      it.wrap.append(it.waiting);
    } else if (!m.pending && it.waiting) {
      it.waiting.remove();
      it.waiting = null;
    }
    if (m.pending && it.elapsed) it.elapsed.textContent = "處理中 · " + (m.elapsed || 0) + " 秒";
    if (m.error) {
      if (!it.tail) {
        it.tail = n("div", "error", m.text);
        it.wrap.append(it.tail);
        if (m.retryable !== false) it.wrap.append(
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
    if (!it.grown) it.grown = {};
    calls.forEach(function (call, i) {
      if (!call.tool_proposal || it.grown[i] || i >= (m.done || 0)) return;
      it.grown[i] = true;
      it.wrap.append(toolProposal(call.tool_proposal));
    });
    if (m.answered && !it.tail) {
      it.tail = calls.some(function (x) {
        return x.name === DRAFT_TOOL;
      })
        ? draft(m.text)
        : answer(m.text);
      it.wrap.append(it.tail);
      // 提案卡跟在答案後面：助理只負責說「我整理成這樣」，動手的是這張卡上的按鈕。
      calls
        .filter(function (x) {
          return x.proposal;
        })
        .forEach(function (x) {
          it.wrap.append(proposal(x));
        });
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
    var started = Date.now(), timer = setInterval(function () {
      if (version === generation && pending.pending) {
        pending.elapsed = Math.floor((Date.now() - started) / 1000);
        paint();
      }
    }, 1000);
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
        text: failureText(e),
        retryable: e.retryable !== false && ![401, 410].includes(e.status),
        question: text,
      });
    } finally {
      clearInterval(timer);
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
  /* --- 助理當場寫的那一支工具：看得到程式碼，按了才算數 -------------------
   *
   * 卡片上非有程式碼不可。沙盒擋得住「這支工具會不會弄壞東西」，擋不住
   * 「這支工具算得對不對」——算式對不對只有設計師本人看得出來，所以採用是一個
   * 人的動作，不是模型自己完成的一步。伺服器回的 status 就照著畫：
   * ok 才有那顆「採用」，被拒或出錯只講發生什麼事，不給按。
   */
  function proposalRows(rows) {
    var scroller = n("div", "answer-table"),
      table = n("table"),
      head = n("thead"),
      body = n("tbody"),
      shown = rows.slice(0, 10),
      columns = Object.keys(shown[0]),
      heading = n("tr");
    columns.forEach(function (key) {
      heading.append(n("th", null, key));
    });
    head.append(heading);
    shown.forEach(function (row) {
      var line = n("tr");
      columns.forEach(function (key) {
        var cell = row[key];
        line.append(n("td", null, cell === null || cell === undefined ? "—" : String(cell)));
      });
      body.append(line);
    });
    table.append(head, body);
    scroller.append(table);
    return scroller;
  }
  function proposalResult(p) {
    var rows = p.rows;
    if (Array.isArray(rows) && rows.length && typeof rows[0] === "object" && rows[0] !== null)
      return proposalRows(rows);
    var pre = n("pre");
    pre.append(n("code", null, p.result_preview || "（沒有結果）"));
    return pre;
  }
  function toolProposal(p) {
    var wrap = n("div", "proposal grown-tool"),
      code = n("details"),
      pre = n("pre"),
      status = n("div", "state");
    wrap.append(
      n("div", "cap", "助理當場寫了一支工具 · 尚未採用"),
      n("b", "tool-title", p.name),
      n("div", "desc", p.description),
    );
    code.append(n("summary", null, "看它寫了什麼"));
    pre.append(n("code", null, p.code));
    code.append(pre);
    wrap.append(code);

    if (p.status === "ok") {
      wrap.append(proposalResult(p));
      // 講畫面上看得到的那個數字。`row_count` 算的是 run() 回的那個東西有幾格——
      // 模型回一個包了四個鍵的 dict 時，那是 4，但表上是 7 列，兩個數字對不起來。
      status.textContent =
        (p.rows && p.rows.length ? "在沙盒跑出 " + p.rows.length + " 列" : "在沙盒跑完了") +
        (p.truncated ? "（已截斷）" : "") +
        "。這支工具還沒有加進來，要不要採用由你決定。";
      wrap.append(status);
      var bar = n("div", "actions"),
        yes = b("採用", null, "primary"),
        no = b("不要", null, "secondary");
      no.addEventListener("click", function () {
        bar.replaceWith(n("div", "state", "沒有採用，這支工具就到這裡。"));
      });
      yes.addEventListener("click", function () {
        yes.disabled = true;
        no.disabled = true;
        adoptProposal(p, bar);
      });
      bar.append(no, yes);
      wrap.append(bar);
    } else {
      // 被拒或出錯：講伺服器說的那句，不要自己翻譯成「暫時無法使用」。
      status.textContent =
        (p.status === "rejected" ? "沙盒拒絕了這段程式碼：" : "這支工具跑出錯誤：") +
        ((p.error && p.error.message) || "沒有說明");
      wrap.append(status);
      var lines = (p.error && p.error.violations) || [];
      if (lines.length) {
        var list = n("ul");
        lines.slice(0, 6).forEach(function (one) {
          list.append(n("li", null, "第 " + one.line + " 行：" + one.detail));
        });
        wrap.append(list);
      }
    }
    return wrap;
  }
  async function adoptProposal(p, bar) {
    try {
      await g.AssistantApi.adoptTool(p.proposal_id);
      bar.replaceWith(
        n("div", "state adopted", "已加入這次對話的工具：" + p.name +
          "。接著問就能直接用它；重新整理或重啟服務就沒了。"),
      );
    } catch (e) {
      // 伺服器說不行（正式唯讀、提案過期、撞名）就照它的話講。
      bar.replaceWith(n("div", "error", "沒有加入：" + e.message));
    }
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
