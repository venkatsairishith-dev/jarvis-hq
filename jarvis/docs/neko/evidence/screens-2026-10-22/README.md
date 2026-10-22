# On-screen test pictures (2026-10-22)

Taken by the console UI test suite (57 tests, all passing) running headed Chrome on a VIRTUAL screen inside the server's test container, so
nothing touched the server's real display. 1: Settings & health ("Hub / Agent PC control" reads "Not set up", a real fault such as the local
model stays red). 2: an approval card showing the exact action. 3: the Command Center with an approval waiting.
Re-run: see jarvis/docs/neko/TEST_VISIBLE_2026-10-22.md (laptop) or `npx playwright test` in jarvis/ui with a headed browser.
