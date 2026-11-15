// Notes, Tasks and Snippets — the three text stores.
#include <time.h>

#include <algorithm>
#include <vector>

#include "../audio/sfx.h"
#include "../core/notify.h"
#include "../core/shell.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

// Wraps a note body into the body area. Not a word processor: it wraps, it
// scrolls, it does not reflow paragraphs or edit in place.
void drawWrapped(M5Canvas& g, const std::string& text, int fromLine) {
    g.setFont(&fonts::Font2);
    g.setTextColor(TEXT, BG);
    g.setTextDatum(top_left);

    const std::vector<std::string> lines = ui::wrap(text, 28);
    for (int d = 0; d < 5; ++d) {
        const size_t idx = static_cast<size_t>(fromLine + d);
        if (idx >= lines.size()) break;
        g.drawString(lines[idx].c_str(), PAD, BODY_Y + 24 + d * 15);
    }
}

// ------------------------------------------------------------------ Notes
class NotesApp : public App {
public:
    const char* id() const override { return "notes"; }
    const char* title() const override { return "Notes"; }

    const char* hints() const override {
        switch (_mode) {
            case Mode::Edit: return "ENTER save   ESC cancel";
            case Mode::Read: return "E edit   D delete   ESC list";
            default:         return "N new   ENTER read   D delete";
        }
    }

    void onEnter() override {
        reload();
        _mode = Mode::List;
        invalidate();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;

        if (_mode == Mode::Edit) {
            if (e.code == KEY_ENTER) {
                save();
                return true;
            }
            if (e.code == KEY_ESC) {
                _mode = Mode::List;
                invalidate();
                return true;
            }
            if (_field.onKey(e)) {
                invalidate();
                return true;
            }
            return false;
        }

        if (_mode == Mode::Read) {
            if (e.code == KEY_E) {
                _field.text  = _body;
                _field.limit = 400;
                _mode        = Mode::Edit;
                invalidate();
                return true;
            }
            if (e.code == KEY_DOWN) {
                _scroll++;
                invalidate();
                return true;
            }
            if (e.code == KEY_UP) {
                if (_scroll > 0) _scroll--;
                invalidate();
                return true;
            }
            if (e.code == KEY_D) return tryDelete();
            return false;
        }

        // List mode
        if (e.code == KEY_N) {
            _editPath.clear();
            _field.text.clear();
            _field.limit = 400;
            _mode        = Mode::Edit;
            invalidate();
            return true;
        }
        if (_cursor.onKey(e, static_cast<int>(_files.size()))) {
            invalidate();
            return true;
        }
        if (_files.empty()) return false;
        if (e.code == KEY_ENTER) {
            _editPath = _files[_cursor.sel].path;
            _body     = store::readText(_editPath, 4096);
            _openedAt = _files[_cursor.sel].created;
            _scroll   = 0;
            _mode     = Mode::Read;
            sfx::confirm();
            invalidate();
            return true;
        }
        if (e.code == KEY_D) return tryDelete();
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);

        if (_mode == Mode::Edit) {
            ui::header(g, _editPath.empty() ? "New note" : "Edit note");
            _field.draw(g, PAD, BODY_Y + 26, SCREEN_W - PAD * 2,
                        "first line becomes the title");
            g.setFont(&fonts::Font0);
            g.setTextColor(DIM, BG);
            char count[24];
            snprintf(count, sizeof(count), "%u/400", (unsigned)_field.text.size());
            g.drawString(count, PAD, BODY_Y + 50);
            return;
        }

        if (_mode == Mode::Read) {
            ui::header(g, "Note", ui::stamp(_openedAt).c_str());
            drawWrapped(g, _body, _scroll);
            return;
        }

        char right[24];
        snprintf(right, sizeof(right), "%u", (unsigned)_files.size());
        ui::header(g, "Notes", right);

        if (!store::ready()) {
            ui::emptyState(g, "No storage", "insert an SD card");
            return;
        }
        if (_files.empty()) {
            ui::emptyState(g, "No notes yet", "press N to write one");
            return;
        }
        const int rows =
            std::min<int>(ROWS_VISIBLE - 1, static_cast<int>(_files.size()));
        for (int i = 0; i < rows; ++i) {
            const int idx = _cursor.first + i;
            if (idx >= static_cast<int>(_files.size())) break;
            ui::listRow(g, i + 1, idx == _cursor.sel,
                        ui::ellipsis(_files[idx].title, 22).c_str(),
                        ui::humanSize(_files[idx].size).c_str());
        }
        ui::scrollBar(g, static_cast<int>(_files.size()), _cursor.first,
                      ROWS_VISIBLE - 1);
    }

