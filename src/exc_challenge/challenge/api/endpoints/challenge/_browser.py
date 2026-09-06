"""Chrome-for-Testing driver layer. Single entry point: `run_round()`.

Every non-obvious constraint below is marked `see REFERENCE §n` and explained in
docs/REFERENCE.md. Changing one of those lines without reading the section it
points at is how this file silently starts producing wrong labels.
"""

import json
import os
import shutil
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

import psutil
from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchWindowException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from api.logger import logger

# REFERENCE §5. Flags outside this list need a reason; several of the usual
# "disable everything" flags silently break extension loading.
_BASE_ARGS = (
    "--no-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--window-size=1920,1080",
    "--force-device-scale-factor=1",
    "--hide-scrollbars",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-search-engine-choice-screen",
    "--disable-notifications",
    "--disable-infobars",
    "--disable-popup-blocking",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-ipc-flooding-protection",
    "--enable-automation",
    "--metrics-recording-only",
    "--disable-breakpad",
    "--noerrdialogs",
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-features=Translate,OptimizationHints,MediaRouter,"
    "InterestFeedContentSuggestions,CalculateNativeWinOcclusion,BackForwardCache",
    "--lang=en-US",
    "--accept-lang=en-US,en",
)

_SCRIPT_TIMEOUT_MARGIN_SEC = 5.0  # Selenium must lose the race to our sentinel
_KILL_GRACE_SEC = 3.0
# Password managers inject their overlay a beat AFTER the gesture that triggers
# it, so sampling immediately would miss them. Provisional - wants measuring
# against the pool alongside `time_to_stable_ms`.
_GESTURE_SETTLE_SEC = 1.5
_INTERNALS_URL = "chrome://extensions-internals/"

# A fixed, deterministic gesture script. Identical on every round, so it cannot
# leak which extensions are enabled. Some extensions - password managers above
# all - only inject after a real user gesture on the field they care about, so
# this is a detection surface, not decoration.
_INTERACTIONS = (
    ("click", "#accept-cookies", None),
    ("type", "#username", "casey@example.com"),
    ("type", "#password", "hunter2-not-a-real-password"),
    ("type", "#notes", " Following up on the action items."),
    ("click", "#rich-editor", None),
    ("scroll", "#ad-banner", None),
)


class BrowserError(RuntimeError):
    """A round could not be run. The caller records it as failed and continues."""


class BrowserInfraError(BrowserError):
    """The browser failed us - staging, launch, navigation or a dead renderer.

    Separate from `BrowserError` because the blame differs. Reserve this for the
    session itself failing - staging, launch, navigation, a dead renderer.

    A timeout is NOT one of these. The submission loads with the page and runs
    before we get control, so it can stall `driver.get()` or outlast the script
    budget by itself; `TimeoutException` therefore stays a plain `BrowserError`.
    See `run_script` for why the in-page sentinel cannot be trusted to catch
    that first.

    This matters because failed rounds score 0 and drag the mean down: a run
    broken by resource contention returns a low score that reads as a weak
    miner. `service.py` refuses to publish a score once too many rounds fail
    this way.
    """


@dataclass(frozen=True)
class BrowserSettings:
    """Launch settings. Field-for-field the same shape as
    `config.challenge.browser`, but plain, so this module is importable and
    testable without the app config."""

    chrome_bin: str
    chromedriver_bin: str
    extensions_dir: str
    scratch_dir: str
    headless: bool = True
    shm_fallback: bool = False
    page_load_timeout_sec: float = 30.0


