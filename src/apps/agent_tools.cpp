#include <algorithm>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../core/notify.h"
#include "../core/sys.h"
#include "../net/host_worker.h"
#include "../net/mazhost.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

enum class ToolKind : uint8_t { Plan, Prompt, Crew, Retro };
enum class View : uint8_t { Template, Project, Task, Working, Result };

struct DeckTemplate {
    const char* id;
    const char* label;
    const char* sub;
};

constexpr DeckTemplate DECK[] = {
    {"fix-bug", "FIX A BUG", "cause + test"},
    {"add-feature", "BUILD FEATURE", "vertical slice"},
    {"add-ui", "ADD UI", "reuse design"},
    {"repo-audit", "AUDIT REPO", "rank fixes"},
    {"research-build", "RESEARCH + BUILD", "compare first"},
    {"security-audit", "SECURITY AUDIT", "trust boundary"},
    {"maz-feature", "PIBLO FEATURE", "Pocket-native"},
    {"agent-workflow", "AGENT WORKFLOW", "reduce friction"},
};
constexpr int DECK_COUNT = sizeof(DECK) / sizeof(DECK[0]);

const char* titleFor(ToolKind kind) {
    switch (kind) {
        case ToolKind::Prompt: return "PROMPT DECK";
        case ToolKind::Crew: return "CREW";
        case ToolKind::Retro: return "RETRO";
        default: return "PLAN";
    }
}

const char* descriptionFor(ToolKind kind) {
    switch (kind) {
        case ToolKind::Prompt: return "make a strong build prompt";
        case ToolKind::Crew: return "split work across agents";
        case ToolKind::Retro: return "learn from recent work";
        default: return "think before changing code";
    }
}

class AgentToolApp final : public App {
public:
    explicit AgentToolApp(ToolKind kind) : _kind(kind) {}

    const char* id() const override {
        switch (_kind) {
            case ToolKind::Prompt: return "prompts";
            case ToolKind::Crew: return "crew";
            case ToolKind::Retro: return "retro";
            default: return "plan";
        }
    }
    const char* title() const override { return titleFor(_kind); }
    const char* hints() const override {
        if (_view == View::Working) return "PC working  stay here";
        if (_view == View::Result) return "ENTER save to Inbox   N new";
        if (_view == View::Template) return "UP/DOWN template   ENTER choose";
        if (_view == View::Project) return "UP/DOWN project   ENTER choose   S skip";
        return "type request   ENTER send";
    }

