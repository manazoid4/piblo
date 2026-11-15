// Piblo — the app registry.
#pragma once
#include <stddef.h>

#include "../core/app.h"

namespace maz {
namespace store { struct Record; }
namespace apps {

struct Descriptor {
    const char* id;
    const char* title;
    const char* shortTitle;
    const char* keywords;
    uint8_t     shortcut;
    bool        onHome;
    App* (*make)();

    const char* cellTitle() const { return shortTitle ? shortTitle : title; }
};

const Descriptor* table(size_t& count);

// One plain sentence saying what is wrong with the link and what to press, or
// nullptr when Wi-Fi, pairing and Core are fine. `quiet` waits for boot to
// settle and stays silent in field mode (Home); Call passes false after a fail.
const char* linkSentence(bool quiet);
const Descriptor* find(const char* id);
App*              create(const char* id);

App* makeHome();
App* makeLive();
App* makeComm();
App* makeCapture();
App* makePcType();
App* makeCaptureHub();
App* makeTeach();
App* makeAgentsHub();
App* makeDesk();
App* makeRecall();
App* makeFlow();
App* makeFlowTools();
App* makeControlCenter();
App* makeNetworkV5();
App* makeCoreConsole();
App* makePairing();
App* makeConnectPc();
App* makeNotes();
App* makeFocus();
App* makeTasks();
App* makeRecorder();
App* makeTools();
App* makeSettings();
App* makeSay();
App* makeVoice();
App* makeConnections();
App* makeSnippets();
App* makeCalculator();
App* makeStopwatch();
App* makeCharging();
App* makeQr();
App* makeViewer();
App* makeGenerator();
App* makeHelp();
App* makeInbox();
App* makeDecision();
App* makeSprint();
App* makeAgentsV3();
App* makeReminders();

App* makePlan();
App* makePromptDeck();
App* makeCrew();
App* makeRetro();

App* makeToday();
App* makeLaptop();
App* makeBeam();
App* makeShift();

void scheduleReminder(store::Record& reminder, uint32_t delaySeconds);
void updateProductServices();

}  // namespace apps
}  // namespace maz
