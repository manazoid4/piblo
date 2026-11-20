// Closes the mobile menu after a link is chosen, so in-page jumps are not hidden behind it.
document.querySelectorAll(".mobile-menu a").forEach(link =>
  link.addEventListener("click", () => link.closest("details").removeAttribute("open")));

// Hero film: plays muted only while on screen, never for people who ask for reduced motion
// (they get the poster). Controls are always shown; once someone pauses it, it stays paused.
const still = window.matchMedia("(prefers-reduced-motion: reduce)");
document.querySelectorAll("video[data-autoplay]").forEach(video => {
  if (still.matches || !("IntersectionObserver" in window)) return;
  let visible = false;
  const watcher = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    if (visible) video.play().catch(() => {}); else video.pause();
  }, { threshold: 0.4 });
  video.addEventListener("pause", () => { if (visible) watcher.disconnect(); });
  watcher.observe(video);
});

// Vercel Web Analytics queue stub (cookie-free). The CSP forbids inline scripts, so it lives here.
window.va = window.va || function () { (window.vaq = window.vaq || []).push(arguments); };
const track = (name) => { try { window.va("event", { name }); } catch {} };
document.querySelectorAll("[data-va]").forEach((el) => el.addEventListener("click", () => track(el.dataset.va)));

// Remember a recognised campaign for this tab so the owner can identify new signups.
const campaignSources = ["reddit", "x", "discord"];
const campaignParam = (new URLSearchParams(location.search).get("utm_source") || "").toLowerCase();
let campaignSource = campaignSources.includes(campaignParam) ? campaignParam : "";
try {
  if (campaignParam) {
    if (campaignSource) sessionStorage.setItem("piblo_campaign_source", campaignSource);
    else sessionStorage.removeItem("piblo_campaign_source");
  } else campaignSource = sessionStorage.getItem("piblo_campaign_source") || "";
} catch { /* signup still works without browser storage */ }
if (!campaignSources.includes(campaignSource)) campaignSource = "";
const campaignInput = document.querySelector("#waitlist-form input[name=source]");
if (campaignInput) campaignInput.value = campaignSource;

// Referral: remember ?ref= (letters/digits only) so a later visit to the form still credits the friend.
const REF = /^[a-z0-9]{6,12}$/;
const store = (fn) => { try { return fn(localStorage); } catch { return null; } };
const urlRef = (new URLSearchParams(location.search).get("ref") || "").toLowerCase();
if (REF.test(urlRef)) store((l) => l.setItem("piblo_ref", urlRef));
const savedRef = store((l) => l.getItem("piblo_ref")) || "";
const refInput = document.querySelector("#waitlist-form input[name=ref]");
if (refInput && REF.test(savedRef)) refInput.value = savedRef;

const shareBox = document.getElementById("share-box");
const showShare = (code) => {
  if (!shareBox || !REF.test(code || "")) return;
  const link = `https://piblo.co.uk/?ref=${code}`;
  const note = shareBox.querySelector(".share-note");
  shareBox.querySelector("#share-link").value = link;
  shareBox.hidden = false;
  shareBox.querySelector("#share-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(link); note.textContent = "Link copied."; }
    catch { shareBox.querySelector("#share-link").select(); note.textContent = "Copy the link above."; }
    track("referral_copy");
  };
  if (navigator.share) {
    const native = shareBox.querySelector("#share-native");
    native.hidden = false;
    native.onclick = () => { track("referral_share"); navigator.share({ title: "Piblo", text: "Have a look at Piblo.", url: link }).catch(() => {}); };
  }
};

// Waitlist form: posts to /api/waitlist (Resend). Without JavaScript the form posts natively
// and the API redirects back with ?subscribed=1 or 0, handled below.
const waitlist = document.getElementById("waitlist-form");
const waitlistStatus = waitlist?.querySelector(".waitlist-status");
const setWaitlistStatus = (text, ok) => {
  waitlistStatus.textContent = text;
  waitlistStatus.classList.toggle("ok", ok);
};
waitlist?.addEventListener("submit", async function (e) {
  e.preventDefault();
  const button = this.querySelector("button[type=submit]");
  const data = Object.fromEntries(new FormData(this));
  if (!data.email?.trim() || !data.consent) {
    return setWaitlistStatus("Please add your email and tick the box.", false);
  }
  const emailInput = this.querySelector('input[name="email"]');
  emailInput.setAttribute("aria-invalid", String(!emailInput.validity.valid));
  if (!emailInput.validity.valid) {
    setWaitlistStatus("Please enter a valid email address.", false);
    emailInput.focus();
    return;
  }
  button.disabled = true;
  setWaitlistStatus("Sending...", false);
  try {
    const res = await fetch("/api/waitlist", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(data),
    });
    const out = await res.json().catch(() => ({}));
    if (res.ok && out.ok) {
      this.reset();
      if (campaignInput) campaignInput.value = campaignSource;
      if (refInput && REF.test(savedRef)) refInput.value = savedRef;
      track("waitlist_signup");
      showShare(out.ref);
      setWaitlistStatus("You're on the list. We'll only email you about Piblo.", true);
    } else {
      setWaitlistStatus(out.reason === "invalid-email" ? "That email address doesn't look right." : "Sorry, that didn't go through. Please try again in a minute.", false);
    }
  } catch {
    setWaitlistStatus("Sorry, that didn't go through. Please check your connection and try again.", false);
  }
  button.disabled = false;
});
if (waitlistStatus) {
  const result = new URLSearchParams(location.search).get("subscribed");
  if (result === "1") {
    setWaitlistStatus("You're on the list. We'll only email you about Piblo.", true);
    track("waitlist_signup");
    showShare(new URLSearchParams(location.search).get("code"));
  }
  if (result === "0") setWaitlistStatus("Sorry, that didn't go through. Please try again.", false);
}