private:
    enum class Mode { List, Read, Edit };

    void reload() {
        _files = store::list("notes", ".txt");
        // Show the first line as the title — that is what a note actually is.
        for (auto& f : _files) {
            const std::string head = store::readText(f.path, 48);
            const size_t      eol  = head.find('\n');
            f.title = eol == std::string::npos ? head : head.substr(0, eol);
            if (f.title.empty()) f.title = f.name;
        }
        _cursor.clamp(static_cast<int>(_files.size()));
    }

    void save() {
        if (_field.text.empty()) {
            _mode = Mode::List;
            invalidate();
            return;
        }
        _field.remember();  // Ctrl+H in any field recalls this text
        const std::string path =
            _editPath.empty() ? store::newPath("notes", "txt") : _editPath;
        if (store::writeText(path, _field.text)) {
            notify::post(Note::Success, "Note saved");
            reload();
        } else {
            notify::post(Note::Error, "Save failed", store::backendName());
        }
        _mode = Mode::List;
        invalidate();
    }

    bool tryDelete() {
        const std::string path =
            _mode == Mode::Read
                ? _editPath
                : (_files.empty() ? "" : _files[_cursor.sel].path);
        if (path.empty()) return false;
        if (_confirm && millis() - _confirmAt < 3000) {
            store::remove(path);
            notify::post(Note::Info, "Note deleted");
            _confirm = false;
            _mode    = Mode::List;
            reload();
        } else {
            _confirm   = true;
            _confirmAt = millis();
            notify::post(Note::Warn, "Press D again", "to delete this note");
        }
        invalidate();
        return true;
    }

    std::vector<store::Entry> _files;
    ListCursor                _cursor;
    TextField                 _field;
    Mode                      _mode = Mode::List;
    std::string               _editPath;
    std::string               _body;
    int                       _scroll    = 0;
    uint32_t                  _openedAt  = 0;
    bool                      _confirm   = false;
    uint32_t                  _confirmAt = 0;
};

// ------------------------------------------------------------------ Tasks
class TasksApp : public App {
public:
    const char* id() const override { return "tasks"; }
    const char* title() const override { return "Tasks"; }

    const char* hints() const override {
        if (_adding) return "ENTER add   ESC cancel";
        return "A add  ENTER done  L later  TAB view";
    }

    void onEnter() override {
        _tasks = store::loadTasks();
        rebuild();
        invalidate();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;

        if (_adding) {
            if (e.code == KEY_ENTER) {
                if (!_field.text.empty()) {
                    _field.remember();
                    store::Task t;
                    t.text    = _field.text;
                    t.bucket  = _bucket;
                    t.created = static_cast<uint32_t>(time(nullptr));
                    _tasks.push_back(t);
                    persist();
                    _field.text.clear();
                }
                _adding = false;
                invalidate();
                return true;
            }
            if (e.code == KEY_ESC) {
                _adding = false;
                invalidate();
                return true;
            }
            if (_field.onKey(e)) {
                invalidate();
                return true;
            }
            return false;
        }

        if (e.code == KEY_A) {
            _adding = true;
            _field.text.clear();
            _field.limit = 90;
            invalidate();
            return true;
        }
        if (e.code == KEY_TAB) {
            _bucket = _bucket ? 0 : 1;
            rebuild();
            invalidate();
            return true;
        }
        if (_cursor.onKey(e, static_cast<int>(_view.size()))) {
            invalidate();
            return true;
        }
        if (_view.empty()) return false;

        const int idx = _view[_cursor.sel];
        if (e.code == KEY_ENTER) {
            _tasks[idx].done = !_tasks[idx].done;  // done <-> reopened
            sfx::confirm();
            persist();
            return true;
        }
        if (e.code == KEY_L) {
            _tasks[idx].bucket = _tasks[idx].bucket ? 0 : 1;
            persist();
            return true;
        }
        if (e.code == KEY_D) {
            if (_confirm && millis() - _confirmAt < 3000) {
                _tasks.erase(_tasks.begin() + idx);
                _confirm = false;
                persist();
            } else {
                _confirm   = true;
                _confirmAt = millis();
                notify::post(Note::Warn, "Press D again", "to delete");
            }
            invalidate();
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, _bucket ? "Tasks / Later" : "Tasks / Today",
                   store::ready() ? nullptr : "no storage");

        if (_adding) {
            _field.draw(g, PAD, BODY_Y + 26, SCREEN_W - PAD * 2,
                        "what needs doing?");
            return;
        }
        if (_view.empty()) {
            ui::emptyState(g, _bucket ? "Nothing parked" : "Nothing for today",
                           "press A to add");
            return;
        }

        const int rows =
            std::min<int>(ROWS_VISIBLE - 1, static_cast<int>(_view.size()));
        for (int i = 0; i < rows; ++i) {
            const int vi = _cursor.first + i;
            if (vi >= static_cast<int>(_view.size())) break;
            const store::Task& t     = _tasks[_view[vi]];
            const std::string  label = (t.done ? "[x] " : "[ ] ") + t.text;
            ui::listRow(g, i + 1, vi == _cursor.sel,
                        ui::ellipsis(label, 26).c_str());
        }
        ui::scrollBar(g, static_cast<int>(_view.size()), _cursor.first,
                      ROWS_VISIBLE - 1);
    }

private:
    void rebuild() {
        _view.clear();
        // Open tasks first: the list should answer "what now", not "what have
        // I done".
        for (size_t i = 0; i < _tasks.size(); ++i)
            if (_tasks[i].bucket == _bucket && !_tasks[i].done)
                _view.push_back(static_cast<int>(i));
        for (size_t i = 0; i < _tasks.size(); ++i)
            if (_tasks[i].bucket == _bucket && _tasks[i].done)
                _view.push_back(static_cast<int>(i));
        _cursor.clamp(static_cast<int>(_view.size()));
    }

