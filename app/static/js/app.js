/* DiagTrack interface behaviour. Plain JavaScript, no libraries. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const root = document.documentElement;
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };

  // light / dark
  $$("[data-theme-toggle]").forEach(b => b.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next; store.set("dt-theme", next);
  }));

  // side menu: collapse on desktop, slide in on phones
  $$("[data-nav-fold]").forEach(b => b.addEventListener("click", () => {
    const mini = root.dataset.nav === "mini";
    if (mini) delete root.dataset.nav; else root.dataset.nav = "mini";
    store.set("dt-nav", mini ? "full" : "mini");
  }));
  $$("[data-nav-open]").forEach(b => b.addEventListener("click", () => document.body.classList.add("nav-open")));
  $$("[data-nav-close]").forEach(b => b.addEventListener("click", () => document.body.classList.remove("nav-open")));

  // drop-down menus (alerts, account)
  $$("[data-menu]").forEach(btn => btn.addEventListener("click", e => {
    e.stopPropagation();
    const pop = btn.nextElementSibling, open = pop.hidden;
    $$(".pop").forEach(p => { p.hidden = true; });
    $$("[data-menu]").forEach(b => b.setAttribute("aria-expanded", "false"));
    pop.hidden = !open; btn.setAttribute("aria-expanded", String(open));
  }));
  document.addEventListener("click", e => { if (!e.target.closest(".pop")) $$(".pop").forEach(p => { p.hidden = true; }); });

  // dialogs
  $$("[data-open]").forEach(b => b.addEventListener("click", () => {
    const d = document.getElementById(b.dataset.open);
    if (d) { d.showModal(); const f = $("textarea, select, input:not([type=hidden])", d); if (f) f.focus(); }
  }));
  $$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
  $$("dialog").forEach(d => d.addEventListener("click", e => { if (e.target === d) d.close(); }));

  $$("[data-dismiss]").forEach(b => b.addEventListener("click", () => b.closest(".toast").remove()));

  // filters apply as soon as they change
  $$("[data-autosubmit]").forEach(f => f.addEventListener("change", e => {
    if (e.target.type === "search") return;
    if (e.target.name === "state") { const s = f.elements.status; if (s) s.value = ""; }
    f.submit();
  }));

  // whole table row opens the ticket
  $$("tr[data-href]").forEach(tr => tr.addEventListener("click", e => {
    if (!e.target.closest("a, button")) window.location = tr.dataset.href;
  }));

  // keyboard: / to search, n for a new ticket
  document.addEventListener("keydown", e => {
    if (e.target.matches("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) {
      if (e.key === "Escape") e.target.blur();
      return;
    }
    if (e.key === "/") { e.preventDefault(); const s = $("[data-search]"); if (s) s.focus(); }
    if (e.key === "n" && !$("dialog[open]")) window.location = "/tickets/new/";
  });

  // attachments: list chosen files, preview photos, accept drag and drop
  $$("[data-drop]").forEach(zone => {
    const input = $("input[type=file]", zone);
    const list = $("[data-file-list]", zone.closest("form"));
    const show = () => {
      if (!list) return;
      list.innerHTML = "";
      Array.from(input.files).forEach(f => {
        const li = document.createElement("li");
        if (f.type.startsWith("image/")) { const img = new Image(); img.src = URL.createObjectURL(f); img.alt = ""; li.appendChild(img); }
        li.appendChild(document.createTextNode(f.name + " (" + (f.size / 1048576).toFixed(1) + " MB)"));
        list.appendChild(li);
      });
    };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(n => zone.addEventListener(n, e => { e.preventDefault(); zone.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach(n => zone.addEventListener(n, () => zone.classList.remove("is-over")));
    zone.addEventListener("drop", e => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); } });
  });

  // raise a ticket: show where it will go, equipment list and extra questions
  const raise = $("[data-raise]");
  if (raise) {
    const meta = JSON.parse($("#cat-meta").textContent);
    const equip = JSON.parse($("#equip-meta").textContent);
    const posted = JSON.parse($("#posted-extra").textContent);
    const cat = $("[data-category]", raise), centre = $("[data-centre]", raise);
    const route = $("[data-route]", raise), block = $("[data-equipment-block]", raise);
    const eq = $("[data-equipment]", raise), extra = $("[data-extra]", raise);
    const refresh = () => {
      const m = meta[cat.value];
      route.hidden = !m;
      if (m) $("span", route).textContent = "Goes to " + m.department + ". Usual priority: " + m.priority + ".";
      block.hidden = !(m && m.equipment);
      const keep = eq.value || eq.dataset.selected;
      eq.innerHTML = '<option value="">Choose the equipment</option>';
      (equip[centre.value] || []).forEach(x => {
        const o = new Option(x.name, x.id); if (String(x.id) === String(keep)) o.selected = true; eq.add(o);
      });
      extra.innerHTML = "";
      ((m && m.fields) || []).forEach(f => {
        const label = document.createElement("label"); label.className = "field";
        const span = document.createElement("span"); span.textContent = f.label + (f.required ? "" : " (optional)"); label.appendChild(span);
        let input;
        if (f.kind === "choice") {
          input = document.createElement("select"); input.add(new Option("Choose", ""));
          f.choices.forEach(c => input.add(new Option(c, c)));
        } else { input = document.createElement("input"); input.type = f.kind === "number" ? "number" : "text"; }
        input.name = "field_" + f.id; input.required = f.required; input.value = posted[input.name] || "";
        label.appendChild(input); extra.appendChild(label);
      });
    };
    cat.addEventListener("change", refresh); centre.addEventListener("change", refresh); refresh();
  }

  // board: drag a card to the next column
  const board = $("[data-board]");
  if (board) {
    const quick = $("[data-board-form]");
    let card = null;
    const actionFor = (c, col) => (col.dataset.accepts || "").split(" ").find(a => a && c.dataset.can.split(" ").includes(a));
    $$(".card[draggable=true]", board).forEach(c => {
      c.addEventListener("dragstart", e => { card = c; c.classList.add("is-dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.dataset.number);
        $$(".col", board).forEach(col => { if (col.dataset.col !== c.dataset.from && !actionFor(c, col)) col.classList.add("is-denied"); }); });
      c.addEventListener("dragend", () => { c.classList.remove("is-dragging"); $$(".col", board).forEach(col => col.classList.remove("is-target", "is-denied")); card = null; });
    });
    $$(".col", board).forEach(col => {
      col.addEventListener("dragover", e => { if (card && actionFor(card, col)) { e.preventDefault(); col.classList.add("is-target"); } });
      col.addEventListener("dragleave", e => { if (!col.contains(e.relatedTarget)) col.classList.remove("is-target"); });
      col.addEventListener("drop", e => {
        e.preventDefault();
        const action = card && actionFor(card, col); if (!action) return;
        const url = "/tickets/" + card.dataset.number + "/" + action + "/";
        if (action === "hold" || action === "resolve") {
          const d = document.getElementById("dlg-" + action), f = $("form", d);
          f.action = url; $("[data-num]", d).textContent = card.dataset.number;
          const hint = $("[data-downtime-hint]", d); if (hint) hint.hidden = !card.dataset.downtime;
          d.showModal(); $("textarea, select", d).focus();
        } else { quick.action = url; quick.submit(); }
      });
    });
  }

  // chart: show the day's numbers on hover, tap or keyboard focus
  $$("[data-chart]").forEach(chart => {
    const tip = $(".tip", chart), pts = $$(".pt", chart);
    const show = p => {
      pts.forEach(x => x.classList.toggle("is-on", x === p));
      tip.innerHTML = "";
      const b = document.createElement("b"); b.textContent = p.dataset.day; tip.appendChild(b);
      [["sw-1", "Raised", p.dataset.raised], ["sw-2", "Resolved", p.dataset.resolved]].forEach(r => {
        const s = document.createElement("span"); const i = document.createElement("i"); i.className = "sw " + r[0];
        s.appendChild(i); s.appendChild(document.createTextNode(r[1] + ": " + r[2])); tip.appendChild(s);
      });
      tip.hidden = false;
      const box = chart.getBoundingClientRect(), r = $(".cross", p).getBoundingClientRect();
      let left = r.left - box.left + 12;
      if (left + tip.offsetWidth > box.width) left = r.left - box.left - tip.offsetWidth - 12;
      tip.style.left = left + "px";
    };
    pts.forEach(p => { p.addEventListener("mouseenter", () => show(p)); p.addEventListener("click", () => show(p)); });
    chart.addEventListener("mouseleave", () => { tip.hidden = true; pts.forEach(x => x.classList.remove("is-on")); });
  });

  // live countdown refresh: reload list pages every 2 minutes so timers stay honest
  if ($(".sla") && !$("[data-raise]") && !$(".composer")) {
    setTimeout(() => { if (!$("dialog[open]") && document.visibilityState === "visible") window.location.reload(); }, 120000);
  }
})();
