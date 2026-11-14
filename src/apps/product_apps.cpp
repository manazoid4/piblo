#include <algorithm>
#include <cstdlib>
#include <ctime>

#include "../core/notify.h"
#include "../core/shell.h"
#include "../net/mazhost.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {
using namespace theme;

namespace {
constexpr time_t VALID_WALL_CLOCK = 1704067200;  // 2024-01-01 UTC
}

void scheduleReminder(store::Record& reminder, uint32_t delaySeconds) {
    const time_t now = time(nullptr);
    if (now >= VALID_WALL_CLOCK) {
        reminder.due = static_cast<uint32_t>(now) + delaySeconds;
        reminder.dueIsUptime = false;
    } else {
        reminder.due = millis() / 1000 + delaySeconds;
        reminder.dueIsUptime = true;
    }
}

namespace {

void drawRecords(M5Canvas& g, const std::vector<store::Record>& rows, const ListCursor& cursor) {
    if (rows.empty()) { ui::emptyState(g, "Nothing here", "press A to add one"); return; }
    const int visible = std::min<int>(ROWS_VISIBLE - 1, rows.size());
    for (int row = 0; row < visible; ++row) {
        const int idx = cursor.first + row;
        if (idx >= static_cast<int>(rows.size())) break;
        ui::listRow(g, row + 1, idx == cursor.sel, ui::ellipsis(rows[idx].title, 20).c_str(),
                    ui::ellipsis(rows[idx].status, 10).c_str());
    }
}

class InboxApp : public App {
public:
    const char* id() const override { return "inbox"; }
    const char* title() const override { return "Inbox"; }
    const char* hints() const override { return _detail ? "D done   ESC list" : "ENTER open   D done"; }
    void onEnter() override { reload(); }
    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_detail && e.code == KEY_ESC) { _detail = false; invalidate(); return true; }
        if (_cursor.onKey(e, _rows.size())) { invalidate(); return true; }
        if (_rows.empty()) return false;
        if (e.code == KEY_ENTER) { _detail = true; invalidate(); return true; }
        if (e.code == KEY_D) { _rows[_cursor.sel].status = "done"; store::updateRecord(_rows[_cursor.sel]); reload(); return true; }
        return false;
    }
    void render(M5Canvas& g) override {
        g.fillScreen(BG); ui::header(g, "Inbox", std::to_string(_rows.size()).c_str());
        if (_detail && !_rows.empty()) {
            g.setFont(&fonts::Font2); g.setTextColor(ACCENT, BG); g.drawString(_rows[_cursor.sel].title.c_str(), PAD, BODY_Y + 24);
            g.setFont(&fonts::Font0); g.setTextColor(TEXT, BG);
            g.drawString(ui::ellipsis(_rows[_cursor.sel].body, 36).c_str(), PAD, BODY_Y + 46);
            g.setTextColor(DIM, BG); g.drawString(_rows[_cursor.sel].source.c_str(), PAD, BODY_Y + 65);
        } else drawRecords(g, _rows, _cursor);
    }
private:
    void reload() { _rows = store::loadRecords("inbox"); _cursor.clamp(_rows.size()); _detail = false; invalidate(); }
    std::vector<store::Record> _rows; ListCursor _cursor; bool _detail = false;
};

class DecisionApp : public App {
public:
    const char* id() const override { return "decision"; }
    const char* title() const override { return "Decision"; }
    const char* hints() const override { return _why ? "ENTER save decision" : "ENTER then add why"; }
    void onEnter() override { _what.text.clear(); _reason.text.clear(); _why = false; invalidate(); }
    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (e.code == KEY_ENTER) {
            if (!_why && !_what.text.empty()) { _what.remember(); _why = true; invalidate(); return true; }
            if (_why && !_reason.text.empty()) {
                _reason.remember();
                store::Record r; r.kind="decision"; r.status="open"; r.title=_what.text; r.body=_reason.text; r.source="device";
                if (store::addRecord(r)) { notify::post(Note::Success, "Decision saved", "what + why"); onEnter(); }
                return true;
            }
        }
        if ((_why ? _reason : _what).onKey(e)) { invalidate(); return true; }
        return false;
    }
    void render(M5Canvas& g) override {
        g.fillScreen(BG); ui::header(g, "Decision", _why ? "WHY" : "WHAT");
        g.setFont(&fonts::Font0); g.setTextColor(DIM, BG);
        g.drawString(_why ? "Why is this the right call?" : "What did you decide?", PAD, BODY_Y + 26);
        (_why ? _reason : _what).draw(g, PAD, BODY_Y + 42, SCREEN_W - PAD * 2, _why ? "because..." : "decision...");
    }
