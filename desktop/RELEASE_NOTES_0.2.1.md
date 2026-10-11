# Kato Desktop 0.2.1

A fix-up release. The app now says what went wrong when Kato can't start, the review loop gets failing tests fixed before it reviews again, and review-loop tabs behave like the rest of the tab strip.

## Fixes

- **No more blank window when Kato can't start.** If something stops Kato before its window loads, the app now shows what happened. That covers four cases: the port Kato needs is taken by another program (it names the program, most often a `kato up` still running in a terminal), the Kato service is missing from the install, the service failed to start, or it didn't answer in time. Details are in `~/.kato/kato-desktop.log`.
- **Kato no longer runs twice.** When its web server can't get its port, Kato now stops and says why. Before, it kept running as a hidden second copy that still picked up tasks.
- **Failing tests are fixed before the next review.** When a review comes back clean but the tests fail, the chat fixes them and the tests run again, in the same round, until they pass. No other review starts while they fail. After 3 fixes that don't make them pass, the loop stops as stuck and names the failing tests; **Resume** gives it one more try.
- **Review-loop tabs act like tabs.** A file opened while a loop tab is showing opens right after it. Closing a tab shows the tab next to it, including the loop tab, instead of an empty editor. The **New task** tab works the same way.

## Changed

- Review-loop rounds start collapsed; open the ones you want to read.
- Each round lists every test run, and the loop's progress says when it is fixing or re-running the tests.

## Install

Grab the file for your OS from the Assets below. **Kato 0.1.0 and 0.2.0 update themselves.** They check for the new release, verify its signature, and install it on restart.

**macOS** (`Kato_0.2.1_aarch64.dmg`, Apple Silicon): open the `.dmg` and drag **Kato** to Applications. The app is signed and notarized by Apple.

**Windows** (`.exe` installer): run it and follow the prompts. The installer isn't signed yet, so Windows SmartScreen may warn; choose **More info → Run anyway**.

**Linux** (`.AppImage`): `chmod +x Kato_0.2.1_amd64.AppImage`, then run it.

## Requirements

- **`git`**
- An **agent CLI**: Claude Code (`claude`) or Codex
- **Docker with gVisor**, only for the optional hardened sandbox

## Notes

- **Apple Silicon only** on macOS for now; Intel and universal builds are still to come.
- Running Kato from a terminal (`kato up`) and the desktop app at the same time doesn't work: they need the same port. The app now tells you when this is the problem.

---

🤖 Built and released with [Claude Code](https://claude.com/claude-code)
