(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  $$("[data-side]").forEach((b) => b.addEventListener("click", () => $("#side").classList.toggle("is-open")));
  const fl = $("[data-flash]");
  if (fl) setTimeout(() => fl.remove(), 6000);

  $$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); const t = b.textContent; b.textContent = "คัดลอกแล้ว"; setTimeout(() => (b.textContent = t), 1500); } catch (e) { prompt("คัดลอกข้อความนี้", b.dataset.copy); }
  }));

  // clickable table rows
  $$("tr[data-href]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("a,button,form,input,select")) location.href = tr.dataset.href; }));

  // confirm before destructive actions
  $$("[data-confirm]").forEach((el) => el.addEventListener("submit", (e) => { if (!confirm(el.dataset.confirm)) e.preventDefault(); }));

  /* notification bell: poll every 30 s so every admin sees new bookings without refreshing */
  const bell = $("[data-bell]"), pop = $("[data-bell-pop]");
  let lastCount = Number(($("[data-bell-count]") || {}).textContent || 0);
  const draw = (d) => {
    let b = $("[data-bell-count]");
    if (d.count && !b) { b = document.createElement("span"); b.className = "badge"; b.style.cssText = "background:var(--taken);color:#fff"; b.dataset.bellCount = ""; bell.appendChild(b); }
    if (b) { b.textContent = d.count; b.hidden = !d.count; }
    pop.innerHTML = (d.items.length ? d.items.map((n) => `<a href="${esc(n.link) || "/admin/notifications"}">${esc(n.title)}<small>${esc(n.body)}</small></a>`).join("") : `<a href="/admin/notifications">ไม่มีการแจ้งเตือนใหม่<small>ดูทั้งหมด</small></a>`)
      + `<form method="post" action="/admin/notifications/read" style="padding:8px 14px"><button class="btn btn--ghost btn--sm">ทำเครื่องหมายว่าอ่านแล้ว</button> <a href="/admin/notifications" style="display:inline;border:0;padding:0">ทั้งหมด</a></form>`;
    if (d.count > lastCount && document.hidden === false) { document.title = `(${d.count}) ` + document.title.replace(/^\(\d+\)\s/, ""); }
    lastCount = d.count;
  };
  const poll = () => fetch("/admin/api/notifications").then((r) => r.ok && r.json()).then((d) => d && draw(d)).catch(() => {});
  if (bell) {
    bell.addEventListener("click", () => { const o = !pop.classList.contains("is-open"); pop.classList.toggle("is-open", o); bell.setAttribute("aria-expanded", o); if (o) poll(); });
    document.addEventListener("click", (e) => { if (!e.target.closest("[data-bell],[data-bell-pop]")) pop.classList.remove("is-open"); });
    setInterval(poll, 30000);
  }

  /* live conflict check on the manual booking form */
  const nf = $("[data-newbooking]");
  if (nf) {
    const box = $("[data-check]");
    const run = async () => {
      const buyout = nf.buyout && nf.buyout.checked;
      $$("[data-single]").forEach((el) => (el.hidden = buyout));
      const house = buyout ? "" : nf.house_id.value;
      const ci = nf.check_in.value, co = nf.check_out.value;
      if (!ci || !co || co <= ci) { box.innerHTML = ""; return; }
      if (buyout) {
        const ids = $$("option", nf.house_id).map((o) => o.value).filter(Boolean);
        const res = await Promise.all(ids.map((id) => fetch(`/admin/api/check?house=${id}&ci=${ci}&co=${co}`).then((r) => r.json())));
        const bad = res.map((r, i) => [r, $$("option", nf.house_id).filter((o) => o.value)[i].textContent]).filter(([r]) => !r.free);
        box.innerHTML = bad.length
          ? `<div class="conflict"><strong>⚠️ เหมาทั้งรีสอร์ทไม่ได้ มีบ้านไม่ว่าง ${bad.length} หลัง</strong><ul>${bad.map(([r, n]) => `<li>${esc(n)}: ${r.conflicts.filter((c) => c.kind !== "pending").map((c) => esc(c.code || c.reason) + " " + c.start + " ถึง " + c.end).join(", ")}</li>`).join("")}</ul></div>`
          : `<div class="ok-box">✅ ทั้ง ${ids.length} หลังว่างในช่วงนี้ เหมาได้</div>`;
        return;
      }
      if (!house) { box.innerHTML = ""; return; }
      const guests = (+nf.adults.value || 1) + (+nf.children.value || 0);
      const r = await fetch(`/admin/api/check?house=${house}&ci=${ci}&co=${co}&guests=${guests}`);
      const d = await r.json();
      const pend = d.conflicts.filter((c) => c.kind === "pending");
      if (!d.free) {
        const hard = d.conflicts.filter((c) => c.kind !== "pending");
        box.innerHTML = `<div class="conflict"><strong>⚠️ ห้องไม่ว่าง! ${esc(nf.house_id.selectedOptions[0].textContent)} ถูกจองแล้ว</strong><ul>${hard.map((c) => `<li>${c.kind === "block" ? "ปิดห้อง: " + esc(c.reason) : `<a href="/admin/bookings/${c.booking_id}">${esc(c.code)}</a> ช่องทาง ${esc(c.channel)} (${esc(c.status)})`} ${c.start} ถึง ${c.end}</li>`).join("")}</ul>`
          + (d.free_from ? `<p style="margin:8px 0 0">ห้องนี้จะว่างอีกครั้งตั้งแต่ <b>${d.free_from}</b></p>` : "")
          + (d.alternatives && d.alternatives.length ? `<p style="margin:6px 0 0">บ้านที่ยังว่างช่วงนี้: ${d.alternatives.map((a) => `<button type="button" class="btn btn--ghost btn--sm" data-alt="${a.id}">${esc(a.name)}</button>`).join(" ")}</p>` : "<p style='margin:6px 0 0'>ไม่มีบ้านหลังอื่นว่างในช่วงนี้</p>")
          + `</div>`;
        $$("[data-alt]", box).forEach((b) => b.addEventListener("click", () => { nf.house_id.value = b.dataset.alt; run(); }));
      } else {
        const q = d.quote;
        box.innerHTML = `<div class="ok-box">✅ ว่าง ${q.nights} คืน ราคาตามระบบ <b>${Number(q.total).toLocaleString()}</b> บาท${q.discounts.length ? " (รวม " + q.discounts.map(esc).join(", ") + ")" : ""}${q.gifts.length ? "<br>ของแถม: " + q.gifts.map(esc).join(", ") : ""}${q.errors.length ? "<br><b style='color:var(--taken)'>" + esc(q.errors[0]) + "</b>" : ""}</div>`
          + (pend.length ? `<div class="conflict" style="border-color:#F0A33A;background:#FFF6E8"><strong style="color:#9A5A00">มีคำขอที่รออนุมัติในช่วงนี้</strong><ul>${pend.map((c) => `<li><a href="/admin/bookings/${c.booking_id}">${esc(c.code)}</a> ${c.start} ถึง ${c.end}</li>`).join("")}</ul></div>` : "");
      }
    };
    nf.addEventListener("change", run);
    run();
    const ch = nf.channel, ag = $("[data-agent-row]");
    const syncAgent = () => { if (ag) ag.hidden = ch.value === "website"; };
    ch.addEventListener("change", syncAgent); syncAgent();
  }

  /* calendar: click a free cell to start a booking, export the visible month as PNG for Facebook */
  $$(".cal td.c-free").forEach((td) => td.addEventListener("click", () => {
    location.href = `/admin/bookings/new?house=${td.dataset.h}&ci=${td.dataset.d}&co=${td.dataset.n}`;
  }));
  const exp = $("[data-export]");
  if (exp) exp.addEventListener("click", async () => {
    const data = JSON.parse(exp.dataset.export);
    await document.fonts.ready;
    const cw = 40, rh = 46, lw = 170, top = 110;
    const W = lw + cw * data.days.length + 20, H = top + rh * data.houses.length + 60;
    const c = document.createElement("canvas"); c.width = W * 2; c.height = H * 2;
    const g = c.getContext("2d"); g.scale(2, 2);
    g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
    g.fillStyle = "#2E1D12"; g.fillRect(0, 0, W, 70);
    g.fillStyle = "#C8A24A"; g.font = "28px Chonburi"; g.fillText(data.title, 20, 46);
    g.fillStyle = "#fff"; g.font = "16px 'Plex Looped'"; g.textAlign = "right"; g.fillText(data.sub, W - 20, 44); g.textAlign = "left";
    g.font = "600 13px 'Plex Looped'";
    data.days.forEach((d, i) => {
      const x = lw + i * cw;
      if (d.w) { g.fillStyle = "#F6F1E8"; g.fillRect(x, top - 34, cw, 34 + rh * data.houses.length); }
      g.fillStyle = "#2C6E80"; g.textAlign = "center"; g.fillText(d.d, x + cw / 2, top - 12);
    });
    g.textAlign = "left";
    data.houses.forEach((h, r) => {
      const y = top + r * rh;
      g.fillStyle = "#1E1A16"; g.font = "600 15px 'Plex Looped'"; g.fillText(h.name, 14, y + 28);
      h.cells.forEach((on, i) => { if (on) { g.fillStyle = "#C0392B"; g.fillRect(lw + i * cw + 3, y + 4, cw - 6, rh - 8); } });
      g.strokeStyle = "#E2DCD2"; g.beginPath(); g.moveTo(0, y + rh); g.lineTo(W, y + rh); g.stroke();
    });
    g.strokeStyle = "#E2DCD2";
    data.days.forEach((_, i) => { g.beginPath(); g.moveTo(lw + i * cw, top - 34); g.lineTo(lw + i * cw, top + rh * data.houses.length); g.stroke(); });
    g.fillStyle = "#57504A"; g.font = "14px 'Plex Looped'";
    g.fillStyle = "#C0392B"; g.fillRect(14, H - 38, 16, 16); g.fillStyle = "#57504A"; g.fillText("ไม่ว่าง   ช่องว่าง = ว่าง จองได้   " + data.phone, 38, H - 25);
    const a = document.createElement("a"); a.download = `ตารางห้องพัก-${data.file}.png`; a.href = c.toDataURL("image/png"); a.click();
  });
})();