private: TextField _what, _reason; bool _why = false;
};

class SprintApp : public App {
public:
    const char* id() const override { return "sprint"; }
    const char* title() const override { return "Sprint"; }
    const char* hints() const override {
        if (shell::focus::running()) return "P pause   X stop";
        return _debriefReady ? "B debrief   ENTER new sprint" : "ENTER start 25 minutes";
    }
    void onEnter() override {
        _goal.text.clear();
        _current = {};
        _debriefReady = false;
        for (const auto& sprint : store::loadRecords("sprint")) {
            if (sprint.status == "running") { _current = sprint; break; }
        }
        _wasRunning = shell::focus::running();
        if (!_current.id.empty() && !_wasRunning) finish("complete");
        invalidate();
    }
    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (shell::focus::running()) {
            if (e.code == KEY_P) { shell::focus::paused() ? shell::focus::resume() : shell::focus::pause(); return true; }
            if (e.code == KEY_X) { shell::focus::cancel(); finish("stopped"); return true; }
            return false;
        }
        if (e.code == KEY_B && _debriefReady) { shell::pushById("braindump"); return true; }
        if (e.code == KEY_ENTER && !_goal.text.empty()) {
            _goal.remember();
            _current.kind="sprint"; _current.status="running"; _current.title=_goal.text; _current.source="device";
            store::addRecord(_current); shell::focus::start(25 * 60, _goal.text); _wasRunning=true; invalidate(); return true;
        }
        if (_goal.onKey(e)) { invalidate(); return true; }
        return false;
    }
    void update() override {
        if (_wasRunning && !shell::focus::running()) { finish("complete"); notify::post(Note::Success, "Sprint complete", "B to debrief"); }
        _wasRunning = shell::focus::running(); if (_wasRunning) invalidate();
    }
    void render(M5Canvas& g) override {
        g.fillScreen(BG); ui::header(g, "Sprint", shell::focus::running() ? "WORKING" : "OUTCOME");
        if (shell::focus::running()) {
            g.setTextDatum(top_center); g.setFont(&fonts::Font4); g.setTextColor(ACCENT, BG);
            g.drawString(ui::hhmmss(shell::focus::remaining()).c_str(), SCREEN_W/2, BODY_Y+28);
            g.setFont(&fonts::Font0); g.setTextColor(TEXT, BG); g.drawString(ui::ellipsis(shell::focus::label(), 34).c_str(), SCREEN_W/2, BODY_Y+60); g.setTextDatum(top_left);
        } else _goal.draw(g, PAD, BODY_Y+38, SCREEN_W-PAD*2, "intended outcome...");
    }
private:
    void finish(const char* status) {
        _current.status=status;
        if (!_current.id.empty()) store::updateRecord(_current);
        _current = {};
        _debriefReady = true;
        invalidate();
    }
    TextField _goal; store::Record _current; bool _wasRunning=false; bool _debriefReady=false;
};

class RemindersApp : public App {
public:
    const char* id() const override { return "reminders"; }
    const char* title() const override { return "Reminders"; }
    const char* hints() const override { return _adding ? "ENTER save: minutes message" : "A add   D done   S snooze 10m"; }
    void onEnter() override { reload(); }
    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_adding) {
            if (e.code == KEY_ENTER) { save(); return true; }
            if (_field.onKey(e)) { invalidate(); return true; }
            return false;
        }
        if (e.code == KEY_A) { _adding=true; _field.text.clear(); invalidate(); return true; }
        if (_cursor.onKey(e, _rows.size())) { invalidate(); return true; }
        if (_rows.empty()) return false;
        if (e.code == KEY_D) { _rows[_cursor.sel].status="done"; store::updateRecord(_rows[_cursor.sel]); reload(); return true; }
        if (e.code == KEY_S) {
            _rows[_cursor.sel].status="snoozed";
            scheduleReminder(_rows[_cursor.sel], 600);
            store::updateRecord(_rows[_cursor.sel]); reload(); return true;
        }
        return false;
    }
    void render(M5Canvas& g) override {
        g.fillScreen(BG); ui::header(g, "Reminders", "LOCAL");
        if (_adding) _field.draw(g, PAD, BODY_Y+38, SCREEN_W-PAD*2, "30 check the chicken");
        else drawRecords(g, _rows, _cursor);
    }
