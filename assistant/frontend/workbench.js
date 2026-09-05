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

  function bookings() {
    S.open("預約", function (host) {
      var tabs = n("div", "booking-tabs"), content = n("div"), version = 0;
      tabs.setAttribute("role", "tablist");
      tabs.setAttribute("aria-label", "預約工作");
      host.append(tabs, content);
      function show(kind, selected) {
        version++;
        var turn = version;
        Array.from(tabs.children).forEach(function (x) { x.setAttribute("aria-selected", x === selected); });
        content.replaceChildren();
        if (kind === "records") bookingRecords(content);
        else conversationList(content, function () { return turn === version; }).catch(function (e) {
          if (turn === version) S.fail(content, e);
        });
      }
      [["conversations", "客人對話"], ["records", "預約紀錄"]].forEach(function (item) {
        var tab = b(item[1], function () { show(item[0], tab); });
        tab.setAttribute("role", "tab"); tabs.append(tab);
      });
      show("conversations", tabs.firstChild);
    });
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
      host.append(
        chips,
        b(
          "＋ 排一筆",
          function () {
            add({});
          },
          "primary full",
        ),
        list,
      );
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
        host.append(
          b(
            "看他的檔案",
            function () {
              profile({ ref: x.customer_ref });
            },
            "primary full",
          ),
        );
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
                "模擬馬上同步",
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
      note(host, "套票、回訪節奏：資料來源尚未提供，不以示範數字冒充。");
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
        warning(host);
        var existing = args.booking || null,
          block = args.block || null,
          selected =
            args.customer || (existing && customer(existing.customer_ref)),
          picked = existing ? existing.services.slice() : [],
          mode = block ? "block" : args.mode || "book";
        var intro = n("div");
        host.append(intro);
        if (!args.customer && !existing && !block) {
          var tabs = n("div", "chips");
          [
            ["book", "排預約"],
            ["block", "不接客"],
          ].forEach(function (p) {
            var t = b(
              p[1],
              function () {
                mode = p[0];
                paint();
              },
              "chip",
            );
            tabs.append(t);
          });
          intro.append(tabs);
        }
        var content = n("div");
        host.append(content);
        var chosenDate = existing
            ? existing.date
            : block
              ? block.date
              : args.date || S.state.days[0].date,
          chosenTime = existing
            ? existing.time
            : block
              ? block.start
              : args.time || S.state.settings.open_time;
        function paint() {
          content.replaceChildren();
          if (intro.firstChild)
            Array.from(intro.firstChild.children).forEach(function (t, i) {
              t.setAttribute("aria-pressed", (i === 0) === (mode === "book"));
            });
          var date = f("日期", "date", chosenDate),
            time = f(
              mode === "block" ? "開始時間" : "時間",
              "time",
              chosenTime,
            ),
            end = f(
              "結束時間",
              "time",
              block ? block.end : clock(Math.min(mins(chosenTime) + 60, 1200)),
            );
          dates(date.input);
          date.input.required = time.input.required = true;
          date.input.addEventListener("change", function () {
            chosenDate = date.input.value;
          });
          time.input.addEventListener("change", function () {
            chosenTime = time.input.value;
          });
          var frm = form(content, function () {
            if (mode === "book" && (!selected || !picked.length)) {
              S.fail(frm, new Error("請先選客人及服務項目。"));
              return;
            }
            var data =
              mode === "block"
                ? {
                    date: date.input.value,
                    start: time.input.value,
                    end: end.input.value,
                  }
                : {
                    date: date.input.value,
                    time: time.input.value,
                    customer_ref: selected.customer_ref,
                    services: picked.slice(),
                  };
            if (existing) data.id = existing.id;
            if (block) data.id = block.id;
            S.confirm(
              "確認這次示範",
              date.input.value +
                " " +
                time.input.value +
                " · " +
                (mode === "block"
                  ? "不接客"
                  : selected.masked_name +
                    "／" +
                    picked.map(serviceLabel).join("＋")),
              async function () {
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
                content.replaceChildren(
                  n("div", "card", "已存入本次示範班表。沒有真的送出 POS。"),
                  b(
                    "查看班表",
                    function () {
                      schedule({ date: date.input.value });
                    },
                    "primary full",
                  ),
                );
                if (result.booking)
                  content.append(
                    b(
                      "查看這筆預約",
                      function () {
                        booking({ id: result.booking.id });
                      },
                      "text-button full",
                    ),
                  );
              },
            );
          });
          if (mode === "book") {
            if (selected) {
              var card = n("div", "card");
              card.append(
                n("h3", null, selected.masked_name),
                n("p", "muted", "電話末四碼 " + selected.phone_last4),
                b(
                  "換人",
                  function () {
                    selected = null;
                    paint();
                  },
                  "text-button",
                ),
              );
              frm.append(card);
              note(frm, "資料已帶入，不用再打一次。");
            } else {
              var search = f("舊客 · 查姓名或電話末四碼", "search"),
                results = n("div", "card");
              frm.append(search.wrap, results);
              function find() {
                results.replaceChildren();
                var list = S.state.customers
                  .filter(function (c) {
                    return (c.masked_name + (c.phone_last4 || "")).includes(
                      search.input.value,
                    );
                  })
                  .slice(0, 8);
                list.forEach(function (c) {
                  results.append(
                    row(c.masked_name, "末四碼 " + c.phone_last4, function () {
                      selected = c;
                      // 有上次服務才帶出來；沒有就讓設計師自己選，不猜「剪髮」。
                      if (!picked.length && c.last_service)
                        picked = [c.last_service];
                      paint();
                    }),
                  );
                });
                if (!list.length) note(results, "查無資料，可新增示範客人。");
              }
              search.input.addEventListener("input", find);
              find();
              frm.append(
                b(
                  "新客 · 新增示範資料",
                  function () {
                    newCustomer(function (c) {
                      // 新客沒有上次服務，一樣不預選；送出時表單會擋沒選項目。
                      selected = c;
                      paint();
                    });
                  },
                  "text-button full",
                ),
              );
            }
            frm.append(n("h3", null, "項目（可複選）"));
            choices(frm, S.state.settings.services, picked, function (id) {
              picked = picked.includes(id)
                ? picked.filter(function (x) {
                    return x !== id;
                  })
                : picked.concat(id);
              paint();
            });
            var duration =
              S.state.settings.duration_mode === "fixed"
                ? S.state.settings.fixed_duration
                : picked.reduce(function (sum, id) {
                    var x = S.state.settings.services.find(function (v) {
                      return v.id === id;
                    });
                    return sum + (x ? x.duration : 0);
                  }, 0);
            note(frm, "本次安排 " + duration + " 分鐘，依示範設定計算。");
          }
          var fields = n("div", "inline-fields");
          fields.append(date.wrap, time.wrap);
          frm.append(fields);
          if (mode === "block") {
            end.input.required = true;
            frm.append(end.wrap);
          }
          var save = submit(
            mode === "block" ? "這段不接客（示範）" : "排進班表（示範）",
          );
          save.disabled = S.state.read_only;
          frm.append(save);
        }
        paint();
      },
      true,
    );
  }

  function newCustomer(done) {
    S.open(
      "新增示範客人",
      function (host) {
        note(host, "只用來演練，不會建立 POS 客編。請勿輸入真實個資。");
        var name = f("示範姓名", "text"),
          phone = f("電話末四碼", "text");
        name.input.required = phone.input.required = true;
        name.input.maxLength = 30;
        phone.input.pattern = "[0-9]{4}";
        phone.input.maxLength = 4;
        phone.input.inputMode = "numeric";
        var frm = form(host, async function () {
          var btn = frm.querySelector("button");
          btn.disabled = true;
          try {
            var result = await S.mutate("customer", {
              name: name.input.value,
              phone_last4: phone.input.value,
            });
            S.close();
            done(result.customer);
          } catch (e) {
            S.fail(frm, e);
          } finally {
            btn.disabled = false;
          }
        });
        frm.append(name.wrap, phone.wrap, submit("新增並帶入"));
      },
      true,
    );
  }

  function schedule(args) {
    S.open("班表", function (host) {
      var index = Math.max(
          0,
          S.state.days.findIndex(function (d) {
            return d.date === args.date;
          }),
        ),
        nav = n("div", "row between"),
        strip = n("div", "date-strip"),
        area = n("div"),
        jump = f("跳到日期", "date");
      strip.dataset.tour = "dates";
      dates(jump.input);
      nav.append(
        b(
          "‹ 前 7 天",
          function () {
            select(Math.max(0, index - 7));
          },
          "text-button",
        ),
        b(
          "回今天",
          function () {
            select(0);
          },
          "text-button",
        ),
        b(
          "後 7 天 ›",
          function () {
            select(Math.min(S.state.days.length - 1, index + 7));
          },
          "text-button",
        ),
      );
      host.append(nav, jump.wrap, strip, area);
      jump.input.addEventListener("change", function () {
        var i = S.state.days.findIndex(function (d) {
          return d.date === jump.input.value;
        });
        if (i >= 0) select(i);
      });
      function select(i) {
        index = i;
        draw();
        var target = strip.children[index];
        if (target)
          target.scrollIntoView({
            block: "nearest",
            inline: "center",
            behavior: "smooth",
          });
      }
      function draw() {
        strip.replaceChildren();
        S.state.days.forEach(function (d, i) {
          var item = b(
            "",
            function () {
              select(i);
            },
            "date-chip",
          );
          item.setAttribute("aria-label", d.date + " 週" + d.weekday);
          item.setAttribute("aria-pressed", i === index);
          item.append(
            n("small", null, "週" + d.weekday),
            n("b", null, Number(d.date.slice(-2))),
            n("small", null, d.slots.length || "·"),
          );
          strip.append(item);
        });
        area.replaceChildren();
        var day = S.state.days[index];
        jump.input.value = day.date;
        area.append(
          n("h3", null, day.date + " 週" + day.weekday),
          n(
            "p",
            "note",
            "示範時間軸 · 空白只表示尚未排單，不代表正式 POS 可約。",
          ),
        );
        var slots = day.slots,
          opening = mins(S.state.settings.open_time),
          closing = mins(S.state.settings.close_time);
        slots.forEach(function (s) {
          opening = Math.min(opening, mins(s.start));
          closing = Math.max(closing, mins(s.end));
        });
        var board = n("div", "timeline"),
          scale = 1.06;
        board.style.height = (closing - opening) * scale + 22 + "px";
        for (var m = opening; m <= closing; m += 60) {
          var line = n("div", "hour");
          line.style.top = (m - opening) * scale + "px";
          line.append(n("span", null, clock(m)));
          board.append(line);
        }
        var cursor = opening;
        function gap(start, end) {
          if (end - start < 15) return;
          var free = b(
            "空 " + clock(start) + "–" + clock(end) + " · 排一筆",
            function () {
              add({ date: day.date, time: clock(start) });
            },
            "slot free",
          );
          free.style.top = (start - opening) * scale + "px";
          free.style.height = (end - start) * scale - 3 + "px";
          board.append(free);
        }
        slots.forEach(function (s) {
          var start = mins(s.start),
            end = mins(s.end);
          if (start > cursor) gap(cursor, start);
          cursor = Math.max(cursor, end);
          var slot = b(
            "",
            function () {
              if (s.kind === "booking") booking({ id: s.id });
              else blockDetail(s);
            },
            "slot " + (s.kind === "block" ? "block" : s.status),
          );
          slot.style.top = (start - opening) * scale + "px";
          slot.style.height = Math.max(24, (end - start) * scale - 3) + "px";
          slot.append(
            n("b", null, s.start + " · " + (s.masked_name || "不接客")),
            n("small", null, s.service_label || s.reason),
          );
          if (s.customer_ref && S.state.notes[s.customer_ref])
            slot.append(n("small", null, S.state.notes[s.customer_ref]));
          board.append(slot);
        });
        if (cursor < closing) gap(cursor, closing);
        area.append(
          board,
          b(
            "＋ 排一筆",
            function () {
              add({ date: day.date });
            },
            "primary full",
          ),
        );
        note(
          area,
          "客人可預約到 " + S.state.settings.open_through + "（示範）。",
        );
      }
      draw();
      host.append(
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
      data.rows.forEach(function (x) {
        host.append(
          row(
            x.masked_name || "客人",
            "更新 " + x.updated_at.replace("T", " ").slice(0, 16),
            function () {
              thread({ ref: x.conversation_ref, customer_ref: x.customer_ref });
            },
          ),
        );
      });
  }
  function thread(args) {
    S.open("對話內容", async function (host) {
      var response = await A.transcript(args.ref),
        data = response.result;
      warning(host);
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
      if (customer(args.customer_ref))
        controls.append(
          b(
            "幫他預約",
            function () {
              add({ customer: customer(args.customer_ref) });
            },
            "primary",
          ),
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
              n(
                "div",
                "speaker",
                m.role === "user"
                  ? "客人"
                  : m.role === "designer"
                    ? m.simulated
                      ? "你（僅演練，未送出）"
                      : "設計師"
                    : "預約小幫手",
              ),
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