    void persist() {
        if (!store::saveTasks(_tasks))
            notify::post(Note::Error, "Save failed", store::backendName());
        rebuild();
        invalidate();
    }

    std::vector<store::Task> _tasks;
    std::vector<int>         _view;
    ListCursor               _cursor;
    TextField                _field;
    uint8_t                  _bucket    = 0;
    bool                     _adding    = false;
    bool                     _confirm   = false;
    uint32_t                 _confirmAt = 0;
};

// --------------------------------------------------------------- Snippets
class SnippetsApp : public App {
public:
    const char* id() const override { return "snippets"; }
    const char* title() const override { return "Snippets"; }
    const char* hints() const override {
        if (_adding) return "ENTER next/save   ESC cancel";
        return _items.empty() ? "A add" : "A add  ENTER show  D delete";
    }

    void onEnter() override {
        _items = store::loadSnippets();
        _cursor.clamp(static_cast<int>(_items.size()));
        invalidate();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;

        if (_adding) {
            if (e.code == KEY_ENTER) {
                if (!_field.text.empty()) _field.remember();
                if (_stage == 0) {
                    _name  = _field.text;
                    _stage = 1;
                    _field.text.clear();
                } else {
                    if (!_name.empty()) {
                        _items.emplace_back(_name, _field.text);
                        store::saveSnippets(_items);
                        notify::post(Note::Success, "Snippet saved", _name);
                    }
                    _adding = false;
                }
                invalidate();
                return true;
            }
            if (e.code == KEY_ESC) {
                _adding = false;
                invalidate();
                return true;
            }
            if (_field.onKey(e)) {
                invalidate();
                return true;
            }
            return false;
        }

        if (e.code == KEY_A) {
            _adding = true;
            _stage  = 0;
            _field.text.clear();
            _name.clear();
            invalidate();
            return true;
        }
        if (_cursor.onKey(e, static_cast<int>(_items.size()))) {
            invalidate();
            return true;
        }
        if (_items.empty()) return false;
        if (e.code == KEY_ENTER) {
            _showing = !_showing;
            invalidate();
            return true;
        }
        if (e.code == KEY_D) {
            _items.erase(_items.begin() + _cursor.sel);
            store::saveSnippets(_items);
            _cursor.clamp(static_cast<int>(_items.size()));
            invalidate();
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Snippets");

        if (_adding) {
            g.setFont(&fonts::Font0);
            g.setTextColor(DIM, BG);
            g.setTextDatum(top_left);
            g.drawString(_stage == 0 ? "name" : "value", PAD, BODY_Y + 24);
            _field.draw(g, PAD, BODY_Y + 36, SCREEN_W - PAD * 2,
                        _stage == 0 ? "email" : "you@example.com");
            return;
        }
        if (_items.empty()) {
            ui::emptyState(g, "No snippets", "press A to add one");
            return;
        }
        if (_showing) {
            g.setFont(&fonts::Font0);
            g.setTextColor(ACCENT, BG);
            g.setTextDatum(top_left);
            g.drawString(_items[_cursor.sel].first.c_str(), PAD, BODY_Y + 24);
            drawWrapped(g, _items[_cursor.sel].second, 0);
            return;
        }
        const int rows =
            std::min<int>(ROWS_VISIBLE - 1, static_cast<int>(_items.size()));
        for (int i = 0; i < rows; ++i) {
            const int idx = _cursor.first + i;
            if (idx >= static_cast<int>(_items.size())) break;
            ui::listRow(g, i + 1, idx == _cursor.sel,
                        ui::ellipsis(_items[idx].first, 18).c_str(),
                        ui::ellipsis(_items[idx].second, 10).c_str());
        }
    }

private:
    std::vector<std::pair<std::string, std::string>> _items;
    ListCursor  _cursor;
    TextField   _field;
    std::string _name;
    bool        _adding  = false;
    bool        _showing = false;
    int         _stage   = 0;
};

}  // namespace

App* makeNotes() { return new NotesApp(); }
App* makeTasks() { return new TasksApp(); }
App* makeSnippets() { return new SnippetsApp(); }

}  // namespace apps
}  // namespace maz
