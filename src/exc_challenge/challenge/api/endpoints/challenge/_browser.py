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
from typing import Any

import psutil
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
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

_MINER_ENTRYPOINT = "detect_extensions"
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

    Separate from `BrowserError` because the blame differs. A miner's own script
    throwing or hanging is caught inside `wrap_miner_script` and comes back as
    `__error`/`__timeout`, so anything that reaches us as a WebDriverException
    is the browser dying, not the submission misbehaving.

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


def wrap_miner_script(miner_js: str, budget_sec: float) -> str:
    """Wrap miner code so a hang, a throw and a result are all reportable."""
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
                {miner_js}
                if (typeof window.{_MINER_ENTRYPOINT} !== "function") {{
                    finish({{__error: "no window.{_MINER_ENTRYPOINT}"}});
                    return;
                }}
                const result = await window.{_MINER_ENTRYPOINT}();
                finish({{ok: result}});
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
            # Log only the IDs that failed to load; the loaded set is the round's
            # answer key and must not leave the Python process.
            raise BrowserInfraError(
                f"extension(s) not loaded, or ids drifted: {sorted(missing)}"
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
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_page_load_timeout(self.settings.page_load_timeout_sec)
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
        except WebDriverException as err:
            raise BrowserInfraError(f"could not load the bait page: {err}") from err
        time.sleep(settle_seconds)

    def run_script(
        self, miner_js: str, pool: list[str], budget_sec: float
    ) -> dict[str, bool]:
        self.driver.set_script_timeout(budget_sec + _SCRIPT_TIMEOUT_MARGIN_SEC)
        result = self.driver.execute_async_script(
            wrap_miner_script(miner_js, budget_sec)
        )

        if not isinstance(result, dict):
            raise BrowserError(f"wrapper returned {type(result).__name__}")
        if result.get("__timeout"):
            raise BrowserError(f"miner script exceeded its {budget_sec}s budget")
        if "__error" in result:
            raise BrowserError(f"miner script threw: {result['__error']}")

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
            except WebDriverException as err:
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
    """Grant owner-write across the whole copied tree. REFERENCE §7.

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
    miner_js: str,
    *,
    pool: list[str],
    page_url: str,
    settings: BrowserSettings,
    settle_seconds: float = 4.0,
    script_budget_sec: float = 10.0,
    round_tag: str | None = None,
) -> dict[str, bool]:
    """Run one round and return the miner's verdict for every id in `pool`.

    `subset` is ground truth and is never written anywhere the browser can
    reach it - not into the page, not into a global, not into a query param.

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
        return session.run_script(miner_js, pool, script_budget_sec)


__all__ = [
    "BrowserError",
    "BrowserInfraError",
    "BrowserSettings",
    "ChromeSession",
    "run_round",
    "wrap_miner_script",
    "normalize_predictions",
]
