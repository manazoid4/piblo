"""Guard firmware entry points against bypassing the device confirmation modal."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("relative", ("src/net/portal_v3.cpp", "src/apps/system_apps.cpp"))
def test_update_entry_points_require_shell_confirmation(relative):
    source = (ROOT / relative).read_text(encoding="utf-8-sig")
    assert "host::fwUpdate(" not in source, "Only the shell confirmation may install firmware"
    assert "shell::confirmFwUpdate();" in source


def test_confirmation_requires_physical_u_or_y(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the native confirmation check")
    shell = (ROOT / "src/core/shell.cpp").read_text(encoding="utf-8-sig")
    # Exercise the real modal branch without the ESP32 UI and network dependencies.
    modal = shell.split("bool handleGlobalKey(const KeyEvent& e) {", 1)[1].split("// Ctrl+U:", 1)[0]
    source = tmp_path / "confirmation.cpp"
    source.write_text(
        '#include "src/core/keys.h"\n#include <cassert>\n#include <initializer_list>\n'
        'using namespace maz;\n'
        'bool gFwConfirm = true;\nint installs = 0;\n'
        'namespace host { void fwUpdate() { ++installs; } }\n'
        'void invalidate() {}\n'
        'bool handleGlobalKey(const KeyEvent& e) {' + modal + 'return false; }\n'
        r"""
int main() {
    assert(!KeyEvent{}.physical);
    for (bool physical : {false, true}) {
        for (auto code : {KEY_U, KEY_Y, KEY_ENTER, KEY_A, KEY_ESC, KEY_N}) {
            for (bool down : {false, true}) {
                gFwConfirm = true;
                installs = 0;
                KeyEvent e;
                e.code = code;
                e.down = down;
                e.physical = physical;
                handleGlobalKey(e);
                const bool confirmed = down && physical && (code == KEY_U || code == KEY_Y);
                assert(installs == (confirmed ? 1 : 0));
                assert(gFwConfirm == !(confirmed || (down && (code == KEY_ESC || code == KEY_N))));
            }
        }
    }
    gFwConfirm = false;
    installs = 0;
    KeyEvent e;
    e.code = KEY_Y;
    e.down = true;
    e.physical = true;
    handleGlobalKey(e);
    assert(installs == 0);
}
""",
        encoding="utf-8",
    )
    binary = tmp_path / "confirmation.exe"
    subprocess.run(
        [compiler, "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
