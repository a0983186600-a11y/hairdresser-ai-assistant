/* v10 views. View state is local; every saved rehearsal goes through AssistantApi. */
(function (g) {
  "use strict";
  var S = g.AssistantShell,
    A = g.AssistantApi,
    n = S.node,
    b = S.button,
    f = S.field;
  function note(host, text) {
    host.append(n("p", "note", text));
  }
  function warning(host) {
    host.append(
      n(
        "p",
        "warning",
        S.state.read_only
          ? "正式資料唯讀；下方班表是示範，不能更動。"
          : "示範演練 · 不會真的送出到 POS 或 LINE。",
      ),
    );
  }
  function mins(t) {
    var p = t.split(":");
    return Number(p[0]) * 60 + Number(p[1]);
  }
  function clock(m) {
    return (
      String(Math.floor(m / 60)).padStart(2, "0") +
      ":" +
      String(m % 60).padStart(2, "0")
    );
  }
  function customer(ref) {
    return S.state.customers.find(function (c) {
      return c.customer_ref === ref;
    });
  }
  function avatar(name) {
    return n("span", "avatar", (name || "客").slice(0, 1));
  }
  function row(name, subtitle, fn) {
    var x = b("", fn, "list-row"),
      txt = n("span", "grow");
    txt.append(n("b", null, name), n("div", "muted", subtitle));
    x.append(avatar(name), txt, n("span", "chevron", "›"));
    return x;
  }
  function status(v) {
    return n(
      "span",
      "pill " + v,
      v === "pending"
        ? "等同步（示範）"
        : v === "cancelled"
          ? "已取消（示範）"
          : "已同步（示範）",
    );
  }
  function dates(input) {
    input.min = S.state.days[0].date;
    input.max = S.state.days[S.state.days.length - 1].date;
  }
  function checkForm(form) {
    return form.reportValidity();
  }
  function form(host, submit) {
    var x = n("form");
    x.addEventListener("submit", function (e) {
      e.preventDefault();
      if (checkForm(x)) submit();
    });
    host.append(x);
    return x;
  }
  function submit(label) {
    var x = b(label, null, "primary full");
    x.type = "submit";
    return x;
  }
  function choices(host, items, picked, fn) {
    var wrap = n("div", "chips");
    items.forEach(function (x) {
      var btn = b(
        x.name,
        function () {
          fn(x.id);
        },
        "chip",
      );
      btn.setAttribute("aria-pressed", picked.includes(x.id));
      wrap.append(btn);
    });
    host.append(wrap);
    return wrap;
  }
  function view(name, args) {
    if (!S.state) {
      S.toast("資料還在讀取，請稍候再點一次。");
      return;
    }
    var fn = views[name];
    if (fn) fn(args || {});
    else if (g.WorkbenchSettings) g.WorkbenchSettings.open(name);
  }

  /* 點「預約」開的就是設計稿 §3 那張開單面板本身。
     客人對話住在右上角的 ✉「訊息」，預約紀錄住在班表底下——同一件事只出現一次。 */
  function bookings(args) {
    args = args || {};
    // 這一頁要打字（搜尋、日期、時間），助理球在這裡只會擋路——設計稿對需要
    // 打字的畫面就是不放球。
    S.open(
      "預約",
      function (host) {
        bookingForm(host, args);
      },
      true,
    );
  }
  /* 公司系統（POS）綁定狀態。資料裡沒有客編就是沒有：顯示陶土色「未綁 POS」，
     不替他補一個看起來合理的號碼。 */
  function posTag(c) {
    var id = c && c.pos_customer_id;
    return n("span", id ? "pos bound" : "pos unbound", id ? "POS " + id : "未綁 POS");
  }
  /* 上次做的項目——只帶他真的做過的。沒有到店紀錄就回空的，讓設計師自己選。
     這裡填任何預設值都是替他編一次到店紀錄（Tai 案 2026-07-25）。 */
  function lastServices(c) {
    if (!c) return [];
    if (c.last_services && c.last_services.length) return c.last_services.slice();
    if (c.last_service) return [c.last_service];
    return [];
  }
  function serviceById(id) {
    return S.state.settings.services.find(function (x) {
      return x.id === id;
    });
  }
  function totalDuration(picked) {
    if (S.state.settings.duration_mode === "fixed")
      return picked.length ? S.state.settings.fixed_duration : 0;
    return picked.reduce(function (sum, id) {
      var x = serviceById(id);
      return sum + (x ? x.duration : 0);
    }, 0);
  }
  function lastVisitLine(c) {
    var when = c.last_visit_label && c.last_visit_label !== "未到店"
      ? c.last_visit_label.slice(5).replace("-", "/")
      : null;
    return when
      ? "末四碼 " + (c.phone_last4 || "未提供") + " · 上次 " + when + " " + c.last_service_label
      : "末四碼 " + (c.phone_last4 || "未提供") + " · 尚無到店紀錄";
  }
  /* 開單面板本體。點「預約」把它攤在 sheet 上、從對話／客人檔案／班表空檔帶身分
     進來的也是同一份——規矩（不猜項目、工時依設定算、缺客人就擋）只寫這裡一次。 */
  function bookingForm(host, args) {
    var existing = args.booking || null,
      block = args.block || null,
      // 從對話、客人檔案、時段帶進來的：身分不用再打一次，也不長出「不接客」那個 tab。
      carried = !!(args.customer || existing),
      selected = args.customer || (existing && customer(existing.customer_ref)) || null,
      picked = existing ? existing.services.slice() : lastServices(selected),
      mode = block ? "block" : args.mode || "book",
      who = "old",
      chosenDate = existing ? existing.date : block ? block.date : args.date || S.state.days[0].date,
      chosenTime = existing
        ? existing.time
        : block
          ? block.start
          : args.time || S.state.settings.open_time,
      chosenEnd = block ? block.end : clock(Math.min(mins(chosenTime) + 60, 1200)),
      newName = "",
      newPhone = "";
    warning(host);
    var panel = n("div", "booking-panel");
    host.append(panel);

    function clientCard() {
      var card = n("div", "client-card"),
        who2 = n("div", "grow"),
        tail = n("div", "client-pos");
      who2.append(
        n("b", null, selected.masked_name),
        n("div", "client-phone", "末四碼 " + (selected.phone_last4 || "未提供")),
      );
      // 卡片上寫得比清單那一列白話：綁了就說綁了再給編號，沒綁就說同步時會建新檔。
      if (selected.pos_customer_id)
        tail.append(
          n("span", "pos bound", "POS 已綁定"),
          n("small", "pos-id", selected.pos_customer_id),
        );
      else
        tail.append(
          n("span", "pos unbound", "POS 未綁定"),
          n("small", null, "同步時會在公司系統建新檔"),
        );
      card.append(n("span", "avatar big", selected.masked_name.slice(0, 1)), who2, tail);
      var line = n("div", "client-note");
      line.append(
        n(
          "span",
          "muted",
          carried ? "資料是從對話帶過來的，不用再打一次。" : "查到了，不用再打一次。",
        ),
        b(
          "換人",
          function () {
            selected = null;
            picked = [];
            paint();
          },
          "link-button",
        ),
      );
      return [n("p", "kicker", "幫客人預約"), card, line];
    }

    function pickList(host2) {
      var search = f("打名字或電話…", "search"),
        results = n("div", "pick-list");
      search.input.placeholder = "打名字或電話…";
      search.wrap.classList.add("hide-label");
      host2.append(search.wrap, results);
      function find() {
        results.replaceChildren();
        var query = search.input.value.trim(),
          rows = S.state.customers
            .filter(function (c) {
              return (c.masked_name + (c.phone_last4 || "")).includes(query);
            })
            .slice(0, 6);
        rows.forEach(function (c) {
          var item = b(
              "",
              function () {
                selected = c;
                // 有上次紀錄才帶項目出來；沒有就讓設計師自己選，不猜「剪髮」。
                picked = lastServices(c);
                paint();
              },
              "pick-row",
            ),
            text = n("span", "grow");
          text.append(n("b", null, c.masked_name), n("small", null, lastVisitLine(c)));
          item.append(n("span", "avatar", c.masked_name.slice(0, 1)), text, posTag(c));
          results.append(item);
        });
        if (!rows.length)
          results.append(
            n("p", "empty", "查不到這個人。可以切到「新客 · 第一次來」建一筆示範資料。"),
          );
      }
      search.input.addEventListener("input", find);
      find();
      host2.append(
        b(
          "從公司系統查 →",
          function () {
            S.toast("示範環境沒有接公司系統，這裡查不到 POS 客編。");
          },
          "link-button block",
        ),
      );
    }

    function paint() {
      panel.replaceChildren();
      if (mode === "book" && selected) clientCard().forEach(function (x) { panel.append(x); });
      if (!carried && !block) {
        var tabs = n("div", "mode-tabs");
        tabs.setAttribute("role", "tablist");
        tabs.setAttribute("aria-label", "排預約或不接客");
        [
          ["book", "排預約"],
          ["block", "不接客"],
        ].forEach(function (pair) {
          var tab = b(pair[1], function () {
            mode = pair[0];
            paint();
          });
          tab.setAttribute("role", "tab");
          tab.setAttribute("aria-selected", String(mode === pair[0]));
          tabs.append(tab);
        });
        panel.append(tabs);
      }
      if (mode === "book" && !selected) {
        var whoTabs = n("div", "who-tabs");
        whoTabs.setAttribute("role", "tablist");
        whoTabs.setAttribute("aria-label", "舊客或新客");
        whoTabs.dataset.tour = "addwho";
        [
          ["old", "舊客 · 查一下就好"],
          ["new", "新客 · 第一次來"],
        ].forEach(function (pair) {
          var tab = b(pair[1], function () {
            who = pair[0];
            paint();
          });
          tab.setAttribute("role", "tab");
          tab.setAttribute("aria-selected", String(who === pair[0]));
          whoTabs.append(tab);
        });
        panel.append(whoTabs);
      }
      var frm = form(panel, send);
      if (mode === "book" && !selected) {
        if (who === "old") pickList(frm);
        else {
          var grid = n("div", "field-grid"),
            name = f("客人姓名", "text", newName),
            phone = f("電話末四碼", "text", newPhone);
          name.input.required = phone.input.required = true;
          name.input.maxLength = 30;
          name.input.placeholder = "怎麼稱呼";
          phone.input.pattern = "[0-9]{4}";
          phone.input.maxLength = 4;
          phone.input.inputMode = "numeric";
          phone.input.placeholder = "後四碼";
          name.input.addEventListener("input", function () {
            newName = name.input.value;
          });
          phone.input.addEventListener("input", function () {
            newPhone = phone.input.value;
          });
          grid.append(name.wrap, phone.wrap);
          frm.append(grid);
          note(frm, "新客同步時會在公司系統建新檔。示範不會真的建 POS 客編，也請勿填真實個資。");
        }
      }
      if (mode === "book") {
        var head = n("div", "section-head");
        head.append(n("span", null, "選擇項目"), n("small", null, "可複選"));
        frm.append(head);
        var chips = n("div", "service-chips"),
          durBox = n("div", "dur-box");
        S.state.settings.services.forEach(function (s) {
          var chip = b("", function () {
            picked = picked.includes(s.id)
              ? picked.filter(function (x) {
                  return x !== s.id;
                })
              : picked.concat(s.id);
            drawChips();
          }, "service-chip");
          chip.dataset.service = s.id;
          chip.append(n("span", null, s.name), n("small", null, s.duration + " 分"));
          chips.append(chip);
        });
        function drawChips() {
          Array.from(chips.children).forEach(function (chip) {
            chip.setAttribute("aria-pressed", String(picked.includes(chip.dataset.service)));
          });
          var total = totalDuration(picked);
          durBox.replaceChildren(
            n("b", null, total ? total + " 分" : "—"),
            n("small", null, total ? "依你設定的工時加總" : "還沒選項目"),
          );
        }
        frm.append(chips);
        var when = n("div", "field-grid"),
          date = f("日期", "date", chosenDate),
          time = f("時間", "time", chosenTime);
        dates(date.input);
        date.input.required = time.input.required = true;
        ["input", "change"].forEach(function (evt) {
          date.input.addEventListener(evt, function () {
            chosenDate = date.input.value;
          });
          time.input.addEventListener(evt, function () {
            chosenTime = time.input.value;
          });
        });
        when.append(date.wrap, time.wrap);
        var dur = n("div", "field dur-field");
        dur.append(n("span", "field-label", "總工時"), durBox);
        frm.append(when, dur);
        drawChips();
        var save = submit("排進班表（示範）");
        save.disabled = S.state.read_only;
        frm.append(save);
        note(frm, "排進去之後，今晚會自動同步進公司系統。（示範不會真的送出）");
      } else {
        var span = n("div", "field-grid"),
          bDate = f("日期", "date", chosenDate),
          bFrom = f("從", "time", chosenTime),
          bTo = f("到", "time", chosenEnd),
          reason = f("備註（可留空）", "text", "");
        dates(bDate.input);
        bDate.input.required = bFrom.input.required = bTo.input.required = true;
        reason.input.maxLength = 20;
        reason.input.placeholder = "吃飯、進修、休息…";
        ["input", "change"].forEach(function (evt) {
          bDate.input.addEventListener(evt, function () {
            chosenDate = bDate.input.value;
          });
          bFrom.input.addEventListener(evt, function () {
            chosenTime = bFrom.input.value;
          });
          bTo.input.addEventListener(evt, function () {
            chosenEnd = bTo.input.value;
          });
        });
        span.append(bFrom.wrap, bTo.wrap);
        frm.append(bDate.wrap, span, reason.wrap);
        frm.blockReason = reason.input;
        var stop = submit("這段不接客（示範）");
        stop.disabled = S.state.read_only;
        frm.append(stop);
      }

      function send() {
        if (mode === "book" && !selected && who === "old") {
          S.fail(frm, new Error("請先查一位客人，或切到「新客 · 第一次來」。"));
          return;
        }
        if (mode === "book" && !picked.length) {
          S.fail(frm, new Error("請先選服務項目；沒選就送出等於替客人決定做什麼。"));
          return;
        }
        var label =
          mode === "block"
            ? "不接客"
            : (selected ? selected.masked_name : newName) +
              "／" +
              picked.map(serviceLabel).join("＋");
        S.confirm(
          "確認這次示範",
          chosenDate + " " + chosenTime + " · " + label,
          async function () {
            var target = selected;
            if (mode === "book" && !target) {
              var made = await S.mutate("customer", { name: newName, phone_last4: newPhone });
              target = made.customer;
            }
            var data =
              mode === "block"
                ? {
                    date: chosenDate,
                    start: chosenTime,
                    end: chosenEnd,
                    reason: frm.blockReason ? frm.blockReason.value : "",
                  }
                : {
                    date: chosenDate,
                    time: chosenTime,
                    customer_ref: target.customer_ref,
                    services: picked.slice(),
                  };
            if (existing) data.id = existing.id;
            if (block) data.id = block.id;
            var result = await S.mutate(
              mode === "block"
                ? block
                  ? "update_block"
                  : "block"
                : existing
                  ? "update_booking"
                  : "book",
              data,
            );
            done(result);
          },
        );
      }
    }

    function done(result) {
      panel.replaceChildren(
        n("div", "card", "已存入本次示範班表。沒有真的送出 POS。"),
        b(
          "查看班表",
          function () {
            schedule({ date: chosenDate });
          },
          "primary full",
        ),
      );
      if (result.booking)
        panel.append(
          b(
            "查看這筆預約",
            function () {
              booking({ id: result.booking.id });
            },
            "text-button full",
          ),
        );
      if (args.back)
        panel.append(
          b(
            "回這段對話",
            function () {
              thread(args.back);
            },
            "text-button full",
          ),
        );
    }
    paint();
  }
  function bookingRecords(host) {
      warning(host);
      var search = f("找客人、服務或日期", "search"),
        filter = "all";
      host.append(search.wrap);
      var chips = n("div", "chips"),
        list = n("div");
      [
        ["all", "全部"],
        ["pending", "等同步"],
        ["confirmed", "已同步"],
        ["cancelled", "已取消"],
      ].forEach(function (pair) {
        var btn = b(
          pair[1],
          function () {
            filter = pair[0];
            Array.from(chips.children).forEach(function (x) {
              x.setAttribute("aria-pressed", x === btn);
            });
            draw();
          },
          "chip",
        );
        btn.setAttribute("aria-pressed", pair[0] === "all");
        chips.append(btn);
      });
      host.append(chips, list);
      function draw() {
        list.replaceChildren();
        var rows = S.state.bookings.filter(function (x) {
          return (
            (filter === "all" || x.status === filter) &&
            (x.masked_name + x.phone_last4 + x.date + x.service_label).includes(
              search.input.value.trim(),
            )
          );
        });
        rows.forEach(function (x) {
          var card = b(
            "",
            function () {
              booking({ id: x.id });
            },
            "card action",
          );
          card.append(
            n("div", "row between", x.date + "　" + x.time),
            n("h3", null, x.masked_name + " · " + x.service_label),
            status(x.status),
          );
          list.append(card);
        });
        if (!rows.length) list.append(n("p", "empty", "沒有符合的預約。"));
      }
      search.input.addEventListener("input", draw);
      host.append(
        b(
          "重新整理",
          async function () {
            await S.refresh();
            draw();
          },
          "text-button full",
        ),
      );
      draw();
  }

  function booking(args) {
    S.open("預約詳情", function (host) {
      function draw() {
        host.replaceChildren();
        var x = S.state.bookings.find(function (v) {
          return v.id === args.id;
        });
        if (!x) {
          note(host, "找不到這筆預約。");
          return;
        }
        warning(host);
        var card = n("div", "card");
        card.append(
          n("h2", null, x.date + " " + x.time),
          n("h3", null, x.masked_name + " · " + x.service_label),
          n(
            "p",
            "note",
            x.duration + " 分鐘 · 電話末四碼 " + (x.phone_last4 || "未提供"),
          ),
          status(x.status),
        );
        host.append(card);
        var pinned = S.state.notes[x.customer_ref];
        if (pinned) host.append(n("div", "card pinned", "釘選\n" + pinned));
        // 同步狀態講白話，不講「狀態」「欄位」：設計師只需要知道還要不要動手。
        if (x.status === "pending")
          note(host, "今晚會自動同步進公司系統；要提前就按一下。");
        else if (x.status === "confirmed")
          note(host, "已同步進公司系統，改動會即時跟過去。");
        if (x.status !== "cancelled") {
          var actions = n("div", "actions");
          actions.append(
            b("改時間", function () {
              add({ booking: x });
            }),
            b("改項目", function () {
              add({ booking: x });
            }),
            b(
              "取消預約",
              function () {
                S.confirm(
                  "取消這筆預約？",
                  x.date +
                    " " +
                    x.time +
                    " · " +
                    x.masked_name +
                    "。只取消本次示範。",
                  async function () {
                    await S.mutate("cancel_booking", { id: x.id });
                    draw();
                  },
                );
              },
              "danger",
            ),
          );
          host.append(actions);
          if (x.status === "pending")
            host.append(
              b(
                "馬上同步（示範）",
                function () {
                  S.confirm(
                    "模擬同步",
                    "只演練狀態改變，不會寫入公司系統。",
                    async function () {
                      await S.mutate("sync_booking", { id: x.id });
                      draw();
                    },
                  );
                },
                "primary full",
              ),
            );
        }
        host.append(
          b(
            "看他的檔案",
            function () {
              profile({ ref: x.customer_ref });
            },
            "text-button full",
          ),
        );
      }
      draw();
    });
  }

  function customers() {
    S.open("客人", function (host) {
      note(host, "示範名單 · 姓名遮罩、電話只留末四碼。");
      var search = f("找姓名或電話末四碼", "search"),
        list = n("div", "card");
      host.append(search.wrap, list);
      function draw() {
        list.replaceChildren();
        var rows = S.state.customers.filter(function (c) {
          return (c.masked_name + (c.phone_last4 || "")).includes(
            search.input.value.trim(),
          );
        });
        rows.forEach(function (c) {
          list.append(
            row(
              c.masked_name,
              "末四碼 " +
                (c.phone_last4 || "未提供") +
                " · " +
                c.visit_count +
                " 次到店",
              function () {
                profile({ ref: c.customer_ref });
              },
            ),
          );
        });
        if (!rows.length) list.append(n("p", "empty", "沒有符合的客人。"));
      }
      search.input.addEventListener("input", draw);
      draw();
    });
  }

  function profile(args) {
    S.open("客人檔案", async function (host) {
      var c = customer(args.ref);
      if (!c) {
        note(host, "這位客人不在本次示範名單內。");
        return;
      }
      var head = n("div", "row profile-head"),
        label = n("div");
      label.append(
        n("h2", null, c.masked_name),
        n("p", "muted", "電話末四碼 " + (c.phone_last4 || "未提供")),
      );
      head.append(avatar(c.masked_name), label);
      host.append(head);
      note(host, "示範檔案 · 未連線 POS，不代表已綁定客編。");
      var stats = n("div", "stats");
      [
        [c.visit_count, "到店次數"],
        [S.money(c.known_spend_twd), "已知消費"],
        [c.last_visit_label || "未到店", "上次來"],
      ].forEach(function (x) {
        var v = n("div");
        v.append(n("b", null, x[0]), n("small", null, x[1]));
        stats.append(v);
      });
      host.append(stats);
      var pin = n("div", "card"),
        content = n(
          "p",
          "pinned",
          S.state.notes[c.customer_ref] ||
            "還沒有釘選。示範資料未提供配方與過敏資訊，不自行猜測。",
        );
      pin.append(
        n("h3", null, "釘選重點"),
        content,
        b(
          "編輯釘選",
          function () {
            S.open(
              "釘選重點",
              function (box) {
                var text = f(
                  "只填示範備註，勿貼真實個資",
                  "textarea",
                  S.state.notes[c.customer_ref] || "",
                );
                box.append(
                  text.wrap,
                  b(
                    "儲存示範釘選",
                    async function () {
                      try {
                        await S.mutate("note", {
                          customer_ref: c.customer_ref,
                          text: text.input.value,
                        });
                        content.textContent = text.input.value;
                        S.close();
                      } catch (e) {
                        S.fail(box, e);
                      }
                    },
                    "primary full",
                  ),
                );
              },
              true,
            );
          },
          "text-button",
        ),
      );
      host.append(pin);
      var example = S.state.presentation_examples && S.state.presentation_examples.profiles[c.customer_ref];
      if (example) {
        var details = n("section", "card profile-examples");
        details.append(n("p", "warning", "以下配方／套票／週期為虛構展示，不是客人的真實資料。"));
        details.append(n("h3", null, "示範釘選"), n("p", null, example.pinned));
        details.append(n("h3", null, "示範套票"), n("p", null, example.package.name),
          n("p", "muted", "剩 " + example.package.remaining + " 次 · 至 " + example.package.expires_on));
        details.append(n("h3", null, "回訪節奏"), n("p", null,
          "示範週期 " + example.cycle_days + " 天" + (example.days_since_visit === null ? "" :
            " · 距示範上次到店 " + example.days_since_visit + " 天")));
        host.append(details);
      } else note(host, "套票、回訪節奏：尚無資料，不自行猜測。");
      var buttons = n("div", "actions");
      buttons.append(
        b(
          "幫他預約",
          function () {
            add({ customer: c });
          },
          "primary",
        ),
        b("擬回訪訊息", function () {
          draft(c.customer_ref);
        }),
      );
      host.append(buttons);
      var visits = n("div", "card");
      visits.append(n("h3", null, "消費紀錄"));
      note(visits, "示範消費紀錄 · 尚未同步 POS，以下不是客人的真實消費。 ");
      host.append(visits);
      if (c.customer_ref.startsWith("demo-customer-")) {
        note(visits, "這位是本次演練新增的客人，尚無到店紀錄。");
        return;
      }
      if (S.health.mode !== "demo") {
        note(
          visits,
          "目前助理已切換正式唯讀，不能將示範客人的識別碼交叉查正式資料。",
        );
        return;
      }
      try {
        var response = await A.customer(c.customer_ref),
          history = response.result;
        history.visits.forEach(function (v) {
          var line = n("div", "list-row"),
            desc = n("div", "grow");
          desc.append(
            n("b", null, serviceLabel(v.service)),
            n("div", "muted", v.visited_at.slice(0, 10)),
          );
          line.append(desc, n("b", null, S.money(v.amount_twd)));
          visits.append(line);
        });
        if (!history.visits.length) note(visits, "沒有到店紀錄。");
        note(
          visits,
          "缺金額 " +
            history.unknown_amount_visits +
            " 筆，已知消費不是完整營收。",
        );
      } catch (e) {
        S.fail(visits, e);
      }
    });
  }
  function serviceLabel(id) {
    var s = S.state.settings.services.find(function (x) {
      return x.id === id;
    });
    return s ? s.name : id;
  }
  function draft(ref) {
    S.open("回訪訊息草稿", async function (host) {
      if (S.health.mode !== "demo") {
        note(host, "請回到示範模式，再對示範客人擬稿。");
        return;
      }
      var result = await A.draft(ref);
      host.append(
        n("p", "bubble", result.result.text),
        n("p", "warning", "這是草稿，不會直接送給客人。"),
        b(
          "複製草稿",
          function () {
            S.copy(result.result.text).catch(function (e) {
              S.fail(host, e);
            });
          },
          "primary full",
        ),
      );
    });
  }

  function add(args) {
    S.open(
      args.booking ? "調整預約" : "排一筆",
      function (host) {
        bookingForm(host, args);
      },
      true,
    );
  }
  /* 班表＝設計稿 §2：一週一頁、一天一畫面、時間比例時間軸。
     卡片高度就是實際時長（1.06 px/分），空檔按同一把尺留白並且可以點。 */
  function schedule(args) {
    args = args || {};
    S.open("班表", function (host) {
      var SCALE = 1.06,
        index = Math.max(
          0,
          S.state.days.findIndex(function (d) {
            return d.date === args.date;
          }),
        ),
        nav = n("div", "spine-nav"),
        jumpRow = n("div", "spine-jump"),
        strip = n("div", "date-strip"),
        area = n("div", "spine"),
        jump = f("跳到日期", "date"),
        month = n("span", "spine-month"),
        pills = [];
      strip.dataset.tour = "dates";
      dates(jump.input);
      var prev = b(
          "‹ 前 7 天",
          function () {
            select(Math.max(0, index - 7), true);
          },
          "link-button",
        ),
        next = b(
          "後 7 天 ›",
          function () {
            select(Math.min(S.state.days.length - 1, index + 7), true);
          },
          "link-button",
        );
      nav.append(prev, next, month);
      jumpRow.append(
        jump.wrap,
        b(
          "回今天",
          function () {
            select(0);
          },
          "link-button",
        ),
      );
      host.append(nav, jumpRow, strip, n("p", "strip-hint", "左右滑可以看更後面的日期"), area);
      jump.input.addEventListener("change", function () {
        var i = S.state.days.findIndex(function (d) {
          return d.date === jump.input.value;
        });
        if (i >= 0) select(i);
        else S.toast("只能看到 " + S.state.days[S.state.days.length - 1].date + " 為止。");
      });
      S.state.days.forEach(function (d, i) {
        var mm = d.date.slice(5, 7);
        if (i === 0 || mm !== S.state.days[i - 1].date.slice(5, 7))
          strip.append(n("span", "month-mark", Number(mm) + " 月"));
        var item = b(
          "",
          function () {
            select(i);
          },
          "date-chip",
        );
        item.setAttribute("aria-label", d.date + " 週" + d.weekday);
        item.append(
          n("small", null, "週" + d.weekday),
          n("b", null, Number(d.date.slice(-2))),
          n("small", "count", "·"),
        );
        pills.push(item);
        strip.append(item);
      });
      // 月份標籤也佔寬度，所以置中要用實測位置算，不能拿索引乘膠囊寬度。
      function center(smooth) {
        var el = pills[index];
        if (!el) return;
        strip.scrollTo({
          left: Math.max(0, el.offsetLeft - (strip.clientWidth - el.offsetWidth) / 2),
          behavior: smooth ? "smooth" : "auto",
        });
      }
      function select(i, smooth) {
        index = i;
        draw();
        center(smooth);
      }
      function summary(day, opening, closing) {
        var booked = day.slots.filter(function (s) {
            return s.kind === "booking";
          }).length,
          free = 0,
          cursor = opening;
        day.slots.forEach(function (s) {
          free += Math.max(0, mins(s.start) - cursor);
          cursor = Math.max(cursor, mins(s.end));
        });
        free += Math.max(0, closing - cursor);
        return booked
          ? booked + " 筆 · 空 " + Math.round(free / 6) / 10 + " 小時"
          : "整天空著";
      }
      function draw() {
        pills.forEach(function (item, i) {
          item.setAttribute("aria-pressed", String(i === index));
          item.querySelector(".count").textContent = S.state.days[i].slots.length || "·";
        });
        var day = S.state.days[index];
        jump.input.value = day.date;
        month.textContent = Number(day.date.slice(5, 7)) + " 月";
        prev.disabled = index === 0;
        next.disabled = index >= S.state.days.length - 1;
        area.replaceChildren();
        var opening = mins(S.state.settings.open_time),
          closing = mins(S.state.settings.close_time);
        day.slots.forEach(function (s) {
          opening = Math.min(opening, mins(s.start));
          closing = Math.max(closing, mins(s.end));
        });
        var head = n("div", "spine-head");
        head.append(
          n("b", null, day.date.slice(5).replace("-", "/")),
          n(
            "span",
            "spine-when",
            index === 0 ? "今天" : index === 1 ? "明天" : "週" + day.weekday,
          ),
          n("i", "rule"),
          n("small", null, summary(day, opening, closing)),
        );
        area.append(head);
        if (opening < mins(S.state.settings.open_time) || closing > mins(S.state.settings.close_time))
          note(area, "部分既有示範單在目前營業時間之外，保留顯示；新單仍依目前營業時間檢查。");
        // 今天才有「現在」。示範是釘住的基準時刻，不是你手機上的現在。
        var nowLine = null;
        if (index === 0) {
          var nowMin = mins(S.state.as_of.slice(11, 16));
          nowLine = n("div", "now-line");
          nowLine.append(
            n("span", "now-clock", S.state.as_of.slice(11, 16)),
            n("i", null),
            n("span", "now-label", "現在（示範基準）"),
          );
          nowLine.dataset.at = nowMin;
        }
        var seq = [],
          cursor = opening;
        day.slots
          .slice()
          .sort(function (a, c) {
            return mins(a.start) - mins(c.start);
          })
          .forEach(function (s) {
            var start = mins(s.start),
              end = mins(s.end);
            if (start - cursor >= 15) seq.push({ gap: true, start: cursor, end: start });
            seq.push({ slot: s, start: start, end: end });
            cursor = Math.max(cursor, end);
          });
        if (closing - cursor >= 15) seq.push({ gap: true, start: cursor, end: closing });
        seq.forEach(function (e) {
          if (nowLine && e.start >= Number(nowLine.dataset.at)) {
            area.append(nowLine);
            nowLine = null;
          }
          if (e.gap) {
            var free = b(
              "",
              function () {
                add({ date: day.date, time: clock(e.start) });
              },
              "slot free",
            );
            free.style.minHeight = Math.max(46, Math.round((e.end - e.start) * SCALE)) + "px";
            free.append(
              n("span", "slot-time", clock(e.start)),
              n(
                "span",
                "grow",
                "空 " + clock(e.start) + "–" + clock(e.end) + " · 排一筆",
              ),
            );
            area.append(free);
            return;
          }
          var s = e.slot,
            duration = e.end - e.start,
            card = b(
              "",
              function () {
                if (s.kind === "booking") booking({ id: s.id });
                else blockDetail(s);
              },
              "slot " + (s.kind === "block" ? "block" : s.status),
            ),
            text = n("span", "grow");
          card.style.minHeight = Math.max(56, Math.round(duration * SCALE)) + "px";
          card.append(n("span", "slot-time", s.start));
          if (s.kind === "booking")
            card.append(n("span", "avatar small", (s.masked_name || "客").slice(0, 1)));
          text.append(
            n(
              "b",
              null,
              s.kind === "booking"
                ? s.masked_name
                : "不接客" + (s.reason && s.reason !== "不接客" ? " · " + s.reason : ""),
            ),
          );
          if (s.kind === "booking")
            text.append(n("small", null, s.service_label + " " + duration + " 分"));
          if (s.customer_ref && S.state.notes[s.customer_ref])
            text.append(n("small", "pin", S.state.notes[s.customer_ref]));
          card.append(text);
          if (s.kind === "booking")
            card.append(
              n("span", "sync " + s.status, s.status === "pending" ? "等同步" : "已同步"),
            );
          area.append(card);
        });
        if (nowLine) area.append(nowLine);
        area.append(
          b(
            "＋ 排一筆",
            function () {
              add({ date: day.date });
            },
            "primary full",
          ),
          n(
            "p",
            "spine-foot",
            "客人可預約到 " +
              S.state.settings.open_through.slice(5).replace("-", "/") +
              " · 之後的你自己排得到",
          ),
        );
        note(
          area,
          "示範基準日 " +
            S.state.as_of.slice(0, 10) +
            " · 已載入至 " +
            S.state.days[S.state.days.length - 1].date +
            "。空白只表示尚未排單，不是正式 POS 可約證明。",
        );
      }
      draw();
      center(false);
      host.append(
        b(
          "看所有預約紀錄",
          function () {
            S.open("預約紀錄", function (box) {
              bookingRecords(box);
            });
          },
          "text-button full",
        ),
        b(
          "重新整理班表",
          async function () {
            await S.refresh();
            draw();
          },
          "text-button full",
        ),
      );
    });
  }
  function blockDetail(x) {
    S.open("不接客", function (host) {
      host.append(
        n("div", "card", x.date + " " + x.start + "–" + x.end),
        b(
          "改時段",
          function () {
            add({ block: x });
          },
          "primary full",
        ),
        b(
          "取消不接客",
          function () {
            S.confirm(
              "取消不接客？",
              "只移除本次演練的區塊，正式班表不受影響。",
              async function () {
                await S.mutate("remove_block", { id: x.id });
                host.replaceChildren(n("p", "card", "已移除示範區塊。"));
              },
            );
          },
          "danger full",
        ),
      );
    });
  }

  function inbox() {
    S.open("客人訊息", async function (host) {
      await conversationList(host);
    });
  }
  async function conversationList(host, stillCurrent) {
      host.classList.add("conversation-list");
      note(
        host,
        S.health.mode === "demo"
          ? "以下是產生的示範對話。"
          : "以下由助理的正式唯讀資料來源查詢；不能在此接手或發送。",
      );
      var data = await A.conversations();
      if (stillCurrent && !stillCurrent()) return;
      if (!data.rows.length) {
        note(host, "目前沒有近期對話。");
        return;
      }
      var search = f("搜尋對話：姓名、末四碼或摘要", "search"),
        list = n("div"), count = n("p", "note"), shown = 8,
        more = b("載入更多對話", function () { shown += 8; draw(); }, "secondary full");
      search.input.maxLength = 120;
      host.append(search.wrap, count, list, more);
      function draw() {
        var query = search.input.value.trim().normalize("NFKC").toLocaleLowerCase();
        var matched = data.rows.filter(function (x) {
          var who = customer(x.customer_ref);
          return [x.masked_name, who && who.phone_last4,
            (x.preview || []).map(function (p) { return p.text; }).join(" ")]
            .join(" ").normalize("NFKC").toLocaleLowerCase().includes(query);
        });
        list.replaceChildren();
        matched.slice(0, shown).forEach(function (x) { list.append(conversationRow(x)); });
        count.textContent = "顯示 " + Math.min(shown, matched.length) + "／" + matched.length + " 段對話";
        more.hidden = shown >= matched.length;
        if (!matched.length) list.append(n("p", "empty", "沒有符合的對話。"));
      }
      search.input.addEventListener("input", function () { shown = 8; draw(); });
      draw();
  }
  function speaker(role, simulated) {
    return role === "user"
      ? "客人"
      : role === "designer"
        ? simulated
          ? "你（僅演練，未送出）"
          : "設計師"
        : "預約小幫手";
  }
  function stamp(iso) {
    return iso.slice(5, 10).replace("-", "/") + " " + iso.slice(11, 16);
  }
  function conversationRow(x) {
    // 一列要能認出是誰：遮罩姓名＋最近 1～2 句（客人講的那句在前）＋時間。
    // 那幾句是伺服器從逐字稿挑的，前端只負責顯示，不自己拼、不自己補。
    var item = b(
        "",
        function () {
          thread({ ref: x.conversation_ref, customer_ref: x.customer_ref });
        },
        "list-row",
      ),
      txt = n("span", "grow"),
      head = n("div", "row between"),
      lines = x.preview || [];
    head.append(
      n("b", null, x.masked_name || "客人"),
      n("small", "muted", stamp(x.updated_at)),
    );
    txt.append(head);
    lines.forEach(function (m) {
      txt.append(n("div", "preview", speaker(m.role) + "：" + m.text));
    });
    if (!lines.length)
      txt.append(n("div", "muted", "這段對話還沒有內容可以顯示。"));
    item.append(avatar(x.masked_name), txt, n("span", "chevron", "›"));
    return item;
  }
  /* 對不到客人的對話：由設計師自己指名要排給誰。系統不替他配一位——
     配錯人就是把別人的預約排到這個人身上，而畫面上看不出來。 */
  function pickCustomer(done) {
    S.open(
      "手動選一位客人",
      function (host) {
        note(host, "這段對話沒有對到名單上的客人；請你指定要排給誰，系統不會自己配。");
        var search = f("找姓名或電話末四碼", "search"),
          list = n("div", "card");
        host.append(search.wrap, list);
        function draw() {
          list.replaceChildren();
          var rows = S.state.customers
            .filter(function (c) {
              return (c.masked_name + (c.phone_last4 || "")).includes(
                search.input.value.trim(),
              );
            })
            .slice(0, 8);
          rows.forEach(function (c) {
            list.append(
              row(c.masked_name, "末四碼 " + (c.phone_last4 || "未提供"), function () {
                S.close();
                done(c);
              }),
            );
          });
          if (!rows.length) note(list, "查無資料。");
        }
        search.input.addEventListener("input", draw);
        draw();
      },
      true,
    );
  }
  function thread(args) {
    S.open("對話內容", async function (host) {
      var response = await A.transcript(args.ref),
        data = response.result,
        who = customer(args.customer_ref);
      warning(host);
      // 畫面上方先說清楚這是誰：認得出就寫遮罩姓名與末四碼，認不出就寫「未辨識」。
      var head = n("div", "card thread-who"),
        title = n("div", "row"),
        label = n("div", "grow");
      label.append(
        n("b", null, who ? who.masked_name : "未辨識"),
        n(
          "div",
          "muted",
          who
            ? "電話末四碼 " + (who.phone_last4 || "未提供")
            : "這段對話還沒對到名單上的客人。",
        ),
      );
      title.append(avatar(who ? who.masked_name : "？"), label);
      head.append(title);
      if (who)
        S.state.bookings
          .filter(function (x) {
            return x.customer_ref === who.customer_ref && x.status !== "cancelled";
          })
          .sort(function (a, c) {
            return (a.date + a.time).localeCompare(c.date + c.time);
          })
          .forEach(function (x) {
            head.append(
              n(
                "div",
                "booked",
                "已排 " +
                  x.date.slice(5).replace("-", "/") +
                  " " +
                  x.time +
                  " · " +
                  x.service_label,
              ),
            );
          });
      host.append(head);
      var controls = n("div", "actions"),
        takeover = b("", async function () {
          var on = !S.state.takeovers[args.ref];
          S.confirm(
            on ? "模擬接手對話" : "模擬交回助理",
            "只演練畫面狀態，不會暫停或恢復正式 LINE 的 AI。",
            async function () {
              await S.mutate("takeover", {
                conversation_ref: args.ref,
                enabled: on,
              });
              paint();
            },
          );
        });
      controls.append(takeover);
      // 排單一律回預約頁那張表單，帶著這位客人；剩下日期、時間、項目要填。
      var carry = { ref: args.ref, customer_ref: args.customer_ref };
      function toBookings(c) {
        S.closeAll();
        bookings({ customer: c, back: carry });
      }
      controls.append(
        who
          ? b(
              "幫他排一筆",
              function () {
                toBookings(who);
              },
              "primary",
            )
          : b("這段對話還沒對到客人", function () {
              pickCustomer(toBookings);
            }),
      );
      host.append(controls);
      var history = n("div", "thread");
      host.append(history);
      function paint() {
        takeover.textContent = S.state.takeovers[args.ref]
          ? "交回助理（示範）"
          : "接手對話（示範）";
        takeover.disabled = S.state.read_only;
        history.replaceChildren();
        data.messages
          .concat(S.state.messages[args.ref] || [])
          .forEach(function (m) {
            var wrap = n(
              "div",
              "turn " + (m.role === "designer" ? "mine" : "theirs"),
            );
            wrap.append(
              n("div", "speaker", speaker(m.role, m.simulated)),
              n("div", "bubble", m.redacted_content),
            );
            history.append(wrap);
          });
      }
      paint();
      var text = f("示範回覆（不會發到 LINE）", "textarea"),
        frm = form(host, function () {
          S.confirm(
            "模擬回覆",
            "這段只加入示範畫面，不會送給客人。",
            async function () {
              await S.mutate("message", {
                conversation_ref: args.ref,
                text: text.input.value,
              });
              text.input.value = "";
              paint();
            },
          );
        });
      text.input.required = true;
      text.input.maxLength = 2000;
      frm.append(text.wrap, submit("加入演練對話"));
    });
  }

  var views = {
    bookings: bookings,
    booking: booking,
    customers: customers,
    client: profile,
    add: add,
    schedule: schedule,
    inbox: inbox,
  };
  g.WorkbenchUI = {
    open: view,
    choices: choices,
    form: form,
    submit: submit,
    note: note,
    warning: warning,
    dates: dates,
  };
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-open]").forEach(function (x) {
      x.addEventListener("click", function () {
        view(x.dataset.open);
      });
    });
    document.querySelector("[data-home]").addEventListener("click", S.closeAll);
    document
      .querySelector("[data-tomorrow]")
      .addEventListener("click", function () {
        if (S.state)
          schedule({
            date: S.state.days[Math.min(1, S.state.days.length - 1)].date,
          });
      });
    var ball = document.querySelector("[data-assistant-ball]"),
      drag = null,
      moved = false;
    ball.addEventListener("pointerdown", function (e) {
      var rect = ball.getBoundingClientRect();
      drag = { x: e.clientX, y: e.clientY, top: rect.top, left: rect.left };
      moved = false;
      ball.setPointerCapture(e.pointerId);
    });
    ball.addEventListener("pointermove", function (e) {
      if (!drag) return;
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 6) moved = true;
      if (moved) {
        var bounds = document
          .querySelector(".workbench")
          .getBoundingClientRect();
        ball.style.top =
          Math.max(
            70,
            Math.min(innerHeight - 65, drag.top + e.clientY - drag.y),
          ) + "px";
        ball.style.left =
          Math.max(
            bounds.left + 8,
            Math.min(bounds.right - 60, drag.left + e.clientX - drag.x),
          ) + "px";
        ball.style.bottom = "auto";
        ball.style.right = "auto";
      }
    });
    ball.addEventListener("pointerup", function () {
      if (moved) {
        var bounds = document
            .querySelector(".workbench")
            .getBoundingClientRect(),
          rect = ball.getBoundingClientRect();
        ball.style.left =
          (rect.left + 26 < (bounds.left + bounds.right) / 2
            ? bounds.left + 8
            : bounds.right - 60) + "px";
      }
      drag = null;
    });
    ball.addEventListener("pointercancel", function () {
      drag = null;
    });
    ball.addEventListener("click", function () {
      if (!moved && g.AssistantChat) g.AssistantChat.open();
    });
    document.querySelector("[data-tour-start]").addEventListener("click", tour);
  });
  g.addEventListener("workbench-ready", function () {
    var initial = document.body.dataset.initialSheet;
    if (initial) view(initial);
  });
  function tour() {
    var step = 0,
      steps = [
        [
          "首頁就是跟助理說話",
          "查客人、回訪與消費；答案下方可以展開工具紀錄。",
          "chat",
        ],
        [
          "主要入口都在這裡",
          "預約、班表、設定在上方；客人和訊息也不用重打資料。",
          "nav",
        ],
        [
          "開單先查客人",
          "挑一位就會帶入資料。這是示範，不會真的建 POS 單。",
          "add",
        ],
        [
          "每天的時間一眼就懂",
          "左右滑選日期，點預約看詳情，點空白演練開單。",
          "dates",
        ],
        [
          "規則只設定一次",
          "工時、價目、預約排法共用同一份示範設定。",
          "settings",
        ],
      ];
    function draw() {
      var old = document.querySelector(".tour-shade");
      if (old) old.remove();
      S.closeAll();
      if (step === 2) add({});
      if (step === 3) schedule({});
      if (step === 4) g.WorkbenchSettings.open("settings");
      var shade = n("div", "tour-shade"),
        spot = n("div", "tour-spot"),
        card = n("div", "tour-card");
      shade.setAttribute("role", "dialog");
      shade.setAttribute("aria-label", "新手教學");
      card.append(
        n("p", "tour-progress", "新手教學 · " + (step + 1) + "／5"),
        n("h2", null, steps[step][0]),
        n("p", null, steps[step][1]),
      );
      var nav = n("div", "row between");
      nav.append(b("略過", end, "text-button"));
      if (step > 0)
        nav.append(
          b(
            "上一步",
            function () {
              step--;
              draw();
            },
            "text-button",
          ),
        );
      nav.append(
        b(
          step === 4 ? "開始使用" : "下一步",
          function () {
            if (step === 4) end();
            else {
              step++;
              draw();
            }
          },
          "primary",
        ),
      );
      card.append(nav);
      shade.append(spot, card);
      document.body.append(shade);
      var attempts = 0;
      function measure() {
        var target =
          document.querySelector('[data-tour="' + steps[step][2] + '"]') ||
          (step === 2 ? document.querySelector(".sheet-content") : null);
        if (!target && attempts++ < 90) {
          requestAnimationFrame(measure);
          return;
        }
        if (!target) return;
        var r = target.getBoundingClientRect();
        Object.assign(spot.style, {
          top: r.top - 8 + "px",
          left: r.left - 8 + "px",
          width: r.width + 16 + "px",
          height: Math.min(r.height + 16, innerHeight - 250) + "px",
        });
        if (r.top > innerHeight / 2) {
          card.style.top = "16px";
          card.style.bottom = "auto";
        }
      }
      requestAnimationFrame(measure);
    }
    function end() {
      document.querySelector(".tour-shade").remove();
      S.closeAll();
    }
    draw();
  }
  g.WorkbenchUI.tour = tour;
})(window);
