/* DiagTrack interface behaviour. Plain JavaScript, no libraries, works without internet. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const root = document.documentElement;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
  const icon = (name) => `<svg class="i" aria-hidden="true"><use href="${window.DT_ICONS}#${name}"/></svg>`;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const typing = (el) => el && (el.matches("input, textarea, select, [contenteditable]"));

  $$("[data-mod-key]").forEach(k => { k.textContent = isMac ? "⌘ K" : "Ctrl K"; });

  /* ---------- theme, menu, density ---------- */
  $$("[data-theme-toggle]").forEach(b => b.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next; store.set("dt-theme", next);
  }));
  $$("[data-nav-fold]").forEach(b => b.addEventListener("click", () => {
    const mini = root.dataset.nav === "mini";
    if (mini) delete root.dataset.nav; else root.dataset.nav = "mini";
    store.set("dt-nav", mini ? "full" : "mini");
  }));
  $$("[data-nav-open]").forEach(b => b.addEventListener("click", () => document.body.classList.add("nav-open")));
  $$("[data-nav-close]").forEach(b => b.addEventListener("click", () => document.body.classList.remove("nav-open")));
  const densityLabel = () => $$("[data-density-label]").forEach(l => { l.textContent = root.dataset.density === "compact" ? "Comfortable rows" : "Compact rows"; });
  densityLabel();
  $$("[data-density-toggle]").forEach(b => b.addEventListener("click", () => {
    if (root.dataset.density === "compact") { delete root.dataset.density; store.set("dt-density", ""); }
    else { root.dataset.density = "compact"; store.set("dt-density", "compact"); }
    densityLabel();
  }));

  /* ---------- drop-down menus ---------- */
  const closeMenus = () => { $$(".menu > .pop").forEach(p => { p.hidden = true; }); $$("[data-menu]").forEach(b => b.setAttribute("aria-expanded", "false")); };
  $$("[data-menu]").forEach(btn => btn.addEventListener("click", e => {
    e.stopPropagation();
    const pop = btn.nextElementSibling, open = pop.hidden;
    closeMenus();
    pop.hidden = !open; btn.setAttribute("aria-expanded", String(open));
  }));
  document.addEventListener("click", e => { if (!e.target.closest(".pop")) closeMenus(); });

  /* ---------- dialogs ---------- */
  $$("[data-open]").forEach(b => b.addEventListener("click", () => {
    const d = document.getElementById(b.dataset.open);
    if (!d) return;
    closeMenus();
    if (b.dataset.call) { const i = $("[data-call-input]", d); if (i) i.value = b.dataset.call; }
    if (b.dataset.vendor) { const v = $("[data-vendor-name]", d); if (v) v.textContent = b.dataset.vendor; }
    d.showModal();
    const f = $("textarea, select, input:not([type=hidden])", d); if (f) f.focus();
  }));
  $$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
  $$("dialog.dialog").forEach(d => d.addEventListener("click", e => { if (e.target === d) d.close(); }));
  $$("[data-dismiss]").forEach(b => b.addEventListener("click", () => b.closest(".toast").remove()));

  /* ---------- reports: show the date boxes only for "Choose dates" ---------- */
  const rf = $("[data-report-form]");
  if (rf) rf.addEventListener("change", e => {
    if (e.target.name === "period") $("[data-custom-dates]", rf).hidden = e.target.value !== "custom";
  });

  /* ---------- filters apply as soon as they change ---------- */
  $$("[data-autosubmit]").forEach(f => f.addEventListener("change", e => {
    if (e.target.type === "search") return;
    if (e.target.name === "state") { const s = f.elements.status; if (s) s.value = ""; }
    f.submit();
  }));

  /* ---------- numbers count up once when the page opens ---------- */
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  $$("[data-count]").forEach(el => {
    const end = parseInt(el.textContent, 10);
    if (reduce || !end || end < 2) return;
    const t0 = performance.now(), dur = 700;
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(end * e);
      if (p < 1) requestAnimationFrame(tick);
    };
    el.textContent = "0"; requestAnimationFrame(tick);
  });

  /* ---------- ticket preview drawer ---------- */
  const drawer = $("#drawer"), drawerBody = $("[data-drawer-body]"), drawerScrim = $(".drawer-scrim");
  let drawerRow = null;
  const closeDrawer = () => {
    drawer.hidden = true; drawerScrim.hidden = true;
    if (drawerRow) drawerRow.classList.remove("is-selected"); drawerRow = null;
  };
  const openDrawer = (url, row) => {
    if (drawerRow) drawerRow.classList.remove("is-selected");
    drawerRow = row; if (row) row.classList.add("is-selected");
    drawer.hidden = false; drawerScrim.hidden = false;
    drawerBody.innerHTML = '<div class="skeleton"><i></i><i></i><i></i><i></i></div>';
    fetch(url, { credentials: "same-origin" }).then(r => r.ok ? r.text() : Promise.reject(r.status)).then(html => {
      drawerBody.innerHTML = html;
      $$("[data-drawer-close]", drawerBody).forEach(b => b.addEventListener("click", closeDrawer));
      bindLightbox(drawerBody);
    }).catch(() => { window.location = url.replace(/\?preview=1$/, ""); });
  };
  $$("[data-drawer-close]").forEach(b => b.addEventListener("click", closeDrawer));
  $$("tr[data-href]").forEach(tr => tr.addEventListener("click", e => {
    if (e.target.closest("a, button, input, select")) return;
    if (tr.dataset.preview && !e.metaKey && !e.ctrlKey && innerWidth > 820) openDrawer(tr.dataset.preview, tr);
    else window.location = tr.dataset.href;
  }));
  $$("a.row[data-preview]").forEach(a => a.addEventListener("click", e => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || innerWidth <= 820) return;
    e.preventDefault(); openDrawer(a.dataset.preview, a);
  }));

  /* ---------- photo viewer ---------- */
  const lb = $("#lightbox"), lbImg = $("[data-lb-img]"), lbCap = $("[data-lb-cap]");
  let lbSet = [], lbAt = 0;
  const lbShow = () => { const a = lbSet[lbAt]; lbImg.src = a.href; lbCap.textContent = (a.querySelector("img") || {}).alt || ""; $$(".lb-nav", lb).forEach(b => { b.hidden = lbSet.length < 2; }); };
  function bindLightbox(scope) {
    $$("a[data-lightbox]", scope).forEach(a => a.addEventListener("click", e => {
      if (e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      lbSet = $$("a[data-lightbox]", a.closest(".block, .drawer-body, main") || document);
      lbAt = Math.max(0, lbSet.indexOf(a)); lbShow(); lb.hidden = false;
    }));
  }
  bindLightbox(document);
  const lbMove = (d) => { lbAt = (lbAt + d + lbSet.length) % lbSet.length; lbShow(); };
  $("[data-lb-close]").addEventListener("click", () => { lb.hidden = true; });
  $("[data-lb-prev]").addEventListener("click", () => lbMove(-1));
  $("[data-lb-next]").addEventListener("click", () => lbMove(1));
  lb.addEventListener("click", e => { if (e.target === lb) lb.hidden = true; });

  /* ---------- command palette ---------- */
  const pal = $("#palette"), palIn = $("[data-palette-input]"), palList = $("[data-palette-list]");
  let screens = []; try { screens = JSON.parse($("#palette-screens").textContent); } catch (e) {}
  let palItems = [], palSel = 0, palTimer = null, palSeq = 0;
  const palRender = (tickets) => {
    const q = palIn.value.trim().toLowerCase();
    const scr = screens.filter(s => !q || s.t.toLowerCase().includes(q)).slice(0, q ? 6 : 12);
    let html = "";
    palItems = [];
    if (tickets && tickets.length) {
      html += "<li><p>Tickets</p></li>";
      tickets.forEach(t => {
        palItems.push(`/tickets/${t.number}/`);
        html += `<li><a href="/tickets/${esc(t.number)}/" data-i="${palItems.length - 1}"><span class="pi"><span class="prio prio-${esc(t.priority)}">${esc(t.priority)}</span></span><span><b class="num">${esc(t.number)}</b> ${esc(t.subject)}<small>${esc(t.meta)}</small></span><span class="loz">${esc(t.status)}</span></a></li>`;
      });
    }
    if (scr.length) {
      html += "<li><p>Go to</p></li>";
      scr.forEach(s => {
        palItems.push(s.u);
        html += `<li><a href="${esc(s.u)}" data-i="${palItems.length - 1}"><span class="pi">${icon(s.i)}</span><span>${esc(s.t)}</span><span></span></a></li>`;
      });
    }
    if (!palItems.length) html = `<li class="empty-s">Nothing matches "${esc(palIn.value)}". Try a ticket number such as KKP-000123.</li>`;
    palList.innerHTML = html;
    palSel = 0; palMark();
  };
  const palMark = () => $$("a[data-i]", palList).forEach(a => {
    const on = +a.dataset.i === palSel; a.setAttribute("aria-selected", on);
    if (on) a.scrollIntoView({ block: "nearest" });
  });
  const palOpen = () => { closeMenus(); palIn.value = ""; palRender([]); pal.showModal(); palIn.focus(); };
  $$("[data-palette-open]").forEach(b => b.addEventListener("click", palOpen));
  palIn.addEventListener("input", () => {
    clearTimeout(palTimer);
    const q = palIn.value.trim(), seq = ++palSeq;
    palRender([]);
    if (q.length < 2) return;
    palTimer = setTimeout(() => fetch("/api/search/?q=" + encodeURIComponent(q), { credentials: "same-origin" })
      .then(r => r.json()).then(d => { if (seq === palSeq) palRender(d.tickets); }).catch(() => {}), 160);
  });
  palIn.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); palSel = Math.min(palItems.length - 1, palSel + 1); palMark(); }
    if (e.key === "ArrowUp") { e.preventDefault(); palSel = Math.max(0, palSel - 1); palMark(); }
    if (e.key === "Enter") {
      e.preventDefault();
      const q = palIn.value.trim().toUpperCase();
      if (/^[A-Z0-9]{2,5}-\d{1,6}$/.test(q) && !palItems.length) { window.location = "/tickets/?q=" + encodeURIComponent(q); return; }
      if (palItems[palSel]) window.location = palItems[palSel];
    }
  });
  pal.addEventListener("click", e => { if (e.target === pal) pal.close(); });

  /* ---------- keyboard ---------- */
  document.addEventListener("keydown", e => {
    if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) { e.preventDefault(); pal.open ? pal.close() : palOpen(); return; }
    if (e.key === "Escape") {
      if (!lb.hidden) { lb.hidden = true; return; }
      if (!drawer.hidden) { closeDrawer(); return; }
    }
    if (!lb.hidden && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { lbMove(e.key === "ArrowLeft" ? -1 : 1); return; }
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey || $("dialog[open]")) return;
    if (e.key === "/") { e.preventDefault(); palOpen(); }
    if (e.key === "n") window.location = "/tickets/new/";
    if (e.key === "m" && $("[data-composer]")) { e.preventDefault(); openComposer(); }
  });

  /* ---------- attachments: list chosen files, preview photos, drag and drop ---------- */
  $$("[data-drop]").forEach(zone => {
    const input = $("input[type=file]", zone);
    if (!input) return;
    const form = zone.closest("form");
    const list = form && $("[data-file-list]", form);
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
    const target = zone.closest(".editor") || zone;
    ["dragenter", "dragover"].forEach(n => target.addEventListener(n, e => { e.preventDefault(); zone.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach(n => target.addEventListener(n, () => zone.classList.remove("is-over")));
    target.addEventListener("drop", e => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); } });
  });

  /* ---------- raise a ticket: where it goes, equipment list, extra questions ---------- */
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

  /* ---------- ticket page: activity, composer, formatting, @mentions ---------- */
  const composer = $("[data-composer]");
  function openComposer(withFiles) {
    if (!composer) return;
    composer.classList.add("is-open");
    const form = $(".composer-form", composer); form.hidden = false;
    const ta = $("[data-editor]", composer); ta.focus();
    if (withFiles) { const f = $("input[type=file]", form); if (f) f.click(); }
  }
  if (composer) {
    $$("[data-compose-open]").forEach(b => b.addEventListener("click", () => openComposer(b.hasAttribute("data-attach"))));
    $("[data-compose-cancel]", composer).addEventListener("click", () => {
      const form = $(".composer-form", composer); form.hidden = true; composer.classList.remove("is-open");
    });
    const internal = $("[data-internal]", composer);
    if (internal) internal.addEventListener("change", () => composer.classList.toggle("is-internal", internal.checked));
    const ta = $("[data-editor]", composer);
    const wrap = (before, after, placeholder) => {
      const s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e) || placeholder;
      ta.setRangeText(before + sel + after, s, e, "end");
      if (sel === placeholder) { ta.selectionStart = s + before.length; ta.selectionEnd = s + before.length + sel.length; }
      ta.focus(); ta.dispatchEvent(new Event("input"));
    };
    const lines = (prefix) => {
      const s = ta.selectionStart, e = ta.selectionEnd, start = ta.value.lastIndexOf("\n", s - 1) + 1;
      const block = ta.value.slice(start, e) || "item";
      let n = 0;
      const out = block.split("\n").map(l => (prefix === "1." ? (++n) + ". " : prefix + " ") + l).join("\n");
      ta.setRangeText(out, start, Math.max(e, start + (ta.value.slice(start, e) ? 0 : 0)), "end"); ta.focus();
    };
    $$("[data-fmt]", composer).forEach(b => b.addEventListener("click", () => {
      const f = b.dataset.fmt;
      if (f === "bold") wrap("**", "**", "bold text");
      if (f === "italic") wrap("_", "_", "italic text");
      if (f === "code") wrap("`", "`", "code");
      if (f === "ul") lines("-");
      if (f === "ol") lines("1.");
      if (f === "link") { const url = prompt("Link address (starting with http)"); if (url) wrap("[", "](" + url + ")", "link text"); }
      if (f === "mention") { wrap("@", "", ""); ta.dispatchEvent(new Event("input")); }
    }));
    ta.addEventListener("keydown", e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") { e.preventDefault(); wrap("**", "**", "bold text"); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "i") { e.preventDefault(); wrap("_", "_", "italic text"); }
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); ta.form.requestSubmit(); }
    });
    mentionBox(ta, $("[data-mention-pop]", composer));
  }
  function mentionBox(ta, pop) {
    if (!ta || !pop) return;
    let people = [], sel = 0, at = -1, seq = 0;
    const close = () => { pop.hidden = true; at = -1; };
    const pick = (p) => {
      const caret = ta.selectionStart;
      ta.setRangeText("@" + p.username + " ", at, caret, "end"); close(); ta.focus();
    };
    const draw = () => {
      pop.innerHTML = people.map((p, i) => `<button type="button" role="option" aria-selected="${i === sel}" data-i="${i}"><span class="av av-s" style="--h:${p.hue}">${esc(p.initials)}</span><span>${esc(p.name)}<small>@${esc(p.username)}, ${esc(p.role)}</small></span></button>`).join("");
      $$("button", pop).forEach(b => b.addEventListener("mousedown", e => { e.preventDefault(); pick(people[+b.dataset.i]); }));
      pop.hidden = !people.length;
    };
    ta.addEventListener("input", () => {
      const caret = ta.selectionStart, before = ta.value.slice(0, caret);
      const m = before.match(/(^|[\s(])@([\w.+-]{0,30})$/);
      if (!m) { close(); return; }
      at = caret - m[2].length - 1;
      const n = ++seq;
      fetch("/api/people/?q=" + encodeURIComponent(m[2]), { credentials: "same-origin" }).then(r => r.json()).then(d => {
        if (n !== seq || at < 0) return; people = d.people; sel = 0; draw();
      }).catch(close);
    });
    ta.addEventListener("keydown", e => {
      if (pop.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % people.length; draw(); }
      if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel - 1 + people.length) % people.length; draw(); }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(people[sel]); }
      if (e.key === "Escape") { e.preventDefault(); close(); }
    });
    ta.addEventListener("blur", () => setTimeout(close, 150));
  }

  const activity = $("[data-activity]");
  if (activity) {
    const feed = $("[data-feed]", activity);
    $$("[data-act-filter]", activity).forEach(b => b.addEventListener("click", () => {
      $$("[data-act-filter]", activity).forEach(x => x.setAttribute("aria-selected", String(x === b)));
      const k = b.dataset.actFilter;
      $$(".feed-item", feed).forEach(li => li.classList.toggle("is-hidden", k !== "all" && li.dataset.kind !== k));
    }));
    const orderBtn = $("[data-act-order]", activity);
    const setOrder = (o) => { feed.dataset.order = o; $("span", orderBtn).textContent = o === "new" ? "Oldest first" : "Newest first"; store.set("dt-order", o); };
    setOrder(store.get("dt-order") || "old");
    orderBtn.addEventListener("click", () => setOrder(feed.dataset.order === "new" ? "old" : "new"));
    $$("[data-edit-comment]", feed).forEach(b => b.addEventListener("click", () => {
      const body = b.closest(".feed-body"), form = $(".edit-form", body);
      form.hidden = false; $("[data-comment-view]", body).hidden = true; b.parentElement.hidden = true;
      const ta = $("textarea", form); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
      $("[data-edit-cancel]", form).onclick = () => { form.hidden = true; $("[data-comment-view]", body).hidden = false; b.parentElement.hidden = false; };
    }));
    if (location.hash && $(location.hash)) $(location.hash).scrollIntoView({ block: "center" });
  }

  /* ---------- board: drag a card to the next column ---------- */
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

  /* ---------- trend chart: show the day's numbers ---------- */
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

  /* ---------- live alerts and pop-ups ---------- */
  const body = document.body;
  const popups = $("#popups");
  const bellDot = $("[data-unread]");
  const alertList = $("[data-alert-list]");
  const liveDot = $(".live");
  let last = parseInt(body.dataset.lastAlert || "0", 10);
  const soundToggle = $("[data-sound-toggle]");
  if (soundToggle) {
    soundToggle.checked = store.get("dt-sound") !== "off";
    soundToggle.addEventListener("change", () => store.set("dt-sound", soundToggle.checked ? "on" : "off"));
  }
  const desktopBtn = $("[data-desktop-alerts]");
  if (desktopBtn) {
    if (!("Notification" in window) || !window.isSecureContext) desktopBtn.hidden = true;
    else {
      const label = () => { desktopBtn.textContent = Notification.permission === "granted" ? "Desktop alerts on" : "Turn on desktop alerts"; };
      label();
      desktopBtn.addEventListener("click", () => Notification.requestPermission().then(label));
    }
  }
  let audio = null;
  const chime = (urgent) => {
    if (store.get("dt-sound") === "off") return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const notes = urgent ? [880, 660, 880] : [660, 880];
      notes.forEach((f, i) => {
        const o = audio.createOscillator(), g = audio.createGain(), t = audio.currentTime + i * 0.14;
        o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(audio.destination);
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.12, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.32);
        o.start(t); o.stop(t + 0.34);
      });
    } catch (e) {}
  };
  const KIND = {
    raised: ["New ticket", "circle-plus"], closed: ["Ticket closed", "circle-check"], resolved: ["Resolved", "check"],
    escalation: ["Escalation", "triangle-alert"], assigned: ["Assigned to you", "user-plus"], reply: ["New reply", "message-square"],
    note: ["Internal note", "lock"], mention: ["You were mentioned", "message-square"], reopened: ["Reopened", "rotate-ccw"],
    transferred: ["Transferred", "arrow-right-left"], priority: ["Priority changed", "flag"], hold: ["On hold", "pause"],
    started: ["Work started", "play"], cancelled: ["Cancelled", "x"], amc: ["AMC renewal", "wrench"], vendor: ["Vendor", "wrench"],
  };
  const dismiss = (el) => { el.classList.add("is-leaving"); setTimeout(() => el.remove(), 260); };
  const popup = (n) => {
    const [label, ic] = KIND[n.kind] || ["Update", "bell"];
    const el = document.createElement("div");
    el.className = `popup k-${n.kind}${n.urgent ? " is-urgent" : ""}`;
    el.setAttribute("role", n.urgent ? "alert" : "status");
    el.innerHTML = `<span class="popup-ic">${icon(ic)}</span><div><span class="popup-kind">${esc(label)}${n.priority ? " · " + esc(n.priority) : ""}</span><b>${esc(n.title)}</b>${n.body ? `<p>${esc(n.body)}</p>` : ""}<a class="btn btn-s" href="${esc(n.url)}">Open${n.number ? " " + esc(n.number) : ""}</a></div><button class="popup-x" type="button" aria-label="Dismiss">${icon("x")}</button><span class="popup-life"></span>`;
    $(".popup-x", el).addEventListener("click", () => dismiss(el));
    const life = $(".popup-life", el);
    if (!n.urgent) life.addEventListener("animationend", () => dismiss(el));
    popups.appendChild(el);
    while (popups.children.length > 4) popups.firstElementChild.remove();
  };
  const desktop = (n) => {
    if (!("Notification" in window) || Notification.permission !== "granted" || document.visibilityState === "visible") return;
    try { const d = new Notification(n.title, { body: n.body, tag: "dt-" + n.id }); d.onclick = () => { window.focus(); window.location = n.url; }; } catch (e) {}
  };
  const addToBell = (n) => {
    if (!alertList) return;
    const empty = $(".empty-s", alertList); if (empty) empty.remove();
    const a = document.createElement("a");
    a.className = `alert-row unread${n.urgent ? " urgent" : ""}`; a.href = n.url;
    a.innerHTML = `<span class="k k-${esc(n.kind)}" aria-hidden="true"></span><span>${esc(n.title)}</span><small>now</small>`;
    alertList.prepend(a);
  };
  const POP_KINDS = ["raised", "closed", "escalation", "assigned", "mention", "reopened", "amc"];
  const poll = () => {
    if (document.visibilityState !== "visible" && !("Notification" in window && Notification.permission === "granted")) return;
    fetch("/api/live/?after=" + last, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(r => { if (!r.ok) throw r.status; return r.json(); })
      .then(d => {
        if (liveDot) liveDot.classList.remove("is-off");
        if (bellDot) {
          const before = bellDot.textContent;
          bellDot.textContent = d.unread; bellDot.hidden = !d.unread;
          if (String(d.unread) !== before && d.unread) { bellDot.classList.remove("is-bump"); void bellDot.offsetWidth; bellDot.classList.add("is-bump"); }
        }
        let sound = false, urgent = false;
        d.items.forEach(n => {
          last = Math.max(last, n.id);
          addToBell(n);
          if (body.dataset.popups && (POP_KINDS.includes(n.kind) || n.urgent)) {
            popup(n); desktop(n);
            if (n.kind === "raised" || n.urgent) { sound = true; urgent = urgent || n.urgent; }
          }
        });
        last = Math.max(last, d.last || 0);
        if (sound) chime(urgent);
      })
      .catch(() => { if (liveDot) liveDot.classList.add("is-off"); });
  };
  // lists and boards refresh every 5 minutes so countdowns stay honest, unless someone is busy on the page
  if ($(".sla") && !$("[data-raise]") && !$("[data-composer]") && !$("[data-board-form]")) {
    setInterval(() => {
      if (drawer.hidden && lb.hidden && !$("dialog[open]") && document.visibilityState === "visible") window.location.reload();
    }, 300000);
  }
  if (body.dataset.lastAlert !== undefined && $("#popups")) {
    setInterval(poll, 15000);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") poll(); });
  }
})();