    void onEnter() override {
        consumeResult();
        if (_view == View::Result) return;
        _task.text.clear();
        _result.clear();
        _provider.clear();
        _project.clear();
        _projects.clear();
        _projectCursor.sel = _projectCursor.first = 0;
        _templateCursor.sel = _templateCursor.first = 0;
        _view = _kind == ToolKind::Prompt ? View::Template : View::Project;
        _loadAt = millis() + 80;  // paint "Loading..." first; the fetch blocks
        _loading = true;
        invalidate();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_view == View::Working) return false;
        if (_view == View::Result) {
            if (e.code == KEY_N) { onEnter(); return true; }
            if (e.code == KEY_ENTER) { saveResult(); return true; }
            if (e.code == KEY_DOWN) { ++_scroll; invalidate(); return true; }
            if (e.code == KEY_UP && _scroll > 0) { --_scroll; invalidate(); return true; }
            return false;
        }
        if (_view == View::Template) {
            if (_templateCursor.onKey(e, DECK_COUNT)) { sfx::select(); invalidate(); return true; }
            if (e.code == KEY_ENTER) {
                _templateId = DECK[_templateCursor.sel].id;
                _view = View::Project;
                sfx::confirm();
                invalidate();
                return true;
            }
            return false;
        }
        if (_view == View::Project) {
            if (e.code == KEY_S) {
                _project.clear();
                _view = View::Task;
                sfx::confirm();
                invalidate();
                return true;
            }
            if (_projectCursor.onKey(e, static_cast<int>(_projects.size()))) {
                sfx::select(); invalidate(); return true;
            }
            if (e.code == KEY_ENTER) {
                if (!_projects.empty()) _project = _projects[_projectCursor.sel].name;
                _view = View::Task;
                sfx::confirm();
                invalidate();
                return true;
            }
            return false;
        }
        if (_view == View::Task) {
            if (e.code == KEY_ENTER) { submit(); return true; }
            if (_task.onKey(e)) { invalidate(); return true; }
        }
        return false;
    }

    void update() override {
        if (_loading && millis() >= _loadAt) { _loading = false; refreshProjects(); invalidate(); }
        if (_view == View::Working) consumeResult();
        // Retry a failed project load, but only once a background poll saw Core
        // online: coreProjects() blocks up to its connect timeout while it is down.
        if ((_view == View::Template || _view == View::Project) && !_projectError.empty() &&
            Sys.hostOnline && !host_worker::busy() && millis() - _lastTry >= 5000) {
            refreshProjects();
            invalidate();
        }
        if (_view == View::Working && host_worker::busy() && millis() - _lastPaint > 250) {
            _lastPaint = millis();
            invalidate();
        }
    }

    std::string contextSnapshot() const override {
        return std::string(titleFor(_kind)) + " / " + descriptionFor(_kind) +
               (_project.empty() ? "" : "; project " + _project);
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, titleFor(_kind), _project.empty() ? "AGENT TOOL" : _project.c_str());
        if (_view == View::Template) { renderTemplates(g); return; }
        if (_view == View::Project) { renderProjects(g); return; }
        if (_view == View::Working) { renderWorking(g); return; }
        if (_view == View::Result) { renderResult(g); return; }

        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.drawString(descriptionFor(_kind), PAD, BELOW_RULE);
        _task.draw(g, PAD, BODY_Y + 39, SCREEN_W - PAD * 2,
                   _kind == ToolKind::Retro ? "what happened / optional note..." : "what do you want done...");
        g.setTextColor(DIM, BG);
        g.drawString("ENTER sends to hub", PAD, BODY_Y + 80);
    }

