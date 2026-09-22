"""Prepare a reusable, project-local runtime without modifying system packages.

Internal helper for audit.py --prepare; this module is not a public CLI.
Only the Python standard library is needed before preparation.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent.parent


class BootstrapError(RuntimeError):
    """An actionable setup error suitable for the public CLI."""


def _progress(message):
    print(message, file=sys.stderr, flush=True)


def _run(command, purpose, cwd=None, timeout=300):
    try:
        result = subprocess.run(
            [str(part) for part in command], cwd=str(cwd) if cwd else None,
            env=os.environ.copy(), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError("{}: הפעולה חרגה מהזמן המותר. בדקו את החיבור והריצו שוב.".format(purpose)) from exc
    except OSError as exc:
        raise BootstrapError("{}: לא ניתן להפעיל את הכלי הנדרש ({})".format(purpose, exc)) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise BootstrapError("{} נכשלה (קוד {}). בדקו את הודעת הכלי והריצו שוב:\n{}".format(
            purpose, result.returncode, detail))
    return result.stdout.strip()


def _digest(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _matches(stamp, expected):
    try:
        return stamp.read_text(encoding="utf-8").strip() == expected
    except OSError:
        return False


def _save(stamp, value):
    temporary = stamp.with_suffix(".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    temporary.replace(stamp)


def _cache_dir():
    override = os.environ.get("A11Y_AUDITOR_CACHE")
    if override:
        directory = Path(override).expanduser()
        if not directory.is_absolute():
            raise BootstrapError("A11Y_AUDITOR_CACHE חייב להיות נתיב מוחלט לתיקיית מטמון ניתנת לכתיבה.")
    else:
        directory = ROOT / ".runtime"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BootstrapError("לא ניתן לכתוב לתיקיית ההכנה. הגדירו A11Y_AUDITOR_CACHE לנתיב מוחלט נגיש: {}".format(exc)) from exc
    return directory.resolve()


def _prepare_python(cache):
    requirements = ROOT / "requirements.txt"
    if not requirements.is_file():
        raise BootstrapError("requirements.txt חסר. התקינו עותק מלא של הכלי ונסו שוב.")
    environment = cache / "venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    created = not python.is_file()
    if created:
        _progress("מכין סביבת Python מבודדת לכלי...")
        try:
            venv.EnvBuilder(with_pip=True).create(str(environment))
        except (OSError, subprocess.SubprocessError) as exc:
            raise BootstrapError("יצירת סביבת Python נכשלה. ודאו שמותקן Python עם תמיכה ב־venv: {}".format(exc)) from exc
    stamp = cache / "python-requirements.sha256"
    expected = _digest([requirements])
    if created or not _matches(stamp, expected):
        _progress("מתקין את תלויות Python הנעולות בסביבה המבודדת...")
        _run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", requirements],
             "התקנת תלויות Python")
        _save(stamp, expected)
    else:
        _progress("משתמש בסביבת Python שהוכנה קודם.")
    return str(python)


def _node_tools():
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise BootstrapError("לסריקה בדפדפן נדרשים Node.js 20 ומעלה ו־npm. התקינו אותם והריצו שוב; לא בוצע שינוי מערכת.")
    version = _run([node, "--version"], "בדיקת Node.js", timeout=15)
    match = re.fullmatch(r"v?(\d+)\.\d+\.\d+(?:[-+].*)?", version)
    if not match or int(match.group(1)) < 20:
        raise BootstrapError("לסריקה בדפדפן נדרש Node.js 20 ומעלה; הגרסה שנמצאה: {}".format(version))
    _run([npm, "--version"], "בדיקת npm", timeout=15)
    return node, npm


def _modules_match(directory, package):
    try:
        for name, version in package["dependencies"].items():
            installed = json.loads((directory / "node_modules" / name / "package.json").read_text(encoding="utf-8"))
            if installed.get("version") != version:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _prepare_browser(cache):
    node, npm = _node_tools()
    manifest, lock = ROOT / "package.json", ROOT / "package-lock.json"
    if not manifest.is_file() or not lock.is_file():
        raise BootstrapError("קובצי package.json או package-lock.json חסרים. התקינו עותק מלא של הכלי.")
    try:
        package = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(package.get("dependencies"), dict):
            raise ValueError("dependencies missing")
    except (ValueError, OSError) as exc:
        raise BootstrapError("קובץ התלויות של הדפדפן אינו תקין; התקינו מחדש את הכלי.") from exc
    directory = cache / "node"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = cache / "node-packages.sha256"
    expected = _digest([manifest, lock])
    if not _matches(stamp, expected) or not _modules_match(directory, package):
        _progress("מתקין את Playwright ו־axe-core הנעולים במטמון המקומי...")
        shutil.copyfile(manifest, directory / manifest.name)
        shutil.copyfile(lock, directory / lock.name)
        _run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], "התקנת רכיבי הדפדפן", cwd=directory)
        _save(stamp, expected)
    else:
        _progress("משתמש ברכיבי הדפדפן שהותקנו קודם.")
    modules = str(directory / "node_modules")
    prior_modules = os.environ.get("NODE_PATH", "").split(os.pathsep)
    os.environ["NODE_PATH"] = os.pathsep.join([modules] + [item for item in prior_modules if item and item != modules])
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "browsers")
    browser_stamp = cache / "chromium.sha256"
    executable = _run([node, "-e", "process.stdout.write(require('playwright').chromium.executablePath())"],
                      "בדיקת נתיב Chromium", cwd=directory, timeout=15)
    if not _matches(browser_stamp, expected) or not Path(executable).is_file():
        _progress("מוריד Chromium למטמון הכלי בלבד; אין התקנת רכיבי מערכת...")
        _run([node, directory / "node_modules/playwright/cli.js", "install", "chromium"],
             "התקנת Chromium", cwd=directory, timeout=600)
        if not Path(executable).is_file():
            raise BootstrapError("התקנת Chromium הסתיימה ללא קובץ הדפדפן הצפוי. בדקו הרשאות והריצו שוב.")
        _save(browser_stamp, expected)
    else:
        _progress("משתמש בדפדפן Chromium שהוכן קודם.")


def prepare(needs_browser: bool) -> str:
    """Return managed Python and persist browser environment for the CLI re-exec."""
    if sys.version_info < (3, 9):
        raise BootstrapError("נדרש Python 3.9 ומעלה. התקינו גרסה מתאימה והריצו שוב.")
    try:
        cache = _cache_dir()
        python = _prepare_python(cache)
        if needs_browser:
            _prepare_browser(cache)
        return python
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError("הכנת הכלי נכשלה. בדקו הרשאות כתיבה ומקום פנוי במטמון: {}".format(exc)) from exc