def wrap_miner_script(budget_sec: float, groups: Mapping[str, Sequence[str]]) -> str:
    """Call every group's entrypoint so a hang, a per-group throw and a result
    are all reportable - and so ONE group throwing cannot cost the others.

    The miner's code is NOT injected here. Each group's file is served as
    `static/detections/<group>.js` and loaded by the bait page's own
    `<script src>` tags, so by the time this runs every `window.detect_<group>`
    that the miner defined is already in place. This wrapper only invokes them
    and normalises the outcome.

    Each group gets its OWN try/catch inside a `Promise.all`, so a throw, a
    missing function or a non-object return costs only that group - never the
    whole round. That isolation is the point of the split.

    A group answers only for the ids it OWNS; other keys are dropped. Without
    the filter two files claiming the same id would race on completion order,
    and the same submission could score differently run to run.

    The `setTimeout` sentinel below is best-effort, not enforcement. It runs in
    the same JS world as the submission, which loaded first and may already have
    replaced `setTimeout` - and a busy loop blocks the thread so no timer fires
    at all. It buys a clean `__timeout` payload from honest miners; the real
    budget is Selenium's out-of-process one. See `ChromeSession.run_script`.
    """
    _group_calls = ",\n".join(
        f"""
                (async () => {{
                    const owned = new Set({json.dumps(list(_ids))});
                    try {{
                        if (typeof window.detect_{group} !== "function") {{
                            failedGroups.push({json.dumps(group)});
                            return;
                        }}
                        const result = await window.detect_{group}();
                        if (result && typeof result === "object") {{
                            for (const key of Object.keys(result)) {{
                                if (owned.has(key)) merged[key] = result[key];
                            }}
                        }} else {{
                            failedGroups.push({json.dumps(group)});
                        }}
                    }} catch (err) {{
                        failedGroups.push({json.dumps(group)});
                    }}
                }})()"""
        for group, _ids in groups.items()
    )
    return f"""
        const done = arguments[arguments.length - 1];
        let settled = false;
        const finish = (payload) => {{
            if (settled) return;
            settled = true;
            done(payload);
        }};
        const timer = setTimeout(() => finish({{__timeout: true}}), {int(budget_sec * 1000)});
        (async () => {{
            try {{
                const merged = {{}};
                const failedGroups = [];
                await Promise.all([{_group_calls}
                ]);
                finish({{ok: merged, failed_groups: failedGroups}});
            }} catch (err) {{
                finish({{__error: String((err && err.stack) || err)}});
            }} finally {{
                clearTimeout(timer);
            }}
        }})();
    """


def normalize_predictions(raw: Any, pool: list[str]) -> dict[str, bool]:
    """One boolean per pool id. Missing and non-boolean values become False;
    keys outside the pool are dropped."""
    if not isinstance(raw, dict):
        raise BrowserError(
            f"miner returned {type(raw).__name__}, expected {{extensionId: boolean}}"
        )
    return {ext_id: bool(raw.get(ext_id, False)) for ext_id in pool}


