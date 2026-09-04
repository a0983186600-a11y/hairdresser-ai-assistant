/*
 * 四個資料頁：預約、班表、客人、設定。
 *
 * 版面是重做的，欄位與用語照舊後台（「已進 POS」「需要處理」「不接客」
 * 「班表開放」「公司系統連接」）——設計師換過來不用重新學名詞，只是東西擺得
 * 比較好找了。資料一律來自 /api/demo/*：B 版是存好的假資料 fixture，
 * A 版由伺服器轉發正式後台，前端這一份程式兩邊完全一樣。
 */
(function (global) {
  "use strict";

  function el(tag, className, text) {
    return global.AssistantShell.node(tag, className, text);
  }

  function mount(id) {
    return document.getElementById(id);
  }

  function fail(host, error) {
    host.innerHTML = "";
    host.appendChild(el("p", "empty", "資料讀不到：" + error.message));
  }

  function cell(row, className, text) {
    var td = el("td", className, text);
    row.appendChild(td);
    return td;
  }

  function pill(label, tone) {
    return el("span", "pill " + (tone || "muted"), label);
  }

  function table(headers) {
    var scroll = el("div", "table-scroll");
    var node = el("table");
    var head = el("thead");
    var headRow = el("tr");
    headers.forEach(function (label) {
      headRow.appendChild(el("th", null, label));
    });
    head.appendChild(headRow);
    node.appendChild(head);
    var body = el("tbody");
    node.appendChild(body);
    scroll.appendChild(node);
    return { scroll: scroll, body: body };
  }

  function tiles(host, items) {
    var wrap = el("div", "tiles");
    items.forEach(function (item) {
      var tile = el("div", "tile");
      tile.appendChild(el("b", null, item[0]));
      tile.appendChild(el("span", null, item[1]));
      wrap.appendChild(tile);
    });
    host.appendChild(wrap);
  }

  function filterBar(host, chips, onPick, searchLabel, onType) {
    var bar = el("div", "toolbar");
    if (searchLabel) {
      var field = el("div", "field");
      var input = document.createElement("input");
      input.type = "search";
      input.placeholder = searchLabel;
      input.addEventListener("input", function () {
        onType(input.value.trim());
      });
      field.appendChild(input);
      bar.appendChild(field);
    }
    var buttons = [];
    chips.forEach(function (chip, index) {
      var button = el("button", "chip", chip.label);
      button.type = "button";
      button.setAttribute("aria-pressed", index === 0 ? "true" : "false");
      button.addEventListener("click", function () {
        buttons.forEach(function (other) {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        onPick(chip.key);
      });
      buttons.push(button);
      bar.appendChild(button);
    });
    host.appendChild(bar);
  }

  function note(host, text) {
    host.appendChild(el("p", "note", text));
  }

  // --- 預約 -----------------------------------------------------------------

  function renderBookings(host, data) {
    host.innerHTML = "";
    tiles(host, [
      [data.summary.total, "未來三週預約"],
      [data.summary.confirmed, "已進 POS"],
      [data.summary.pending, "需要處理"]
    ]);

    var panel = el("div", "panel");
    panel.appendChild(el("h2", null, "預約列表"));
    note(panel, data.privacy_note);

    var state = { keyword: "", only: "all" };
    var built = table(["日期", "時間", "客人", "電話後四碼", "服務", "狀態", "怎麼來的"]);

    function draw() {
      built.body.innerHTML = "";
      var rows = data.rows.filter(function (row) {
        if (state.only !== "all" && row.status !== state.only) {
          return false;
        }
        if (!state.keyword) {
          return true;
        }
        var hay = row.masked_name + row.phone_last4 + row.service_label + row.date_label;
        return hay.indexOf(state.keyword) >= 0;
      });
      if (!rows.length) {
        var empty = el("tr");
        var only = el("td", "empty", "沒有符合的預約。");
        only.colSpan = 7;
        empty.appendChild(only);
        built.body.appendChild(empty);
        return;
      }
      rows.forEach(function (row) {
        var line = el("tr");
        cell(line, null, row.date_label);
        cell(line, "num", row.time_label);
        cell(line, "name", row.masked_name);
        cell(line, "num", row.phone_last4 || "—");
        cell(line, null, row.service_label);
        cell(line, null, "").appendChild(pill(row.status_label, row.status_tone));
        cell(line, null, row.source);
        built.body.appendChild(line);
      });
    }

    filterBar(panel, [
      { key: "all", label: "全部" },
      { key: "confirmed", label: "已進 POS" },
      { key: "pending", label: "需要處理" }
    ], function (key) {
      state.only = key;
      draw();
    }, "找客人、服務或日期", function (value) {
      state.keyword = value;
      draw();
    });

    panel.appendChild(built.scroll);
    host.appendChild(panel);
    draw();
  }

  // --- 班表 -----------------------------------------------------------------

  function renderSchedule(host, data) {
    host.innerHTML = "";
    var panel = el("div", "panel");
    panel.appendChild(el("h2", null, "我的時間表"));
    note(panel, data.booking_open_note + " " + data.privacy_note);

    var weeks = [];
    for (var start = 0; start < data.days.length; start += 7) {
      weeks.push(data.days.slice(start, start + 7));
    }

    var board = el("div", "week");

    function drawWeek(index) {
      board.innerHTML = "";
      weeks[index].forEach(function (day) {
        var block = el("div", "day" + (day.is_anchor_day ? " anchor" : ""));
        var when = el("div", "when", day.label);
        when.appendChild(el("small", null, "週" + day.weekday));
        block.appendChild(when);
        if (!day.slots.length) {
          block.appendChild(el("div", "free", "整天沒有預約，也沒有擋住的時段。"));
          board.appendChild(block);
          return;
        }
        var slots = el("div", "slots");
        day.slots.forEach(function (slot) {
          var line = el("div", "slot" + (slot.kind === "booking" ? "" : " off"));
          line.appendChild(el("span", "time", slot.starts_at + "–" + slot.ends_at));
          if (slot.kind === "booking") {
            line.appendChild(el("span", "who", slot.masked_name));
            line.appendChild(el("span", "what", slot.service_label));
          } else {
            line.appendChild(el("span", "who", "不接客"));
            line.appendChild(el("span", "what", slot.reason));
          }
          var tail = el("span", "tail");
          tail.appendChild(pill(slot.status_label, slot.status_tone));
          line.appendChild(tail);
          slots.appendChild(line);
        });
        block.appendChild(slots);
        board.appendChild(block);
      });
    }

    filterBar(panel, weeks.map(function (week, index) {
      return { key: index, label: week[0].label + " 起" };
    }), function (key) {
      drawWeek(key);
    });

    panel.appendChild(board);
    host.appendChild(panel);
    drawWeek(0);
  }

  // --- 客人 -----------------------------------------------------------------

  function renderCustomers(host, data) {
    host.innerHTML = "";
    tiles(host, [
      [data.summary.total, "我的客人"],
      [data.summary.inactive_60, "超過 60 天沒回來"],
      [global.AssistantShell.money(data.summary.known_spend_twd), "已知消費合計"],
      [data.summary.unknown_amount_visits, "缺金額的到店筆數"]
    ]);

    var panel = el("div", "panel");
    panel.appendChild(el("h2", null, "查客人"));
    note(
      panel,
      "金額只算得出來的那幾次，缺金額的另外列。" + data.privacy_note
    );

    var state = { keyword: "", only: "all" };
    var built = table(data.columns.map(function (column) {
      return column.label;
    }));

    function draw() {
      built.body.innerHTML = "";
      var rows = data.rows.filter(function (row) {
        if (state.only === "inactive" && row.days_since_last_visit < 60) {
          return false;
        }
        if (state.only === "loyal" && row.visit_count < 5) {
          return false;
        }
        if (!state.keyword) {
          return true;
        }
        return (row.masked_name + (row.phone_last4 || "")).indexOf(state.keyword) >= 0;
      });
      if (!rows.length) {
        var empty = el("tr");
        var only = el("td", "empty", "目前沒有符合的客人資料。");
        only.colSpan = data.columns.length;
        empty.appendChild(only);
        built.body.appendChild(empty);
        return;
      }
      rows.slice(0, 200).forEach(function (row) {
        var line = el("tr");
        data.columns.forEach(function (column, index) {
          var value = row[column.key];
          if (column.key === "known_spend_twd") {
            value = global.AssistantShell.money(value);
          }
          if (column.key === "days_since_last_visit") {
            value = value + " 天";
          }
          if (column.key === "visit_count") {
            value = value + " 次";
          }
          if (column.key === "unknown_amount_visits") {
            value = value + " 筆";
          }
          var className = index === 0 ? "name" : "num";
          cell(line, className, value === null || value === undefined ? "—" : value);
        });
        built.body.appendChild(line);
      });
    }

    filterBar(panel, [
      { key: "all", label: "全部" },
      { key: "inactive", label: "60 天沒回來" },
      { key: "loyal", label: "到店 5 次以上" }
    ], function (key) {
      state.only = key;
      draw();
    }, "姓名或電話後四碼", function (value) {
      state.keyword = value;
      draw();
    });

    panel.appendChild(built.scroll);
    host.appendChild(panel);
    draw();
  }

  // --- 設定 -----------------------------------------------------------------

  function renderSettings(host, data) {
    host.innerHTML = "";
    data.sections.forEach(function (section) {
      var panel = el("div", "panel");
      var head = el("div", "toolbar");
      head.appendChild(el("h2", null, section.title));
      if (section.badge) {
        head.appendChild(pill(section.badge.label, section.badge.tone));
      }
      panel.appendChild(head);
      note(panel, section.note);
      var rows = el("div", "rows");
      section.fields.forEach(function (field) {
        var row = el("div", "row");
        row.appendChild(el("span", "label", field.label));
        if (field.type === "toggle") {
          var toggle = el("span", "switch" + (field.enabled ? " on" : ""));
          toggle.appendChild(el("span", "track"));
          toggle.appendChild(el("span", null, field.value));
          row.appendChild(toggle);
        } else {
          row.appendChild(el("span", "value", field.value));
        }
        rows.appendChild(row);
      });
      panel.appendChild(rows);
      host.appendChild(panel);
    });
    host.appendChild(el("p", "note", data.privacy_note));
  }

  var RENDER = {
    bookings: renderBookings,
    schedule: renderSchedule,
    customers: renderCustomers,
    settings: renderSettings
  };

  document.addEventListener("DOMContentLoaded", function () {
    var page = document.body.getAttribute("data-page");
    var host = mount("page-body");
    if (!page || !host || !RENDER[page]) {
      return;
    }
    global.AssistantApi.demo(page).then(function (data) {
      RENDER[page](host, data);
    }).catch(function (error) {
      fail(host, error);
    });
  });
})(window);
