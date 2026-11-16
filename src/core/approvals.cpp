#include "approvals.h"

#include <algorithm>
#include <string>
#include <vector>

#include <approval_format.h>
#include <easy_keys.h>
#include <nod_hold.h>
#include <poll_backoff.h>

#include "../audio/sfx.h"
#include "../input/keyboard.h"
#include "../net/host_worker.h"
#include "../net/mazhost.h"
#include "../ui/theme.h"
#include "sys.h"

namespace maz {
namespace approvals {

using namespace theme;

namespace {

struct Req {
    std::string id, tool, summary, project, session;
    bool        nod = false;
    uint32_t    deadline = 0;  // millis() when Core's 60 s window closes
};

enum class Phase : uint8_t { Idle, Showing, Sending, Result };

std::vector<Req>         gQueue;
Phase                    gPhase = Phase::Idle;
std::string              gSendId, gSendDecision, gBeeped, gMsg;
std::vector<std::string> gDone;       // ids already answered/vanished; ignored if Core still lists them briefly
uint16_t                 gMsgColor = 0;
uint32_t                 gMsgUntil = 0, gNextPoll = 0;
uint8_t                  gRetries = 0, gFails = 0;
bool                     gSubmitted = false;
// The Nod hold-to-approve: gHoldKey is the Y/ENTER code being held (0 = none).
uint8_t                  gHoldKey = 0;
std::string              gHoldId;  // the request id the hold started on, so a front-swap can't inherit it
uint32_t                 gHoldStart = 0, gHintUntil = 0;

bool done(const std::string& id) { return std::find(gDone.begin(), gDone.end(), id) != gDone.end(); }
void markDone(const std::string& id) {
    if (done(id)) return;
    gDone.push_back(id);
    if (gDone.size() > 12) gDone.erase(gDone.begin());
}
void drop(const std::string& id) {
    gQueue.erase(std::remove_if(gQueue.begin(), gQueue.end(), [&](const Req& r) { return r.id == id; }), gQueue.end());
}
void toast(const char* msg, uint16_t color, uint32_t ms = 1000) {
    gMsg = msg; gMsgColor = color; gMsgUntil = millis() + ms; gPhase = Phase::Result;
}

bool absorb(const host::BuddyPoll& p) {
    const uint32_t now = millis();
    Sys.buddy = p.ok ? (p.agent == "needs_you" ? 2 : p.agent == "working" ? 1 : 0) : 0;
    if (!p.ok) { if (gFails < 255) ++gFails; if (gFails >= 3) gQueue.clear(); return false; }
    gFails = 0;
    std::vector<Req> next;
    for (const host::BuddyItem& it : p.items) {
        if (done(it.id)) continue;
        Req r{it.id, it.tool, it.summary, it.project, it.session, it.nod,
              now + static_cast<uint32_t>(it.left) * 1000u};
        for (const Req& old : gQueue) if (old.id == it.id) { r.deadline = old.deadline; break; }
        next.push_back(std::move(r));
    }
    // The request we are showing vanished from Core while we were deciding on it:
    // it was answered or cancelled at the terminal.
    if (gPhase == Phase::Showing && !gQueue.empty()) {
        const std::string shown = gQueue.front().id;
        const bool still = std::any_of(next.begin(), next.end(), [&](const Req& r) { return r.id == shown; });
        if (!still) { markDone(shown); toast("Handled at terminal", DIM); }
    }
    gQueue = std::move(next);
    return !gQueue.empty();
}

void send(const char* decision) {
    gHoldKey = 0;
    if (gQueue.empty() || gPhase != Phase::Showing) return;
    gSendId = gQueue.front().id;
    gSendDecision = decision;
    gRetries = 0;
    gSubmitted = false;
    gPhase = Phase::Sending;
}

// Line i of Core's three-line detail (head, amount, to recipient) (Core already truncated and cased them).
std::string nodLine(const std::string& s, int i) {
    size_t a = 0;
    while (i-- > 0 && a != std::string::npos) { a = s.find('\n', a); if (a != std::string::npos) a++; }
    if (a == std::string::npos) return "";
    return s.substr(a, s.find('\n', a) - a);
}

std::string clip(const std::string& s, size_t from, size_t n, bool more) {
    if (from >= s.size()) return "";
    std::string out = s.substr(from, n);
    if (more && s.size() > from + n && out.size() > 2) { out.resize(out.size() - 2); out += ".."; }
    return out;
}

}  // namespace

bool active() { return gPhase != Phase::Idle; }
bool holding() { return gHoldKey != 0; }

bool update(bool screenOff) {
    bool arrived = false;
    const uint32_t now = millis();

    // 1. Collect worker results that belong to us.
    if (host_worker::state() == host_worker::State::Done) {
        if (host_worker::jobKind() == host_worker::JobKind::BuddyPoll) {
            host::BuddyPoll p;
            if (host_worker::takeBuddyPollResult(p)) absorb(p);
        } else if (host_worker::jobKind() == host_worker::JobKind::BuddyDecide) {
            host_worker::BuddyDecideResult r;
            if (host_worker::takeBuddyDecideResult(r) && r.id.rfind("al-", 0) != 0) {  // "al-": generic alert answer, see alerts.cpp
                if (r.ok) {
                    markDone(r.id); drop(r.id);
                    if (r.applied == "allow" || r.applied == "allow_all") { toast("Approved", OK); sfx::confirm(); }
                    else if (r.applied == "deny") toast("Denied", ERR);
                    else if (r.applied == "gone") toast("Expired", DIM);
                    else toast("Handled at terminal", DIM);
                    gNextPoll = now + 400;  // pick up the next queued request quickly
                } else if (gRetries++ < 2) {
                    gSubmitted = false;  // retry on the next idle worker slot
                } else {
                    markDone(r.id); drop(r.id);
                    toast("hub unreachable - use terminal", WARN, 1800);
                }
            }
        }
    }

    // 2a. Nod hold: approve once Y/ENTER has stayed down long enough; drop the hold if the
    // key is gone (lost release event) or the request is no longer on screen.
    if (gHoldKey) {
        if (gPhase != Phase::Showing || gQueue.empty() || !gQueue.front().nod || gQueue.front().id != gHoldId || !KB.held(gHoldKey)) gHoldKey = 0;
        else if (nod::holdDone(now - gHoldStart)) send("allow");
    }

    // 2. Phase housekeeping.
    if (gPhase == Phase::Result && static_cast<int32_t>(now - gMsgUntil) >= 0) gPhase = Phase::Idle;
    if (gPhase == Phase::Showing || gPhase == Phase::Idle) {
        // Timed-out requests vanish on their own (the hook already fell back to the terminal).
        while (!gQueue.empty() && static_cast<int32_t>(now - gQueue.front().deadline) >= 0) {
            markDone(gQueue.front().id);
            gQueue.erase(gQueue.begin());
        }
        if (!gQueue.empty() && gPhase == Phase::Idle) {
            gPhase = Phase::Showing;
            if (gQueue.front().id != gBeeped) {
                gBeeped = gQueue.front().id;
                sfx::approval();
                arrived = true;
            }
        } else if (gQueue.empty() && gPhase == Phase::Showing) {
            gPhase = Phase::Idle;
        }
    }

    // 3. One worker job per tick at most: pending decision first, else the slow poll.
    if (host_worker::state() != host_worker::State::Idle) return arrived;
    if (!host::configured() || !Sys.wifiConnected) return arrived;
    if (gPhase == Phase::Sending && !gSubmitted) {
        gSubmitted = host_worker::submitBuddyDecide(gSendId, gSendDecision);
        return arrived;
    }
    if (static_cast<int32_t>(now - gNextPoll) >= 0) {
        const bool hot = gPhase == Phase::Showing || gPhase == Phase::Result;
        // Core unreachable (laptop asleep): each failed poll doubles the wait, capped at 15 s so a
        // returning Core is still seen well inside the 60 s approval window.
        gNextPoll = now + ::nod::backoffMs(hot ? 2500u : screenOff ? 12000u : 5000u, gFails, 15000u);
        host_worker::submitBuddyPoll();
    }
    return arrived;
}

bool handleKey(const KeyEvent& e) {
    if (gPhase == Phase::Idle) return false;
    const bool nodShown = gPhase == Phase::Showing && !gQueue.empty() && gQueue.front().nod;
    if (!e.down) {
        // Released before the hold finished: nothing happens except a hint.
        if (gHoldKey && e.code == gHoldKey) { gHoldKey = 0; gHintUntil = millis() + 1500; }
        return true;
    }
    if (gPhase != Phase::Showing) return true;  // swallow while sending / confirming
    if (e.code == KEY_Y || e.code == KEY_ENTER) {
        if (!nodShown) send("allow");
        else if (!gHoldKey) { gHoldKey = e.code; gHoldStart = millis(); gHoldId = gQueue.front().id; }  // key repeats must not restart the hold
    }
    else if (easy_keys::isRejectKey(e.code)) send("deny");  // DEL / N
    else if (easy_keys::isLaterKey(e.code)) {               // L / ESC: leave it pending at Core, never deny
        const std::string id = gQueue.front().id;
        markDone(id); drop(id);
        toast("Later: still waiting", DIM, 1200);
    }
    // No session-wide approval shortcut: each action needs a deliberate decision.
    return true;
}

void render(M5Canvas& g) {
    if (gPhase == Phase::Idle) return;
    const int x = 4, y = 18, w = SCREEN_W - 8, h = 100;
    g.fillRoundRect(x, y, w, h, 4, PANEL);
    g.drawRoundRect(x, y, w, h, 4, ACCENT);
    g.setTextDatum(top_left);

    if (gPhase == Phase::Result) {
        g.setFont(&fonts::Font4);
        g.setTextDatum(middle_center);
        g.setTextColor(gMsgColor, PANEL);
        g.drawString(gMsg.c_str(), SCREEN_W / 2, y + h / 2);
        g.setTextDatum(top_left);
        return;
    }
    if (gQueue.empty()) return;
    const Req& r = gQueue.front();
    const int32_t leftMs = static_cast<int32_t>(r.deadline - millis());
    const int left = leftMs > 0 ? (leftMs + 999) / 1000 : 0;

    // Detailed approvals carry amount/recipient lines; without them (older Core, plain agent
    // asks) every line below renders exactly as before. Accept both Core heads (see header).
    const bool details = approvalHasDetails(r.summary);
    const std::string head = details ? nodLine(r.summary, 0) : "CLAUDE CODE NEEDS YOU";
    g.setFont(&fonts::Font0);
    g.setTextColor(ACCENT, PANEL);
    g.drawString(head.c_str(), x + 6, y + 5);
    if (gQueue.size() > 1) {
        char c[12];
        snprintf(c, sizeof(c), "+%u more", static_cast<unsigned>(gQueue.size() - 1));
        g.setTextDatum(top_right);
        g.setTextColor(WARN, PANEL);
        g.drawString(c, x + w - 6, y + 5);
        g.setTextDatum(top_left);
    }
    g.setFont(&fonts::Font2);
    g.setTextColor(TEXT, PANEL);
    g.drawString(clip(r.tool, 0, 14, false).c_str(), x + 6, y + 17);
    if (!r.project.empty()) {
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_right);
        g.setTextColor(ACCENT2, PANEL);
        g.drawString(clip(r.project, 0, 18, false).c_str(), x + w - 6, y + 22);
        g.setTextDatum(top_left);
    }
    if (details) {
        g.setFont(&fonts::Font2);
        g.setTextColor(WARN, PANEL);
        g.drawString(nodLine(r.summary, 1).c_str(), x + 6, y + 36);
    }
    g.setFont(&fonts::Font0);
    g.setTextColor(DIM, PANEL);
    const size_t N = 36;
    if (details) g.drawString(nodLine(r.summary, 2).c_str(), x + 6, y + 54);
    else {
        g.drawString(clip(r.summary, 0, N, false).c_str(), x + 6, y + 40);
        g.drawString(clip(r.summary, N, N, true).c_str(), x + 6, y + 52);
    }

