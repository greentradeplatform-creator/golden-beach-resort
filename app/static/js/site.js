(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const T = (window.GBR && window.GBR.t) || {};
  const lang = document.documentElement.lang;
  const LP = lang === "en" ? "/en" : "";
  const fmt = (n) => Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const thMonths = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."];
  const enMonths = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const shortDate = (iso) => { const d = new Date(iso + "T00:00"); return `${d.getDate()} ${(lang === "en" ? enMonths : thMonths)[d.getMonth()]}`; };
  const addDays = (iso, n) => { const d = new Date(iso + "T00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

  /* menu */
  const nav = $("#nav");
  $$("[data-menu]").forEach((b) => b.addEventListener("click", () => {
    const open = !nav.classList.contains("is-open");
    nav.classList.toggle("is-open", open);
    $$("[data-menu]").forEach((x) => x.setAttribute("aria-expanded", open));
  }));

  /* header turns solid once the hero is scrolled past */
  const head = $("#head");
  if (head && !head.classList.contains("is-solid")) {
    const hero = $(".hero");
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { head.classList.remove("is-solid"); head.style.position = ""; }
      else { head.classList.add("is-solid"); head.style.position = "fixed"; head.style.left = 0; head.style.right = 0; }
    }, { rootMargin: "-80px 0px 0px 0px" });
    if (hero) io.observe(hero);
  }

  /* flash message */
  const fl = $("[data-flash]");
  if (fl) setTimeout(() => fl.remove(), 5000);

  /* keep check-out after check-in on every date pair */
  $$("form").forEach((f) => {
    const ci = f.querySelector("[name=ci],[name=check_in]");
    const co = f.querySelector("[name=co],[name=check_out]");
    if (!ci || !co) return;
    ci.addEventListener("change", () => {
      co.min = addDays(ci.value, 1);
      if (!co.value || co.value <= ci.value) co.value = addDays(ci.value, 1);
      co.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  /* ---------- site plan ---------- */
  const plan = $("[data-plan]");
  if (plan) {
    const form = $("[data-plan-form]");
    const plaques = $$(".plaque", plan);
    let state = {};
    const params = () => new URLSearchParams(new FormData(form));

    const select = (id) => {
      plaques.forEach((p) => p.setAttribute("aria-pressed", p.dataset.house === String(id)));
      $("[data-pick-empty]").hidden = true;
      $$("[data-pick-house]").forEach((el) => (el.hidden = el.dataset.pickHouse !== String(id)));
      paintPick(id);
      if (window.innerWidth < 900) $("[data-pick]").scrollIntoView({ behavior: "smooth", block: "start" });
    };

    const paintPick = (id) => {
      const el = $(`[data-pick-house="${id}"]`);
      const s = state[id];
      if (!el || !s) return;
      const q = params();
      const nights = s.nights || 1;
      $("[data-pick-label]", el).textContent = `${T.total} ${nights} ${T.nights}`;
      $("[data-pick-total]", el).textContent = fmt(s.total);
      const note = $("[data-pick-note]", el);
      const book = $("[data-pick-book]", el);
      if (!s.free) {
        note.textContent = T.takenNote + (s.next ? ` ${T.freeFrom} ${shortDate(s.next)}` : "");
        book.setAttribute("aria-disabled", "true"); book.style.opacity = .45; book.style.pointerEvents = "none";
      } else if (!s.fits) {
        note.textContent = T.nofitNote;
        book.removeAttribute("aria-disabled"); book.style.opacity = 1; book.style.pointerEvents = "";
      } else {
        note.textContent = "";
        book.removeAttribute("aria-disabled"); book.style.opacity = 1; book.style.pointerEvents = "";
      }
      const u = new URL(book.href, location.origin);
      u.searchParams.set("ci", q.get("ci")); u.searchParams.set("co", q.get("co"));
      book.href = u.pathname + u.search;
    };

    const paint = () => {
      plaques.forEach((p) => {
        const s = state[p.dataset.house]; if (!s) return;
        p.classList.toggle("is-taken", !s.free);
        p.classList.toggle("is-nofit", s.free && !s.fits);
        $("[data-state]", p).textContent = !s.free ? T.taken : (!s.fits ? T.nofit : T.free);
        $("[data-price]", p).textContent = fmt(s.total / (s.nights || 1));
      });
      const q = params();
      const lg = $("[data-legend-dates]");
      if (lg) lg.textContent = `${T.showing} ${shortDate(q.get("ci"))}${q.get("co") !== addDays(q.get("ci"), 1) ? " – " + shortDate(q.get("co")) : ""}`;
      const on = plaques.find((p) => p.getAttribute("aria-pressed") === "true");
      if (on) paintPick(on.dataset.house);
    };

    const load = async () => {
      const q = params();
      if (!q.get("ci") || !q.get("co") || q.get("co") <= q.get("ci")) return;
      const r = await fetch(`/api/availability?${q}`);
      if (!r.ok) return;
      state = await r.json();
      paint();
      history.replaceState(null, "", `${location.pathname}?${q}#plan`);
    };

    plaques.forEach((p) => p.addEventListener("click", () => select(p.dataset.house)));
    form.addEventListener("change", load);
    load();

    // the hero search scrolls to the plan instead of reloading
    const search = $("[data-search]");
    if (search) search.addEventListener("submit", (e) => {
      e.preventDefault();
      ["ci", "co", "guests"].forEach((k) => (form.elements[k].value = search.elements[k].value));
      load().then(() => $("#plan").scrollIntoView({ behavior: "smooth" }));
    });
  }

  /* ---------- live quote (house page widget + booking form) ---------- */
  const qBox = $("[data-quote]");
  if (qBox) {
    const form = qBox.closest("form");
    const out = $("[data-quote-out]");
    const submit = $("[data-quote-submit]", form);
    const L = JSON.parse(qBox.dataset.labels || "{}");
    let timer;
    const run = async () => {
      const fd = new FormData(form);
      const house = fd.get("house_id");
      const ci = fd.get("check_in"), co = fd.get("check_out");
      if (!house || !ci || !co || co <= ci) { out.innerHTML = ""; return; }
      const addons = [...form.querySelectorAll("[name^=addon_]")].map((s) => `${s.name.slice(6)}:${s.value}`).join(",");
      const qs = new URLSearchParams({ house, ci, co, adults: fd.get("adults") || 2, children: fd.get("children") || 0, phone: fd.get("phone") || "", addons });
      qs.set("lang", lang);
      const r = await fetch(`/api/quote?${qs}`);
      const q = await r.json();
      let h = "";
      if (q.errors && q.errors.length) h += `<p class="quote__err">${q.errors[0]}</p>`;
      if (q.available === false) {
        h += `<p class="quote__err">${L.taken}${q.next_free ? " " + L.freeFrom + " " + shortDate(q.next_free) : ""}</p>`;
        if (q.alternatives && q.alternatives.length) h += `<p class="small">${L.alts} ${q.alternatives.map((a) => `<a href="${LP}/book?house=${a.slug}&ci=${ci}&co=${co}">${a.name}</a>`).join(", ")}</p>`;
      }
      if (q.nights && q.nights.length) {
        const byPrice = {};
        q.nights.forEach((n) => { const k = n.price + (n.label ? "|" + n.label : ""); (byPrice[k] = byPrice[k] || { n: 0, price: n.price, label: n.label }).n++; });
        Object.values(byPrice).forEach((g) => { h += `<div class="quote__line"><span>${fmt(g.price)} × ${g.n} ${L.nights}${g.label ? " (" + g.label + ")" : ""}</span><span>${fmt(g.price * g.n)}</span></div>`; });
        if (q.extra_guest_total) h += `<div class="quote__line"><span>${L.extra}</span><span>${fmt(q.extra_guest_total)}</span></div>`;
        q.addons.forEach((a) => (h += `<div class="quote__line"><span>${a.name} × ${a.qty}</span><span>${fmt(a.amount)}</span></div>`));
        q.discounts.forEach((d) => (h += `<div class="quote__line quote__line--disc"><span>${d.name}</span><span>−${fmt(d.amount)}</span></div>`));
        h += `<div class="quote__line quote__line--total"><span>${L.total}</span><span>${fmt(q.total)} ${L.baht}</span></div>`;
        q.gifts.forEach((g) => (h += `<div class="quote__gift">${L.gift} ${g}</div>`));
      }
      out.innerHTML = h;
      if (submit) submit.disabled = q.available === false || (q.errors && q.errors.length > 0);
      document.dispatchEvent(new CustomEvent("quote", { detail: q }));
    };
    form.addEventListener("change", () => { clearTimeout(timer); timer = setTimeout(run, 150); });
    form.addEventListener("input", (e) => { if (e.target.name === "phone") { clearTimeout(timer); timer = setTimeout(run, 600); } });
    run();
  }

  /* lightbox for galleries */
  const lb = $("[data-lb]");
  if (lb) {
    $$("[data-zoom]").forEach((img) => img.addEventListener("click", () => {
      $("img", lb).src = img.dataset.zoom; lb.classList.add("is-open"); $("button", lb).focus();
    }));
    const close = () => lb.classList.remove("is-open");
    lb.addEventListener("click", (e) => { if (e.target !== $("img", lb)) close(); });
    document.addEventListener("keydown", (e) => e.key === "Escape" && close());
  }

  /* copy buttons */
  $$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); const t = b.textContent; b.textContent = b.dataset.done || "✓"; setTimeout(() => (b.textContent = t), 1500); } catch (e) { /* ignore */ }
  }));
})();