class ChromeSession:
    """One round: one scratch dir, one browser, torn down on exit.

    Scratch lives under a single per-round root so the process sweeper can
    identify this round's Chrome processes by path and cannot touch a
    concurrent round's.
    """

    def __init__(self, settings: BrowserSettings, tag: str) -> None:
        self.settings = settings
        self.tag = tag
        self.root = Path(settings.scratch_dir) / f"round-{tag}"
        self.profile_dir = self.root / "profile"
        self.ext_root = self.root / "ext"
        self.driver: webdriver.Chrome | None = None
        self.loaded: set[str] = set()  # real store ids Chrome actually enabled
        self._driver_pid: int | None = None

    def __enter__(self) -> "ChromeSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.ext_root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- launch ------------------------------------------------------------

    def launch(self, ext_ids: list[str]) -> None:
        """Start Chrome with `ext_ids` loaded, and prove they loaded.

        Populates `self.loaded` so callers can report what enabled without a
        second round trip to chrome://extensions-internals/.
        """
        ext_dirs = self._stage_extensions(ext_ids)
        self._start_driver(self._build_options(ext_dirs))
        self.loaded = self._read_loaded_ids()

        missing = set(ext_ids) - self.loaded
        if missing:
            # REFERENCE §3: a mis-keyed extension is otherwise a permanent silent
            # false negative, capping MCC for a reason no miner can fix.
            # Names only what FAILED, never `self.loaded`. The two together
            # reconstruct this round's enabled subset - the answer key - and
            # this message reaches the server log, which prod bind-mounts out
            # of the container. The failures alone are what a mis-keyed or
            # missing extension needs for diagnosis.
            raise BrowserInfraError(
                f"{len(missing)} of {len(ext_ids)} extension(s) did not load: "
                f"{sorted(missing)}"
            )

    def _stage_extensions(self, ext_ids: list[str]) -> list[Path]:
        # REFERENCE §7: Chrome rewrites `_metadata/` on every unpacked load. A
        # read-only source dir makes static DNR silently no-op, so we copy.
        source_root = Path(self.settings.extensions_dir)
        staged = []

        for ext_id in ext_ids:
            if "," in ext_id:
                raise BrowserInfraError(
                    f"id '{ext_id}' has a comma; --load-extension splits on it"
                )

            src = source_root / ext_id
            if not src.is_dir():
                raise BrowserInfraError(
                    f"'{ext_id}' not unpacked under {source_root} - "
                    f"run scripts/fetch_extensions.py"
                )

            dst = self.ext_root / ext_id
            shutil.copytree(src, dst)
            _make_writable(dst)
            staged.append(dst)

        return staged

    def _build_options(self, ext_dirs: list[Path]) -> Options:
        options = Options()
        options.binary_location = self.settings.chrome_bin

        for arg in _BASE_ARGS:
            options.add_argument(arg)

        if self.settings.headless:
            options.add_argument(
                "--headless=new"
            )  # REFERENCE §1: not `=old`, not headless-shell
        if self.settings.shm_fallback:
            options.add_argument("--disable-dev-shm-usage")

        options.add_argument(f"--user-data-dir={self.profile_dir}")
        options.add_argument("--profile-directory=Default")
        # REFERENCE §2: --load-extension, never add_extension() or BiDi install.
        options.add_argument(
            "--load-extension=" + ",".join(str(d.resolve()) for d in ext_dirs)
        )
        return options

    def _start_driver(self, options: Options) -> None:
        service = Service(
            # explicit path so Selenium Manager never runs (it needs network)
            executable_path=self.settings.chromedriver_bin,
            popen_kw={"start_new_session": True},  # own process group, for killpg
        )
        try:
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(self.settings.page_load_timeout_sec)
        except WebDriverException as err:
            # Recorded even on failure: the constructor may have started
            # chromedriver before giving up, and `close()` needs the pid to kill
            # the process group.
            self._driver_pid = getattr(getattr(service, "process", None), "pid", None)
            raise BrowserInfraError(f"could not start Chrome: {err}") from err

        self._driver_pid = getattr(getattr(service, "process", None), "pid", None)

    def _read_loaded_ids(self) -> set[str]:
        try:
            self.driver.get(_INTERNALS_URL)
            entries = json.loads(self.driver.find_element("tag name", "pre").text)
            return {e["id"] for e in entries if isinstance(e, dict) and "id" in e}
        except Exception as err:  # page shape may drift on upgrade
            logger.warning(
                f"[{self.tag}] {_INTERNALS_URL} unreadable ({err}); using Preferences"
            )
            return self._loaded_ids_from_prefs()

    def _loaded_ids_from_prefs(self) -> set[str]:
        prefs_path = self.profile_dir / "Default" / "Preferences"
        if not prefs_path.is_file():
            return set()
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        return set(prefs.get("extensions", {}).get("settings", {}))

    # -- run ---------------------------------------------------------------

    def open_page(self, page_url: str, settle_seconds: float) -> None:
        """Load the bait page and wait for extensions to act.

        Service workers spin up, DNR rulesets re-index and content scripts
        inject asynchronously; sampling early turns a detectable extension into
        a false negative. The window comes from the pool's measured
        `time_to_stable_ms`.
        """
        try:
            self.driver.get(page_url)
        except TimeoutException as err:
            # index.html pulls the submission in with a render-blocking
            # `<script src>` in `<head>`, so its top-level code runs inside this
            # navigation. A miner that blocks there stalls the load, and that is
            # the miner's doing - not ours. See `run_script`.
            raise BrowserError(
                f"the bait page did not load within "
                f"{self.settings.page_load_timeout_sec}s; the submission runs "
                f"during load and can block it"
            ) from err
        except WebDriverException as err:
            raise BrowserInfraError(f"could not load the bait page: {err}") from err
        time.sleep(settle_seconds)
        self._assert_page_rendered()

    def _assert_page_rendered(self) -> None:
        """Fail loudly if the bait page came back empty.

        The page is a React bundle: `<div id="root">` is filled in by
        `static/js/main.*.js`. If that asset 404s - a bad sync, a renamed
        hash - navigation still succeeds with HTTP 200 and an empty body.
        Every extension then has nothing to react to, every detector returns
        nothing, and ALL miners score 0 with no error anywhere.

        Infra, not miner: this is our asset failing, so the round must not
        count against whoever happened to be scored when it broke.
        """
        try:
            rendered = self.driver.execute_script(
                "const r = document.getElementById('root');"
                "return !!r && r.childElementCount > 0;"
            )
        except WebDriverException as err:
            raise BrowserInfraError(
                f"could not check whether the bait page rendered: {err}"
            ) from err

        if not rendered:
            raise BrowserInfraError(
                "the bait page loaded but rendered nothing (#root is empty) - "
                "its script bundle is probably missing; check "
                "templates/static/js/ against index.html"
            )

    def run_script(
        self,
        pool: list[str],
        groups: Mapping[str, Sequence[str]],
        budget_sec: float,
    ) -> dict[str, bool]:
        """Invoke every group's entrypoint and normalise what came back.

        Two clocks can end this call, and they assign blame differently.

        The wrapper's in-page sentinel is the fast path but NOT authoritative -
        it shares a JS world with the submission, which can disarm it or block
        the thread outright (see `wrap_miner_script`). Selenium's script
        timeout is enforced out-of-process, where page JS cannot reach it, so
        that is what actually bounds the budget.

        Hence the split below. `TimeoutException` means the script never
        yielded, which is the submission's problem however it came about. Any
        other `WebDriverException` is the session dying - "tab crashed",
        "chrome not reachable" - which is what shm exhaustion under concurrency
        looks like, and is ours.

        Getting this backwards is not cosmetic. `service.py` refuses to publish
        a score once too many rounds raise `BrowserInfraError`, so blaming
        ourselves for a miner's hang hands any submission a way to turn its own
        timeout into a failed run - a 500 to the validator instead of the 0.0
        it earned.

        A single group throwing must NOT cost the others - that isolation
        happens inside `wrap_miner_script`, per group. This layer only refuses
        to publish a result once EVERY group failed, which is
        indistinguishable from the whole script being broken.
        """
        self.driver.set_script_timeout(budget_sec + _SCRIPT_TIMEOUT_MARGIN_SEC)
        try:
            result = self.driver.execute_async_script(
                wrap_miner_script(budget_sec, groups)
            )
        except TimeoutException as err:
            raise BrowserError(
                f"miner script exceeded its {budget_sec}s budget without "
                f"yielding (the in-page timer never fired)"
            ) from err
        except WebDriverException as err:
            raise BrowserInfraError(f"browser died running the script: {err}") from err

        if not isinstance(result, dict):
            raise BrowserError(f"wrapper returned {type(result).__name__}")
        if result.get("__timeout"):
            raise BrowserError(f"miner script exceeded its {budget_sec}s budget")
        if "__error" in result:
            raise BrowserError(f"miner script threw: {result['__error']}")

        failed_groups = sorted(result.get("failed_groups") or [])
        if groups and set(failed_groups) >= set(groups):
            raise BrowserError(
                f"every group failed ({len(groups)}): {failed_groups}"
            )
        if failed_groups:
            logger.warning(
                f"[{self.tag}] group(s) failed, scored as false: {failed_groups}"
            )

        return normalize_predictions(result.get("ok"), pool)

    def interact(self, pause: float = 0.0) -> list[str]:
        """Drive the page the way a person would; return what actually happened.

        Every step is best-effort: a missing element is skipped, not fatal. The
        page is shared across rounds but the *extensions* are not, and an
        extension can remove or cover an element - a blocked ad banner being the
        obvious case.
        """
        performed = []
        for action, selector, text in _INTERACTIONS:
            try:
                element = self.driver.find_element("css selector", selector)
                if action == "scroll":
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                        element,
                    )
                else:
                    element.click()
                    if text:
                        element.send_keys(text)
                performed.append(f"{action} {selector}")
            except (InvalidSessionIdException, NoSuchWindowException) as err:
                # The browser is GONE. These subclass WebDriverException, so
                # the broad clause below would log "skipped" and march through
                # the remaining gestures before anything noticed. Name the
                # gesture that killed it - that is the only diagnostic we get.
                raise BrowserInfraError(
                    f"browser died during gesture {action} {selector} "
                    f"({type(err).__name__}); completed {performed}"
                ) from err
            except WebDriverException as err:
                # A missing or covered element is normal - a blocker can remove
                # the ad banner.
                performed.append(
                    f"{action} {selector} -> skipped ({type(err).__name__})"
                )
            time.sleep(pause)

        return performed

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        """`driver.quit()` fails exactly when cleanup matters most, so the
        sweep and the rmtree run regardless of whether it succeeded."""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception as err:
                logger.warning(f"[{self.tag}] driver.quit() failed: {err}")
            self.driver = None

        self._sweep_processes()
        shutil.rmtree(self.root, ignore_errors=True)

    def _sweep_processes(self) -> int:
        """Kill leftovers holding this round's scratch path. Matching on that
        path is what keeps a concurrent round's browser safe."""
        if self._driver_pid:
            try:
                os.killpg(os.getpgid(self._driver_pid), signal.SIGTERM)
            except OSError:
                pass

        survivors = self._procs_under_root()
        if not survivors:
            return 0

        for proc in survivors:
            _suppress(proc.terminate)

        _, alive = psutil.wait_procs(survivors, timeout=_KILL_GRACE_SEC)
        for proc in alive:
            _suppress(proc.kill)

        logger.warning(f"[{self.tag}] swept {len(survivors)} leftover process(es)")
        return len(survivors)

    def _procs_under_root(self) -> list[psutil.Process]:
        marker = str(self.root)
        matched = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                # `run-1` is a prefix of `run-10`, so a bare substring test
                # lets one round SIGKILL a concurrent round's Chrome. Match
                # only at a path boundary or an exact end.
                if any(
                    arg == marker or arg.endswith(marker) or f"{marker}{os.sep}" in arg
                    for arg in (proc.info.get("cmdline") or [])
                ):
                    matched.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return matched