    // Countdown bar (60 s window).
    const int bw = w - 44;
    g.drawRect(x + 6, y + 70, bw, 7, LINE);
    const int fill = std::min(bw - 2, (bw - 2) * left / 60);
    g.fillRect(x + 7, y + 71, fill, 5, left > 15 ? OK : left > 7 ? WARN : ERR);
    char t[8];
    snprintf(t, sizeof(t), "%ds", left);
    g.setTextColor(TEXT, PANEL);
    g.drawString(t, x + bw + 12, y + 70);

    // Nod: a hold bar under the hint fills while ENTER/Y stays down.
    const bool hint = r.nod && gHintUntil && static_cast<int32_t>(millis() - gHintUntil) < 0;
    g.setTextColor(gPhase == Phase::Sending || hint ? WARN : ACCENT, PANEL);
    g.drawString(gPhase == Phase::Sending ? "Sending..." : hint ? easy_keys::kApprovalTapHint
                 : r.nod ? easy_keys::kApprovalHoldFoot : easy_keys::kApprovalOnceFoot, x + 6, y + (r.nod ? 80 : 85));
    if (r.nod) {
        g.drawRect(x + 6, y + 90, w - 12, 7, ACCENT);
        if (gHoldKey) g.fillRect(x + 7, y + 91, (w - 14) * nod::holdPercent(millis() - gHoldStart) / 100, 5, OK);
    }
}

}  // namespace approvals
}  // namespace maz
