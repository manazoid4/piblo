#include <algorithm>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../core/shell.h"
#include "../net/mazhost.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

constexpr const char* ACTION_LABELS[] = {"GIT STATUS", "TESTS", "BUILD", "GIT FETCH", "PULL FF-ONLY"};
constexpr const char* ACTION_IDS[] = {"git_status", "tests", "build", "git_fetch", "git_pull_ff"};
constexpr int ACTION_COUNT = 5;

class CoreConsoleApp final : public App {
public:
    const char* id() const override { return "core"; }
    const char* title() const override { return "PC"; }
    const char* hints() const override {
        if (_view == View::Result) return "R refresh  ESC actions";
        if (_view == View::Actions) return "ENTER run  ESC projects";
        return "R refresh  ENTER project";
    }

    void onEnter() override { _view = View::Projects; _loadAt = millis() + 80; _loading = true; invalidate(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_view == View::Result) {
            if (e.code == KEY_R) { pollJob(); return true; }
            if (e.code == KEY_ESC) { _view = View::Actions; invalidate(); return true; }
            return false;
        }
        if (_view == View::Actions) {
            if (e.code == KEY_ESC) { _view = View::Projects; invalidate(); return true; }
            if (_cursor.onKey(e, ACTION_COUNT)) { sfx::select(); invalidate(); return true; }
            if (e.code == KEY_ENTER) { startAction(_cursor.sel); return true; }
            return false;
        }
        if (e.code == KEY_R) { refresh(); return true; }
        if (_cursor.onKey(e, static_cast<int>(_projects.size()))) { sfx::select(); invalidate(); return true; }
        if (e.code == KEY_ENTER && !_projects.empty()) {
            _project = _projects[_cursor.sel].name;
            _cursor.sel = _cursor.first = 0;
            _view = View::Actions;
            sfx::confirm();
            invalidate();
            return true;
        }
        if (e.code == KEY_ENTER && !_status.ok) {
            shell::pushById("wifi");
            return true;
        }
        return false;
    }

    void update() override {
        if (_loading && millis() >= _loadAt) { _loading = false; refresh(); }
        if (_view == View::Result && !_job.id.empty() && _job.state != "done" &&
            millis() - _lastPoll > 1500) pollJob(false);
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (_view == View::Result) { renderResult(g); return; }
        if (_view == View::Actions) { renderActions(g); return; }
        renderProjects(g);
    }

private:
    enum class View : uint8_t { Projects, Actions, Result };

    void refresh() {
        _status = host::coreStatus();
        _projects = host::coreProjects(_error);
        _cursor.sel = _cursor.first = 0;
        _view = View::Projects;
        sfx::confirm();
        invalidate();
    }

    void renderProjects(M5Canvas& g) {
        std::string right = _status.ok ? (_status.ollama ? "AI + PC" : "PC / AI OFF") : "OFFLINE";
        ui::header(g, "PC", _loading ? "" : right.c_str());
        if (_loading) { line(g, 0, "Loading...", DIM); return; }
        if (!_status.ok) {
            line(g, 0, _status.error.c_str(), WARN);
            line(g, 2, "Configure CONTROL > PC", DIM);
            line(g, 3, "then press R to refresh", DIM);
            return;
        }
        char meta[96];
        snprintf(meta, sizeof(meta), "%s / %d projects", _status.hostname.c_str(), _status.projects);
        line(g, 0, meta, ACCENT);
        if (_projects.empty()) {
            line(g, 2, _error.empty() ? "No projects found" : _error.c_str(), WARN);
            return;
        }
        constexpr int visible = 4;
        if (_cursor.sel >= _cursor.first + visible) _cursor.first = _cursor.sel - visible + 1;
        if (_cursor.sel < _cursor.first) _cursor.first = _cursor.sel;
        for (int row = 0; row < visible; ++row) {
            const int idx = _cursor.first + row;
            if (idx >= static_cast<int>(_projects.size())) break;
            char sub[32];
            snprintf(sub, sizeof(sub), "%s%s%d", _projects[idx].branch.c_str(),
                     _projects[idx].dirty ? " *" : " ", _projects[idx].dirty);
            ui::listRow(g, row + 2, idx == _cursor.sel, ui::ellipsis(_projects[idx].name, 18).c_str(), sub);
        }
        ui::scrollBar(g, static_cast<int>(_projects.size()), _cursor.first, visible);
    }

    void renderActions(M5Canvas& g) {
        ui::header(g, _project.c_str(), "PC ACTIONS");
        for (int row = 0; row < ACTION_COUNT; ++row)
            ui::listRow(g, row + 1, row == _cursor.sel, ACTION_LABELS[row], row >= 1 && row <= 2 ? "job" : "safe");
    }

    void startAction(int idx) {
        if (idx < 0 || idx >= ACTION_COUNT) return;
        _job = host::coreStartJob(ACTION_IDS[idx], _project);
        _result.clear();
        if (_job.id.empty()) {
            _result = _job.error.empty() ? "Could not start hub job" : _job.error;
        }
        _view = View::Result;
        _lastPoll = millis();
        sfx::confirm();
        invalidate();
    }

    void pollJob(bool sound = true) {
        _lastPoll = millis();
        if (_job.id.empty()) { invalidate(); return; }
        _job = host::coreJob(_job.id);
        if (_job.state == "done") {
            _result = !_job.output.empty() ? _job.output : (_job.ok ? "Completed" : _job.error);
            if (sound) sfx::confirm();
        }
        invalidate();
    }

    void renderResult(M5Canvas& g) {
        std::string right = _job.state.empty() ? "ERROR" : _job.state;
        ui::header(g, _project.c_str(), right.c_str());
        std::string heading = _job.action.empty() ? "PC JOB" : _job.action;
        line(g, 0, heading.c_str(), ACCENT);
        if (_job.state != "done" && !_job.id.empty()) {
            line(g, 2, "Running on hub...", TEXT);
            line(g, 3, "You can leave this screen.", DIM);
            line(g, 4, "R checks now; auto-refreshes.", DIM);
            return;
        }
        const std::string text = !_result.empty() ? _result : (!_job.error.empty() ? _job.error : "No output");
        const std::vector<std::string> lines = ui::wrap(text, 37);
        for (size_t k = 0; k < lines.size() && k < 5; ++k)
            line(g, 1 + static_cast<int>(k), lines[k].c_str(), _job.ok ? TEXT : ERR);
    }

    static void line(M5Canvas& g, int row, const char* text, uint16_t color) {
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);
        g.setTextColor(color, BG);
        g.drawString(text, PAD, BELOW_RULE + row * 15);
    }

    View _view = View::Projects;
    ListCursor _cursor;
    host::CoreStatus _status;
    std::vector<host::CoreProject> _projects;
    host::CoreJob _job;
    std::string _project;
    std::string _error;
    std::string _result;
    uint32_t _lastPoll = 0;
    uint32_t _loadAt = 0;
    bool _loading = false;
};

}  // namespace

App* makeCoreConsole() { return new CoreConsoleApp(); }

}  // namespace apps
}  // namespace maz