def _make_writable(root: Path) -> None:
    """Grant owner-write across the whole copied tree.

    `copytree` preserves the source modes, so chmod'ing only the root leaves a
    read-only source read-only underneath - and Chrome silently fails to
    rewrite `_metadata/`, which makes static DNR a no-op with no error.
    """
    os.chmod(root, 0o755)  # nosec B103 - per-round scratch, same-uid only
    for path in root.rglob("*"):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)  # nosec B103


def _suppress(action: Any) -> None:
    try:
        action()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def run_round(
    subset: set[str],
    *,
    pool: list[str],
    groups: Mapping[str, Sequence[str]],
    page_url: str,
    settings: BrowserSettings,
    settle_seconds: float = 4.0,
    script_budget_sec: float = 10.0,
    round_tag: str | None = None,
) -> dict[str, bool]:
    """Run one round and return the miner's verdict for every id in `pool`.

    `subset` is ground truth and is never written anywhere the browser can
    reach it - not into the page, not into a global, not into a query param.

    `groups` maps each published group name (`extensions.yml`'s `group:`
    values) to the ids it owns. The wrapper calls `window.detect_<group>()` for
    each, in its own try/catch, so one group throwing costs only that group's
    labels - and it keeps only the ids that group owns, so two files cannot
    race to answer for the same extension.

    The gesture script runs on every round. It is fixed and identical each time,
    so it leaks nothing, and without it the whole password-manager class of the
    pool is undetectable - those extensions only inject once a real user has
    touched the field they care about.
    """
    if not subset:
        raise BrowserError(
            "subset is empty; a round must enable at least one extension"
        )

    tag = round_tag or uuid.uuid4().hex[:12]
    with ChromeSession(settings, tag) as session:
        session.launch(sorted(subset))
        session.open_page(page_url, settle_seconds)
        session.interact()
        time.sleep(_GESTURE_SETTLE_SEC)
        return session.run_script(pool, groups, script_budget_sec)


__all__ = [
    "BrowserError",
    "BrowserInfraError",
    "BrowserSettings",
    "ChromeSession",
    "run_round",
    "wrap_miner_script",
    "normalize_predictions",
]