private:
    void refreshProjects() {
        std::string error;
        _lastTry = millis();
        _projects = host::coreProjects(error);
        _projectError = error;
        _projectCursor.clamp(_projects.size());
    }

    void renderTemplates(M5Canvas& g) {
        constexpr int visible = 5;
        if (_templateCursor.sel >= _templateCursor.first + visible)
            _templateCursor.first = _templateCursor.sel - visible + 1;
        if (_templateCursor.sel < _templateCursor.first) _templateCursor.first = _templateCursor.sel;
        for (int row = 0; row < visible; ++row) {
            const int idx = _templateCursor.first + row;
            if (idx >= DECK_COUNT) break;
            ui::listRow(g, row + 1, idx == _templateCursor.sel, DECK[idx].label, DECK[idx].sub);
        }
        ui::scrollBar(g, DECK_COUNT, _templateCursor.first, visible);
    }

    void renderProjects(M5Canvas& g) {
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.drawString("Choose context. S = no project", PAD, BELOW_RULE);
        if (_loading) { g.drawString("Loading...", PAD, BODY_Y + 39); return; }
        if (_projects.empty()) {
            g.setTextColor(WARN, BG);
            g.drawString(_projectError.empty() ? "No projects found; press S" : _projectError.c_str(), PAD, BODY_Y + 39);
            return;
        }
        constexpr int visible = 4;
        if (_projectCursor.sel >= _projectCursor.first + visible)
            _projectCursor.first = _projectCursor.sel - visible + 1;
        if (_projectCursor.sel < _projectCursor.first) _projectCursor.first = _projectCursor.sel;
        for (int row = 0; row < visible; ++row) {
            const int idx = _projectCursor.first + row;
            if (idx >= static_cast<int>(_projects.size())) break;
            ui::listRow(g, row + 2, idx == _projectCursor.sel,
                        _projects[idx].name.c_str(), _projects[idx].branch.c_str());
        }
        ui::scrollBar(g, static_cast<int>(_projects.size()), _projectCursor.first, visible);
    }

    void renderWorking(M5Canvas& g) {
        ui::panel(g, 52, BODY_Y + 25, 136, 50);
        g.setTextDatum(middle_center);
        g.setFont(&fonts::Font2);
        g.setTextColor(ACCENT, PANEL);
        g.drawString(host_worker::stateName(), SCREEN_W / 2, BODY_Y + 45);
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, PANEL);
        g.drawString(titleFor(_kind), SCREEN_W / 2, BODY_Y + 63);
        g.setTextDatum(top_left);
    }

    void renderResult(M5Canvas& g) {
        g.setFont(&fonts::Font0);
        g.setTextColor(_ok ? OK : WARN, BG);
        const std::string status = (_ok ? std::string("READY / ") : std::string("FAILED / ")) +
                                   (_provider.empty() ? "PC" : _provider);
        g.drawString(status.c_str(), PAD, BELOW_RULE);
        constexpr size_t WIDTH = 37;
        for (int row = 0; row < 5; ++row) {
            const size_t start = static_cast<size_t>(_scroll + row) * WIDTH;
            if (start >= _result.size()) break;
            g.setTextColor(TEXT, BG);
            g.drawString(_result.substr(start, WIDTH).c_str(), PAD, BODY_Y + 35 + row * 14);
        }
    }

    void submit() {
        if (host_worker::busy()) {
            notify::post(Note::Info, "PC busy", "finish current hub job first");
            return;
        }
        const std::string task = _task.text;
        if (_kind != ToolKind::Retro && task.empty()) return;
        bool queued = false;
        if (_kind == ToolKind::Crew)
            queued = host_worker::submitWorkflow(host_worker::WorkflowKind::Crew, task, _project);
        else if (_kind == ToolKind::Retro)
            queued = host_worker::submitWorkflow(host_worker::WorkflowKind::Retro, task, _project);
        else if (_kind == ToolKind::Prompt)
            queued = host_worker::submitWorkflow(host_worker::WorkflowKind::Prompt, task, _project, _templateId);
        else
            queued = host_worker::submitWorkflow(host_worker::WorkflowKind::Plan, task, _project);
        if (!queued) {
            notify::post(Note::Error, "Could not start", host_worker::stateName());
            return;
        }
        _view = View::Working;
        _lastPaint = millis();
        sfx::confirm();
        invalidate();
    }

    void consumeResult() {
        if (host_worker::state() != host_worker::State::Done ||
            host_worker::jobKind() != host_worker::JobKind::Workflow) return;
        host_worker::WorkflowResult result;
        if (!host_worker::takeWorkflowResult(result)) return;
        _ok = result.reply.ok;
        _result = result.reply.ok ? result.reply.text : result.reply.error;
        if (_result.empty()) _result = result.reply.ok ? "Ready on hub" : "Workflow failed";
        _provider = result.reply.provider;
        _scroll = 0;
        _view = View::Result;
        sfx::confirm();
        invalidate();
    }

    void saveResult() {
        store::Record row;
        row.kind = "inbox";
        row.status = "open";
        row.title = titleFor(_kind);
        row.body = _result;
        row.source = _provider.empty() ? "hub" : _provider;
        if (!_project.empty()) row.ref = _project;
        if (store::addRecord(row)) {
            notify::post(Note::Success, "Saved to Inbox", titleFor(_kind));
            sfx::confirm();
        }
    }

    ToolKind _kind;
    View _view = View::Project;
    TextField _task;
    ListCursor _projectCursor;
    ListCursor _templateCursor;
    std::vector<host::CoreProject> _projects;
    std::string _project;
    std::string _projectError;
    std::string _templateId = "maz-feature";
    std::string _result;
    std::string _provider;
    int _scroll = 0;
    bool _ok = false;
    uint32_t _lastPaint = 0;
    uint32_t _lastTry = 0;
    uint32_t _loadAt = 0;
    bool _loading = false;
};

}  // namespace

App* makePlan() { return new AgentToolApp(ToolKind::Plan); }
App* makePromptDeck() { return new AgentToolApp(ToolKind::Prompt); }
App* makeCrew() { return new AgentToolApp(ToolKind::Crew); }
App* makeRetro() { return new AgentToolApp(ToolKind::Retro); }

}  // namespace apps
}  // namespace maz