// online payment: Opn form + wait for the QR to be paid
(function () {
  const btn = document.getElementById("opn-open");
  if (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      if (!window.OmiseCard) { const t = +(btn.dataset.tries || 0); if (t > 5) return; btn.dataset.tries = t + 1; setTimeout(function () { btn.click(); }, 1200); return; }
      OmiseCard.configure({ publicKey: btn.dataset.key });
      OmiseCard.open({
        amount: Number(btn.dataset.amount), currency: "THB", frameLabel: btn.dataset.label,
        submitLabel: btn.dataset.submit, defaultPaymentMethod: btn.dataset.default,
        otherPaymentMethods: btn.dataset.other,
        onCreateTokenSuccess: function (nonce) {
          const f = document.getElementById("opn-form");
          if (nonce.indexOf("tokn_") === 0) f.omiseToken.value = nonce; else f.omiseSource.value = nonce;
          f.submit();
        }
      });
    });
  }
  const box = document.querySelector("[data-poll]");
  if (box) {
    let n = 0;
    const tick = function () {
      if (++n > 240) return;                      // stop after ~20 minutes
      fetch(box.dataset.poll, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (d && (d.booking === "confirmed" || d.charge === "successful" || d.charge === "failed" || d.charge === "expired")) location.reload();
          else setTimeout(tick, 5000);
        }).catch(function () { setTimeout(tick, 8000); });
    };
    setTimeout(tick, 4000);
  }
})();