private:
    void reload() { _rows=store::loadRecords("reminder"); _cursor.clamp(_rows.size()); _adding=false; invalidate(); }
    void save() {
        const size_t split=_field.text.find(' '); if (split==std::string::npos) return;
        const int minutes=atoi(_field.text.substr(0,split).c_str()); if (minutes<1) return;
        _field.remember();
        store::Record r; r.kind="reminder"; r.status="open"; r.title=_field.text.substr(split+1); r.source="human";
        scheduleReminder(r, minutes * 60);
        if (store::addRecord(r)) { notify::post(Note::Success,"Reminder set",std::to_string(minutes)+" minutes"); reload(); }
    }
    std::vector<store::Record> _rows; ListCursor _cursor; TextField _field; bool _adding=false;
};

}  // namespace

void updateProductServices() {
    static uint32_t nextReminder = 0;
    static uint32_t nextOutbox = 0;
    if (!store::ready()) return;

    if (millis() >= nextOutbox && Sys.hostOnline) {
        nextOutbox = millis() + 15000;
        auto queued = store::loadRecords("outbox", 8);
        for (auto& item : queued) {
            if (item.status != "queued" || item.ref.empty()) continue;
            host::Reply result;
            if (item.source == "talk") {
                const std::string session = host::startSession();
                if (!session.empty()) result = host::talkAudio(session, item.ref);
            } else if (item.source == "braindump") {
                result = host::brainDump(item.ref, {});
            } else {
                continue;
            }
            if (!result.ok) break;

            store::Record answer;
            answer.kind="inbox"; answer.status="open";
            answer.title=item.source == "talk" ? "Piblo answer" : "Brain Dump processed";
            answer.body=result.text; answer.source=result.provider; answer.ref=item.ref;
            if (!store::addRecord(answer)) break;

            if (!result.reminderTitle.empty() && result.reminderDelay) {
                store::Record reminder;
                reminder.kind="reminder"; reminder.status="open";
                reminder.title=result.reminderTitle; reminder.source="voice";
                scheduleReminder(reminder, result.reminderDelay);
                store::addRecord(reminder);
            }
            item.status="sent"; item.body=result.text;
            if (store::updateRecord(item)) {
                if (item.source == "talk") store::remove(item.ref);
                notify::post(Note::Success, "Queued result ready", answer.title);
            }
            break;  // one blocking LAN job per service pass
        }
    }

    if (millis() < nextReminder) return;
    nextReminder = millis() + 1000;
    const time_t now = time(nullptr);
    const uint32_t uptime = millis() / 1000;
    const bool wallClockReady = now >= VALID_WALL_CLOCK;
    auto reminders = store::loadRecords("reminder", 64);
    for (auto& reminder : reminders) {
        if (reminder.status != "open" && reminder.status != "snoozed") continue;
        if (!reminder.due) continue;

        // Migrate reminders created by older builds before NTP was available.
        if (!reminder.dueIsUptime && reminder.due < VALID_WALL_CLOCK) {
            const uint32_t delay = reminder.due > reminder.created
                ? reminder.due - reminder.created : 60;
            reminder.due = uptime + delay;
            reminder.dueIsUptime = true;
            store::updateRecord(reminder);
        }
        if (reminder.dueIsUptime && wallClockReady) {
            const uint32_t remaining = reminder.due > uptime ? reminder.due - uptime : 0;
            reminder.due = static_cast<uint32_t>(now) + remaining;
            reminder.dueIsUptime = false;
            store::updateRecord(reminder);
        }

        const bool isDue = reminder.dueIsUptime
            ? uptime >= reminder.due
            : wallClockReady && static_cast<uint32_t>(now) >= reminder.due;
        if (isDue) {
            reminder.status = "fired"; store::updateRecord(reminder);
            notify::post(Note::Warn, "REMINDER", reminder.title);
            break;
        }
    }
}

App* makeInbox() { return new InboxApp(); }
App* makeDecision() { return new DecisionApp(); }
App* makeSprint() { return new SprintApp(); }
App* makeReminders() { return new RemindersApp(); }

}  // namespace apps
}  // namespace maz
