/* Settings edit one rehearsal model; booking forms read that same model. */
(function (g) {
  "use strict";
  var S = g.AssistantShell,
    U = g.WorkbenchUI,
    n = S.node,
    b = S.button,
    f = S.field;
  function clone() {
    return JSON.parse(JSON.stringify(S.state.settings));
  }
  function select(label, values, current) {
    var wrap = n("label", "field", label),
      input = n("select");
    values.forEach(function (x) {
      var o = n("option", null, x[1]);
      o.value = x[0];
      input.append(o);
    });
    input.value = current;
    wrap.append(input);
    return { wrap: wrap, input: input };
  }
  function check(label, value) {
    var x = f(label, "checkbox");
    x.input.checked = value;
    return x;
  }
  function save(host, draft, saved) {
    var btn = b(
      "儲存示範設定",
      async function () {
        btn.disabled = true;
        try {
          await S.mutate("settings", draft());
          if (saved) saved();
          S.toast("已儲存示範設定，開單工時會一起更新。");
        } catch (e) {
          S.fail(host, e);
        } finally {
          btn.disabled = S.state.read_only;
        }
      },
      "primary full",
    );
    btn.disabled = S.state.read_only;
    host.append(btn);
  }
  function menu() {
    S.open("設定", function (host) {
      host.dataset.tour = "settings";
      U.warning(host);
      [
        ["price", "項目、工時與價格", "工時會帶入開單"],
        ["policy", "預約排法", "依項目或固定每位時間"],
        ["faq", "常見問題", "答案與關鍵字"],
        ["window", "班表開放", "開放到哪一天"],
        ["calendar", "手機行事曆", "下載本次示範班表"],
        ["password", "更改密碼", "尚未連正式帳號"],
        ["billing", "續費", "示範版不收款"],
      ].forEach(function (item) {
        var row = b(
          "",
          function () {
            open(item[0]);
          },
          "card action",
        );
        row.append(n("h3", null, item[1]), n("p", "muted", item[2]));
        host.append(row);
      });
      host.append(b("重看新手教學", U.tour, "text-button full"));
    });
  }
  function prices() {
    S.open(
      "項目、工時與價格",
      function (host) {
        var draft = clone(),
          rows = n("div"),
          expanded = new Set(),
          readers = [];
        function configured(s) {
          return s.price_mode === "length"
            ? [s.short, s.medium, s.long].every(function (x) { return x !== null; })
            : s.price !== null;
        }
        draft.services.forEach(function (s) { if (!configured(s)) expanded.add(s.id); });
        U.warning(host);
        U.note(host, "已填價格為虛構展示，不是店家正式價目；可編輯，儲存只影響本次示範。");
        var remaining = n("p", "setup-progress");
        host.append(remaining, rows);
        function read() {
          readers.forEach(function (fn) {
            fn();
          });
        }
        function priceField(label, value) {
          var p = f(label, "number", value);
          p.input.min = 0;
          p.input.max = 100000;
          p.input.placeholder = "未填";
          return p;
        }
        function draw() {
          rows.replaceChildren();
          var missing = draft.services.filter(function (s) { return !configured(s); }).length;
          remaining.textContent = missing ? "還有 " + missing + " 項待設定" : "項目都設定好了";
          readers = [];
          draft.services.forEach(function (s) {
            var row = n("section", "price-row"),
              head = n("div", "row"),
              title = n("b", "grow", s.name),
              editor = n("div", "price-editor"),
              dur = f("工時（分）", "number", s.duration);
            var amounts = s.price_mode === "length"
              ? [s.short, s.medium, s.long].map(function (x) { return x === null ? "未填" : S.money(x); }).join(" / ")
              : s.price === null ? "價格未填" : S.money(s.price);
            var toggle = b("", function () {
              read();
              if (expanded.has(s.id)) expanded.delete(s.id); else expanded.add(s.id);
              draw();
            }, "service-summary");
            toggle.setAttribute("aria-label", "編輯" + s.name);
            toggle.setAttribute("aria-expanded", expanded.has(s.id));
            toggle.append(title, n("span", "service-meta", s.duration + " 分 · " + amounts + (s.price_from ? " 起" : "")), n("span", "chev", expanded.has(s.id) ? "⌃" : "⌄"));
            editor.hidden = !expanded.has(s.id);
            dur.input.min = 15;
            dur.input.max = 600;
            head.append(
              n("span", "muted grow", "設定 " + s.name),
              b(
                "移除",
                function () {
                  read();
                  draft.services = draft.services.filter(function (x) {
                    return x.id !== s.id;
                  });
                  draw();
                },
                "text-button",
              ),
            );
            row.append(toggle, editor);
            editor.append(head, dur.wrap);
            var mode = select(
                "價格方式",
                [
                  ["flat", "單一價"],
                  ["length", "分長度"],
                ],
                s.price_mode,
              ),
              from = check("價格為「起」", s.price_from),
              price = priceField("價格", s.price),
              short = priceField("短髮", s.short),
              medium = priceField("中長", s.medium),
              long = priceField("長髮", s.long);
            editor.append(mode.wrap, from.wrap);
            if (s.price_mode === "length") {
              var group = n("div", "inline-fields");
              group.append(short.wrap, medium.wrap, long.wrap);
              editor.append(group);
            } else editor.append(price.wrap);
            function money(input) {
              return input.value === "" ? null : Number(input.value);
            }
            readers.push(function () {
              s.duration = Number(dur.input.value);
              s.price_from = from.input.checked;
              s.price = money(price.input);
              s.short = money(short.input);
              s.medium = money(medium.input);
              s.long = money(long.input);
              s.price_mode = mode.input.value;
            });
            mode.input.addEventListener("change", function () {
              read();
              draw();
            });
            rows.append(row);
          });
        }
        draw();
        var name = f("新增項目名稱", "text"),
          duration = f("工時（分）", "number", 60);
        host.append(
          name.wrap,
          duration.wrap,
          b(
            "＋ 新增項目",
            function () {
              read();
              var label = name.input.value.trim();
              if (
                !label ||
                draft.services.some(function (s) {
                  return s.name === label;
                })
              ) {
                S.toast("請填不同的項目名稱。");
                return;
              }
              var i = 1;
              while (
                draft.services.some(function (s) {
                  return s.id === "custom-" + i;
                })
              )
                i++;
              draft.services.push({
                id: "custom-" + i,
                name: label,
                duration: Number(duration.input.value),
                price: null,
                price_from: false,
                price_mode: "flat",
                short: null,
                medium: null,
                long: null,
              });
              expanded.add("custom-" + i);
              name.input.value = "";
              draw();
            },
            "secondary full",
          ),
        );
        save(host, function () {
          read();
          return draft;
        }, function () {
          expanded.clear();
          draft.services.forEach(function (s) { if (!configured(s)) expanded.add(s.id); });
          draw();
        });
      },
      true,
    );
  }
  function policy() {
    S.open(
      "預約排法",
      function (host) {
        var draft = clone();
        U.warning(host);
        var mode = select(
            "每位客人預留的時間",
            [
              ["service", "依項目估工時"],
              ["fixed", "固定每位一樣久"],
            ],
            draft.duration_mode,
          ),
          fixed = f("固定預留（分）", "number", draft.fixed_duration),
          step = select(
            "客人每幾分鐘可開始",
            [15, 30, 60, 90, 120].map(function (x) {
              return [String(x), x + " 分鐘"];
            }),
            String(draft.step),
          ),
          same = check("接受當天預約", draft.same_day),
          opening = f("開店時間", "time", draft.open_time),
          closing = f("打烊時間", "time", draft.close_time);
        fixed.input.min = 15;
        fixed.input.max = 600;
        fixed.wrap.hidden = mode.input.value !== "fixed";
        // Same underlying setting as before, presented as the two design cards.
        mode.wrap.hidden = true;
        var modeCards = n("div", "policy-modes");
        modeCards.setAttribute("role", "radiogroup");
        modeCards.setAttribute("aria-label", "每位客人預留的時間");
        function drawModes() {
          modeCards.replaceChildren();
          [
            ["service", "依項目估工時", "照項目設定計算，多個項目會加總。"],
            ["fixed", "固定每位一樣久", "不管做什麼，都預留相同時間。"]
          ].forEach(function (item) {
            var button = b("", function () {
              mode.input.value = item[0];
              fixed.wrap.hidden = item[0] !== "fixed";
              drawModes();
            }, "policy-mode");
            button.setAttribute("role", "radio");
            button.setAttribute("aria-checked", mode.input.value === item[0]);
            button.append(n("b", null, item[1]), n("small", null, item[2]));
            modeCards.append(button);
          });
        }
        drawModes();
        mode.input.addEventListener("change", function () {
          fixed.wrap.hidden = mode.input.value !== "fixed";
          drawModes();
        });
        host.append(
          mode.wrap,
          modeCards,
          fixed.wrap,
          step.wrap,
          same.wrap,
          opening.wrap,
          closing.wrap,
          n("h3", null, "不接客與項目規則"),
        );
        var rules = n("div");
        host.append(rules);
        function draw() {
          rules.replaceChildren();
          if (!draft.rules.length)
            U.note(rules, "目前沒有額外規則，仍會檢查現有預約與不接客區塊。");
          draft.rules.forEach(function (r, i) {
            var card = n("div", "card");
            card.append(
              n(
                "b",
                null,
                { daily: "每天", weekday: "平日", weekend: "週末" }[r.scope] +
                  " " +
                  r.start +
                  "–" +
                  r.end,
              ),
              n(
                "p",
                "note",
                r.mode === "none"
                  ? "完全不接客"
                  : "只接 " +
                      r.services
                        .map(function (id) {
                          return draft.services.find(function (s) {
                            return s.id === id;
                          }).name;
                        })
                        .join("、"),
              ),
              b(
                "刪除此規則",
                function () {
                  draft.rules.splice(i, 1);
                  draw();
                },
                "text-button",
              ),
            );
            rules.append(card);
          });
        }
        draw();
        host.append(
          b(
            "＋ 新增一條規則",
            function () {
              S.open(
                "新增不接客規則",
                function (box) {
                  var scope = select(
                      "哪幾天",
                      [
                        ["daily", "每天"],
                        ["weekday", "平日"],
                        ["weekend", "週末"],
                      ],
                      "daily",
                    ),
                    start = f("從", "time", "17:00"),
                    end = f("到", "time", "20:00"),
                    kind = select(
                      "這段時間",
                      [
                        ["none", "完全不接客"],
                        ["only", "只接部分項目"],
                      ],
                      "none",
                    ),
                    picked = [],
                    chips = n("div");
                  function drawChips() {
                    chips.replaceChildren();
                    chips.hidden = kind.input.value !== "only";
                    U.choices(chips, draft.services, picked, function (id) {
                      picked = picked.includes(id)
                        ? picked.filter(function (x) {
                            return x !== id;
                          })
                        : picked.concat(id);
                      drawChips();
                    });
                  }
                  kind.input.addEventListener("change", drawChips);
                  drawChips();
                  box.append(
                    scope.wrap,
                    start.wrap,
                    end.wrap,
                    kind.wrap,
                    chips,
                    b(
                      "存這條規則",
                      function () {
                        if (
                          start.input.value >= end.input.value ||
                          (kind.input.value === "only" && !picked.length)
                        ) {
                          S.fail(
                            box,
                            new Error("請確認起訖時間，並選擇可接的項目。"),
                          );
                          return;
                        }
                        draft.rules.push({
                          scope: scope.input.value,
                          start: start.input.value,
                          end: end.input.value,
                          mode: kind.input.value,
                          services: picked,
                        });
                        draw();
                        S.close();
                      },
                      "primary full",
                    ),
                  );
                },
                true,
              );
            },
            "secondary full",
          ),
        );
        save(host, function () {
          draft.duration_mode = mode.input.value;
          draft.fixed_duration = Number(fixed.input.value);
          draft.step = Number(step.input.value);
          draft.same_day = same.input.checked;
          draft.open_time = opening.input.value;
          draft.close_time = closing.input.value;
          return draft;
        });
      },
      true,
    );
  }
  function faq() {
    S.open(
      "常見問題",
      function (host) {
        var draft = clone(),
          list = n("div");
        U.warning(host);
        host.append(list);
        function draw() {
          list.replaceChildren();
          draft.faqs.forEach(function (x, i) {
            var card = n("div", "card");
            card.append(
              n("h3", null, x.question),
              n("p", "note", x.keywords || "通用答案"),
              n("p", null, x.answer),
              b(
                "刪除",
                function () {
                  draft.faqs.splice(i, 1);
                  draw();
                },
                "text-button",
              ),
            );
            list.append(card);
          });
          if (!draft.faqs.length)
            U.note(list, "尚未填答案，助理不會用預設地址或價格冒充。");
        }
        draw();
        var q = f("問題", "text"),
          kw = f("關鍵字（逗號分隔，可留空）", "text"),
          a = f("答案", "textarea");
        var shortcuts = n("div", "chips");
        ["停車", "公休", "怎麼去"].forEach(function (x) {
          shortcuts.append(
            b(
              x,
              function () {
                q.input.value = x;
                kw.input.value = x;
                a.input.focus();
              },
              "chip",
            ),
          );
        });
        host.append(
          shortcuts,
          q.wrap,
          kw.wrap,
          a.wrap,
          b(
            "加入常見問題",
            function () {
              if (!q.input.value.trim() || !a.input.value.trim()) {
                S.toast("問題和答案都要填。");
                return;
              }
              draft.faqs.push({
                question: q.input.value.trim(),
                keywords: kw.input.value.trim(),
                answer: a.input.value.trim(),
              });
              q.input.value = kw.input.value = a.input.value = "";
              draw();
            },
            "secondary full",
          ),
        );
        save(host, function () {
          return draft;
        });
      },
      true,
    );
  }
  function windowSettings() {
    S.open(
      "班表開放",
      function (host) {
        U.warning(host);
        var draft = clone(),
          month = f("開放到哪個月", "month", draft.open_through.slice(0, 7));
        host.append(month.wrap);
        save(host, function () {
          var parts = month.input.value.split("-").map(Number);
          if (parts.length !== 2 || !parts[0] || !parts[1])
            throw new Error("請先選月份。");
          var last = new Date(Date.UTC(parts[0], parts[1], 0));
          draft.open_through = last.toISOString().slice(0, 10);
          return draft;
        });
      },
      true,
    );
  }
  function calendar() {
    S.open(
      "手機行事曆",
      function (host) {
        U.warning(host);
        U.note(
          host,
          "可下載本次示範的行事曆檔，名稱會標明「示範」。這不是正式班表訂閱；分享或換瀏覽器不會取得此工作階段。",
        );
        var link = f(
          "本次示範連結",
          "text",
          new URL(S.state.calendar_url, location.origin).href,
        );
        link.input.readOnly = true;
        var download = n("a", "primary full", "下載示範 .ics");
        download.href = S.state.calendar_url;
        download.download = "gotyou-demo.ics";
        download.style.display = "block";
        download.style.textAlign = "center";
        host.append(
          link.wrap,
          download,
          b(
            "複製連結",
            function () {
              S.copy(link.input.value).catch(function (e) {
                S.fail(host, e);
              });
            },
            "secondary full",
          ),
          b(
            "換一條連結",
            function () {
              S.confirm(
                "更換示範連結？",
                "原本那條會立刻失效，正式行事曆不受影響。",
                async function () {
                  await S.mutate("rotate_calendar", {});
                  link.input.value = new URL(
                    S.state.calendar_url,
                    location.origin,
                  ).href;
                  download.href = S.state.calendar_url;
                },
              );
            },
            "text-button full",
          ),
        );
      },
      true,
    );
  }
  function unavailable(title, explanation) {
    S.open(
      title,
      function (host) {
        host.append(
          n("p", "warning", explanation),
          b("返回設定", S.close, "secondary full"),
        );
      },
      true,
    );
  }
  function open(name) {
    var views = {
      settings: menu,
      price: prices,
      policy: policy,
      faq: faq,
      window: windowSettings,
      calendar: calendar,
      password: function () {
        unavailable(
          "更改密碼",
          "A／B 示範版沒有設計師登入帳號。這裡不收密碼，也不會假裝替你改成功；接正式後台時才啟用。",
        );
      },
      billing: function () {
        unavailable(
          "續費",
          "示範版不收款、不建立訂閱。正式方案與付款流程接回後才會開放。",
        );
      },
    };
    if (views[name]) views[name]();
  }
  g.WorkbenchSettings = { open: open };
})(window);
