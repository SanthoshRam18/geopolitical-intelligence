// Reviews for a page:
//   * "Post a review": a star rating, a review, suggestions, a name and an email,
//     sent to POST /reviews and stored by the orchestrator. The email is never shown.
//   * "Hear what people think": a box in the corner that opens into the reviews
//     longer than 100 words (GET /reviews/public).
//
//   <script src="/static/review.js" data-page="features" defer></script>
//
// The widget appends itself to <main> (or <body>) and styles itself, so any page
// can include it.
(() => {
  const script = document.currentScript;
  const page = script?.dataset.page || "engine";
  const MIN_WORDS = 100;
  const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  const css = `
  .rv-wrap { margin: 48px auto 24px; max-width: 1320px; padding: 0 18px; }
  .rv-card { position: relative; overflow: hidden; border-radius: 18px; padding: 26px 28px; display: flex; flex-wrap: wrap; gap: 18px; align-items: center; justify-content: space-between;
    background: linear-gradient(135deg, rgba(167,139,250,.16), rgba(92,200,255,.12) 55%, rgba(52,211,153,.12)); border: 1px solid rgba(167,139,250,.35); color: #e6ebf3; font-family: ${FONT}; }
  .rv-card h3 { margin: 0 0 4px; font-size: 20px; }
  .rv-card p { margin: 0; color: #a7b1c2; font-size: 14px; }
  .rv-stat { font-size: 14px; color: #cbd5e1; margin-top: 6px; }
  .rv-stat b { color: #fbbf24; }
  .rv-btn { border: 0; border-radius: 12px; padding: 13px 22px; font: 700 15px ${FONT}; cursor: pointer; color: #160a2b; background: linear-gradient(135deg, #f5b942, #f472b6); box-shadow: 0 8px 24px rgba(244,114,182,.25); }
  .rv-btn:hover { transform: translateY(-1px); }
  .rv-btn:disabled { opacity: .6; cursor: progress; transform: none; }
  .rv-dialog { border: 1px solid #2a3850; border-radius: 16px; padding: 0; width: min(560px, 94vw); background: #0f1622; color: #e6ebf3; font-family: ${FONT}; }
  .rv-dialog::backdrop { background: rgba(3,6,12,.72); backdrop-filter: blur(3px); }
  .rv-form { padding: 22px 24px 20px; display: grid; gap: 12px; }
  .rv-form h3 { margin: 0; font-size: 19px; }
  .rv-form label { display: grid; gap: 6px; font-size: 13px; color: #a7b1c2; }
  .rv-form small { color: #7c879a; font-size: 12px; }
  .rv-form textarea, .rv-form input { font: 15px ${FONT}; color: #e6ebf3; background: #0b111b; border: 1px solid #2a3850; border-radius: 10px; padding: 10px 12px; resize: vertical; }
  .rv-form textarea:focus, .rv-form input:focus { outline: none; border-color: #5cc8ff; }
  .rv-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media (max-width: 520px) { .rv-row { grid-template-columns: 1fr; } }
  .rv-stars { display: flex; gap: 6px; }
  .rv-stars button { font-size: 30px; line-height: 1; background: none; border: 0; cursor: pointer; color: #334155; padding: 0 2px; }
  .rv-stars button.on { color: #fbbf24; text-shadow: 0 0 12px rgba(251,191,36,.45); }
  .rv-count.ok { color: #4ade80; }
  .rv-actions { display: flex; gap: 10px; justify-content: flex-end; align-items: center; }
  .rv-ghost { background: transparent; color: #a7b1c2; border: 1px solid #2a3850; border-radius: 10px; padding: 10px 16px; cursor: pointer; font: 14px ${FONT}; }
  .rv-msg { font-size: 13px; color: #a7b1c2; margin-right: auto; }
  .rv-msg.err { color: #f87171; } .rv-msg.ok { color: #4ade80; }

  .rv-corner { position: fixed; right: 18px; bottom: 18px; z-index: 50; font-family: ${FONT}; color: #e6ebf3; display: flex; flex-direction: column; align-items: flex-end; gap: 10px; }
  .rv-toggle { display: inline-flex; align-items: center; gap: 9px; border: 1px solid rgba(167,139,250,.5); border-radius: 999px; padding: 11px 18px; cursor: pointer; color: #f3eaff;
    font: 700 14px ${FONT}; background: linear-gradient(135deg, rgba(91,33,182,.92), rgba(219,39,119,.92)); box-shadow: 0 12px 30px rgba(91,33,182,.45); }
  .rv-toggle:hover { transform: translateY(-1px); }
  .rv-toggle .n { background: rgba(255,255,255,.2); border-radius: 999px; padding: 1px 8px; font-size: 12px; }
  .rv-panel { width: min(400px, 92vw); max-height: min(70vh, 620px); display: none; flex-direction: column; border-radius: 18px; overflow: hidden;
    background: #0f1622; border: 1px solid #2a3850; box-shadow: 0 24px 60px rgba(0,0,0,.6); }
  .rv-panel.open { display: flex; }
  .rv-panel header { padding: 16px 18px 12px; border-bottom: 1px solid #1f2a3c; background: linear-gradient(135deg, rgba(167,139,250,.18), rgba(244,114,182,.12)); }
  .rv-panel header h4 { margin: 0; font-size: 16px; }
  .rv-panel header p { margin: 4px 0 0; color: #a7b1c2; font-size: 13px; }
  .rv-panel header button { float: right; background: none; border: 0; color: #a7b1c2; font-size: 20px; cursor: pointer; line-height: 1; }
  .rv-list { overflow-y: auto; padding: 12px 14px 14px; display: grid; gap: 10px; }
  .rv-item { border: 1px solid #1f2a3c; border-radius: 12px; padding: 12px 14px; background: #0b111b; }
  .rv-item .top { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }
  .rv-item .who { font-weight: 700; }
  .rv-item .when { color: #7c879a; }
  .rv-item .stars { color: #fbbf24; letter-spacing: 1px; font-size: 14px; margin: 2px 0 6px; }
  .rv-item .stars span { color: #334155; }
  .rv-item p { margin: 0; color: #cbd5e1; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
  .rv-item .more { background: none; border: 0; color: #5cc8ff; cursor: pointer; padding: 4px 0 0; font: 13px ${FONT}; }
  .rv-empty { color: #a7b1c2; font-size: 14px; padding: 8px 4px; }
  .rv-empty a { color: #5cc8ff; cursor: pointer; }
  .rv-founder-btn { border: 0; border-radius: 10px; padding: 10px 18px; font: 700 13px ${FONT}; cursor: pointer; color: #e6ebf3; background: linear-gradient(135deg, rgba(91,33,182,.7), rgba(219,39,119,.7)); border: 1px solid rgba(167,139,250,.4); }
  .rv-founder-btn:hover { background: linear-gradient(135deg, rgba(91,33,182,.9), rgba(219,39,119,.9)); }
  .rv-pin-dialog { border: 1px solid #2a3850; border-radius: 14px; padding: 0; width: min(360px, 92vw); background: #0f1622; color: #e6ebf3; font-family: ${FONT}; }
  .rv-pin-dialog::backdrop { background: rgba(3,6,12,.78); backdrop-filter: blur(4px); }
  .rv-pin-form { padding: 22px 22px 18px; display: grid; gap: 14px; }
  .rv-pin-form h3 { margin: 0; font-size: 17px; }
  .rv-pin-form p { margin: 0; color: #a7b1c2; font-size: 13px; }
  .rv-pin-input { font: 15px ${FONT}; color: #e6ebf3; background: #0b111b; border: 1px solid #2a3850; border-radius: 10px; padding: 10px 12px; width: 100%; box-sizing: border-box; letter-spacing: 2px; }
  .rv-pin-input:focus { outline: none; border-color: #a78bfa; }
  .rv-pin-err { color: #f87171; font-size: 13px; min-height: 18px; }
  .rv-pin-actions { display: flex; gap: 10px; justify-content: flex-end; }
  .rv-item.founder-only { border-color: rgba(167,139,250,.35); background: rgba(91,33,182,.08); }
  .rv-item .suggestions { margin-top: 8px; padding-top: 8px; border-top: 1px solid #1f2a3c; color: #a7b1c2; font-size: 13px; font-style: italic; }
  .rv-item .hidden-badge { display: inline-block; background: rgba(248,113,113,.15); border: 1px solid rgba(248,113,113,.4); color: #f87171; border-radius: 6px; font-size: 11px; padding: 1px 7px; margin-left: 6px; vertical-align: middle; }`;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.append(style);

  const make = (tag, attrs = {}, text) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  };
  const words = (text) => (text.match(/\S+/g) || []).length;

  // ------------------------------------------------------------- the review form
  const wrap = make("section", { class: "rv-wrap", "aria-label": "Reviews" });
  const card = make("div", { class: "rv-card" });
  const intro = make("div");
  intro.append(make("h3", {}, "How was it?"), make("p", {}, "Rate the project, tell us what worked, and suggest what to build next. Your review is stored with the project."));
  const stat = make("div", { class: "rv-stat" });
  intro.append(stat);
  const open = make("button", { class: "rv-btn", type: "button" }, "★ Post a review");
  const founderBtn = make("button", { class: "rv-founder-btn", type: "button", title: "Founder access: view all reviews" }, "🔑 Founder");
  const btnGroup = make("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap;" });
  btnGroup.append(open, founderBtn);
  card.append(intro, btnGroup);
  wrap.append(card);

  const dialog = make("dialog", { class: "rv-dialog" });
  const form = make("form", { class: "rv-form", method: "dialog", novalidate: "" });
  const stars = make("div", { class: "rv-stars", role: "radiogroup", "aria-label": "Rating" });
  let rating = 0;
  const paint = () => [...stars.children].forEach((b, i) => { b.classList.toggle("on", i < rating); b.setAttribute("aria-checked", String(i + 1 === rating)); });
  for (let i = 1; i <= 5; i += 1) {
    const star = make("button", { type: "button", role: "radio", "aria-label": `${i} star${i > 1 ? "s" : ""}` }, "★");
    star.addEventListener("click", () => { rating = i; paint(); });
    stars.append(star);
  }
  const review = make("textarea", { rows: "5", maxlength: "4000", placeholder: "What did you like, or not?" });
  const count = make("small", { class: "rv-count" });
  const showCount = () => {
    const n = words(review.value);
    count.className = `rv-count${n > MIN_WORDS ? " ok" : ""}`;
    count.textContent = n > MIN_WORDS ? `${n} words · this review can appear under "Hear what people think"` : `${n} words · reviews over ${MIN_WORDS} words can appear on the site`;
  };
  review.addEventListener("input", showCount);
  const suggestions = make("textarea", { rows: "3", maxlength: "2000", placeholder: "What should we add or improve?" });
  const name = make("input", { type: "text", maxlength: "80", placeholder: "Shown with a public review" });
  const email = make("input", { type: "email", maxlength: "254", placeholder: "you@example.com", required: "" });
  const msg = make("span", { class: "rv-msg" });
  const cancel = make("button", { type: "button", class: "rv-ghost" }, "Cancel");
  const submit = make("button", { type: "submit", class: "rv-btn" }, "Submit review");
  const field = (label, input, note) => { const l = make("label", {}, label); l.append(input); if (note) l.append(note); return l; };
  const ratingLabel = make("label", {}, "Your rating");
  ratingLabel.append(stars);
  const row = make("div", { class: "rv-row" });
  row.append(field("Your name (optional)", name), field("Your email", email, make("small", {}, "Never shown. Only the team sees it.")));
  const actions = make("div", { class: "rv-actions" });
  actions.append(msg, cancel, submit);
  form.append(make("h3", {}, page === "features" ? "Review the features" : "Review the GEOINT Engine"), ratingLabel,
    field("Your review", review, count), field("Suggestions", suggestions), row, actions);
  dialog.append(form);

  function openForm() {
    rating = 0; paint(); review.value = ""; suggestions.value = ""; msg.textContent = ""; msg.className = "rv-msg";
    submit.disabled = false; showCount();
    dialog.showModal();
  }
  open.addEventListener("click", openForm);
  cancel.addEventListener("click", () => dialog.close());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fail = (text) => { msg.className = "rv-msg err"; msg.textContent = text; };
    if (!rating) return fail("Pick a rating from 1 to 5 stars.");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim())) return fail("Enter a valid email address.");
    submit.disabled = true;
    msg.className = "rv-msg"; msg.textContent = "Sending…";
    try {
      const response = await fetch("/reviews", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page, rating, review: review.value, suggestions: suggestions.value, name: name.value, email: email.value.trim() }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save the review. Check the fields and try again.");
      msg.className = "rv-msg ok";
      msg.textContent = data.public ? "Thank you! It will appear under \"Hear what people think\"." : "Thank you! Your review is saved.";
      refresh();
      setTimeout(() => dialog.close(), 1800);
    } catch (error) {
      fail(error.message);
      submit.disabled = false;
    }
  });

  // ------------------------------------------------------- "Hear what people think"
  const corner = make("div", { class: "rv-corner" });
  const panel = make("section", { class: "rv-panel", "aria-label": "What people think" });
  const head = make("header");
  const close = make("button", { type: "button", "aria-label": "Close" }, "×");
  const subtitle = make("p", {}, "Reviews of 100+ words from visitors.");
  head.append(close, make("h4", {}, "Hear what people think about this"), subtitle);
  const list = make("div", { class: "rv-list" });
  panel.append(head, list);
  const toggle = make("button", { class: "rv-toggle", type: "button", "aria-expanded": "false" });
  const badge = make("span", { class: "n" }, "0");
  toggle.append(make("span", { "aria-hidden": "true" }, "💬"), document.createTextNode("Hear what people think"), badge);
  corner.append(panel, toggle);
  const setOpen = (on) => { panel.classList.toggle("open", on); toggle.setAttribute("aria-expanded", String(on)); };
  toggle.addEventListener("click", () => setOpen(!panel.classList.contains("open")));
  close.addEventListener("click", () => setOpen(false));

  function item(r) {
    const node = make("article", { class: "rv-item" });
    const top = make("div", { class: "top" });
    top.append(make("span", { class: "who" }, r.name || "Anonymous"), make("span", { class: "when" }, `${r.date} · ${r.page === "features" ? "Features" : "Engine"}`));
    const starLine = make("div", { class: "stars", "aria-label": `${r.rating} of 5 stars` }, "★".repeat(r.rating));
    starLine.append(make("span", {}, "★".repeat(5 - r.rating)));
    const all = r.review, cut = all.split(/\s+/).slice(0, 60).join(" ");
    const text = make("p", {}, cut.length < all.length ? `${cut}…` : all);
    node.append(top, starLine, text);
    if (cut.length < all.length) {
      const more = make("button", { class: "more", type: "button" }, "Read more");
      more.addEventListener("click", () => { const full = text.textContent === all; text.textContent = full ? `${cut}…` : all; more.textContent = full ? "Read more" : "Show less"; });
      node.append(more);
    }
    return node;
  }

  async function refresh() {
    try {
      const [sum, pub] = await Promise.all([fetch("/reviews/summary").then((r) => r.json()), fetch("/reviews/public").then((r) => r.json())]);
      stat.replaceChildren();
      if (sum.count) {
        stat.append(make("b", {}, `${Number(sum.average).toFixed(1)} ★`), document.createTextNode(` average from ${sum.count} review${sum.count === 1 ? "" : "s"}`));
        subtitle.textContent = `${Number(sum.average).toFixed(1)} ★ average from ${sum.count} review${sum.count === 1 ? "" : "s"}; those of 100+ words are shown here.`;
      } else {
        stat.textContent = "No reviews yet: be the first.";
      }
      const shown = pub.reviews || [];
      badge.textContent = String(shown.length);
      if (shown.length) {
        list.replaceChildren(...shown.map(item));
      } else {
        const empty = make("div", { class: "rv-empty" }, "No reviews to show yet. Reviews longer than 100 words appear here. ");
        const write = make("a", { role: "button", tabindex: "0" }, "Write one");
        write.addEventListener("click", () => { setOpen(false); openForm(); });
        empty.append(write);
        list.replaceChildren(empty);
      }
    } catch { /* reviews are a nicety; the page works without them */ }
  }

  // ------------------------------------------------------- Founder Pin
  const pinDialog = make("dialog", { class: "rv-pin-dialog" });
  const pinForm = make("form", { class: "rv-pin-form", method: "dialog", novalidate: "" });
  const pinErr = make("div", { class: "rv-pin-err" });
  const pinInput = make("input", { class: "rv-pin-input", type: "password", placeholder: "Enter founder pin", autocomplete: "off", maxlength: "30" });
  const pinCancel = make("button", { type: "button", class: "rv-ghost" }, "Cancel");
  const pinSubmit = make("button", { type: "submit", class: "rv-btn" }, "Unlock");
  const pinActions = make("div", { class: "rv-pin-actions" });
  pinActions.append(pinCancel, pinSubmit);
  pinForm.append(make("h3", {}, "🔑 Founder Access"), make("p", {}, "Enter the founder pin to view all reviews, including private ones."), pinInput, pinErr, pinActions);
  pinDialog.append(pinForm);
  pinCancel.addEventListener("click", () => { pinDialog.close(); pinErr.textContent = ""; pinInput.value = ""; });

  function founderItem(r) {
    const node = make("article", { class: `rv-item${r.hidden ? " founder-only" : ""}` });
    const top = make("div", { class: "top" });
    const nameEl = make("span", { class: "who" }, r.name || "Anonymous");
    if (r.hidden) nameEl.append(make("span", { class: "hidden-badge" }, "hidden"));
    top.append(nameEl, make("span", { class: "when" }, `${(r.created_at || "").slice(0, 10)} · ${r.page === "features" ? "Features" : "Engine"}`));
    const starLine = make("div", { class: "stars" }, "★".repeat(r.rating));
    starLine.append(make("span", {}, "★".repeat(5 - r.rating)));
    const all = r.review || "", cut = all.split(/\s+/).slice(0, 60).join(" ");
    const text = make("p", {}, cut.length < all.length ? `${cut}…` : (all || "(no review text)"));
    node.append(top, starLine, text);
    if (cut.length < all.length) {
      const more = make("button", { class: "more", type: "button" }, "Read more");
      more.addEventListener("click", () => { const full = text.textContent === all; text.textContent = full ? `${cut}…` : all; more.textContent = full ? "Read more" : "Show less"; });
      node.append(more);
    }
    if (r.suggestions && r.suggestions.trim()) {
      const sug = make("div", { class: "suggestions" });
      sug.append(make("b", {}, "Suggestion: "), document.createTextNode(r.suggestions));
      node.append(sug);
    }
    return node;
  }

  async function loadFounderReviews(pin) {
    const res = await fetch(`/reviews/all?pin=${encodeURIComponent(pin)}`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Invalid pin.");
    return (await res.json()).reviews || [];
  }

  function showFounderPanel(allRevs) {
    const header = panel.querySelector("header h4");
    if (header) header.textContent = "🔑 All reviews (Founder view)";
    subtitle.textContent = `${allRevs.length} total review${allRevs.length === 1 ? "" : "s"} · includes private and short ones`;
    const sorted = [...allRevs].reverse();
    if (sorted.length) {
      list.replaceChildren(...sorted.map(founderItem));
    } else {
      list.replaceChildren(make("div", { class: "rv-empty" }, "No reviews yet."));
    }
    badge.textContent = String(allRevs.length);
    setOpen(true);
  }

  pinForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    pinErr.textContent = "";
    pinSubmit.disabled = true;
    pinSubmit.textContent = "Checking…";
    try {
      const allRevs = await loadFounderReviews(pinInput.value);
      pinDialog.close();
      pinInput.value = "";
      showFounderPanel(allRevs);
    } catch (err) {
      pinErr.textContent = err.message;
    } finally {
      pinSubmit.disabled = false;
      pinSubmit.textContent = "Unlock";
    }
  });

  founderBtn.addEventListener("click", () => { pinErr.textContent = ""; pinInput.value = ""; pinDialog.showModal(); setTimeout(() => pinInput.focus(), 50); });

  const mount = () => { (document.querySelector("main") || document.body).append(wrap); document.body.append(dialog, pinDialog, corner); refresh(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
