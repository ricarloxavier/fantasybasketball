"""HTTP session for hashtagbasketball.com: login, cookie persistence, WebForms postbacks, page cache.

The site is ASP.NET WebForms: every page is one <form id="form1"> that posts
back to itself with __VIEWSTATE / __EVENTVALIDATION. To change a control we
must resend every field the page rendered, with our changes applied, and
re-read the form state from each response before the next change.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..cache import TTLCache
from ..config import Settings

log = logging.getLogger(__name__)

BASE_URL = "https://hashtagbasketball.com"
LOGIN_PATH = "/premium/login"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
DEFAULT_TTL = 30 * 60


class HashtagAuthError(RuntimeError):
    pass


class HashtagPageError(RuntimeError):
    pass


@dataclass
class FormState:
    action: str
    fields: dict[str, str] = field(default_factory=dict)          # name -> value for everything that would be submitted
    selects: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # name -> [(value, text)]
    checkboxes: dict[str, bool] = field(default_factory=dict)      # name -> checked
    postback_targets: dict[str, str] = field(default_factory=dict) # control name -> __doPostBack target
    submit_buttons: dict[str, str] = field(default_factory=dict)   # name -> value

    def name_for(self, suffix: str) -> str:
        """Full control name for a suffix such as 'DDWEEK' or 'Repeater2$ctl13$CBSUN'."""
        candidates = [n for n in list(self.fields) + list(self.checkboxes) + list(self.submit_buttons)
                      if n == suffix or n.endswith("$" + suffix)]
        if not candidates:
            raise HashtagPageError(f"Form control ending in {suffix!r} not found.")
        if len(candidates) > 1:
            raise HashtagPageError(f"Ambiguous control suffix {suffix!r}: {candidates}")
        return candidates[0]

    def select_value(self, suffix: str) -> str:
        return self.fields.get(self.name_for(suffix), "")

    def options(self, suffix: str) -> list[tuple[str, str]]:
        return self.selects.get(self.name_for(suffix), [])


def extract_form_state(html: str, page_url: str) -> FormState:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="form1") or soup.find("form")
    if form is None:
        raise HashtagPageError("No <form> on page (not logged in, or markup changed).")
    state = FormState(action=urljoin(page_url, form.get("action") or page_url))
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        kind = (inp.get("type") or "text").lower()
        value = inp.get("value", "")
        if kind in ("hidden", "text", "password", "email", "number"):
            state.fields[name] = value
        elif kind == "checkbox":
            checked = inp.has_attr("checked")
            state.checkboxes[name] = checked
            if checked:
                state.fields[name] = value or "on"
            onclick = inp.get("onclick") or ""
            if "__doPostBack" in onclick:
                state.postback_targets[name] = name
        elif kind == "radio":
            if inp.has_attr("checked"):
                state.fields[name] = value
            if "__doPostBack" in (inp.get("onclick") or ""):
                state.postback_targets[name] = name
        elif kind == "submit":
            state.submit_buttons[name] = value
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opts = [(o.get("value", o.get_text(strip=True)), o.get_text(strip=True)) for o in sel.find_all("option")]
        state.selects[name] = opts
        chosen = next((o.get("value", "") for o in sel.find_all("option") if o.has_attr("selected")), None)
        if chosen is None and opts:
            chosen = opts[0][0]
        state.fields[name] = chosen or ""
        if "__doPostBack" in (sel.get("onchange") or ""):
            state.postback_targets[name] = name
    for ta in form.find_all("textarea"):
        if ta.get("name"):
            state.fields[ta["name"]] = ta.get_text()
    return state


class HashtagSession:
    """requests.Session with login, cookie file and page cache."""

    def __init__(self, email: str, password: str, cookie_file: Path, cache: Optional[TTLCache] = None,
                 cookie_header: str = "", timeout: float = 30.0):
        self.email = email
        self.password = password
        self.cookie_file = Path(cookie_file)
        self.cache = cache or TTLCache()
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        self._logged_in_this_run = False
        self.last_html: str = ""
        self._load_cookies()
        if cookie_header:
            for part in cookie_header.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    self.http.cookies.set(k, v, domain="hashtagbasketball.com")

    @classmethod
    def from_settings(cls, settings: Settings, cache: Optional[TTLCache] = None) -> "HashtagSession":
        if not settings.hashtag_email or not settings.hashtag_password:
            if not settings.hashtag_cookie:
                raise HashtagAuthError("Set HASHTAG_EMAIL and HASHTAG_PASSWORD (or HASHTAG_COOKIE) in .env.")
        cookie_file = settings.cache_dir.parent / "hashtag_session.json"
        return cls(settings.hashtag_email, settings.hashtag_password, cookie_file,
                   cache=cache or TTLCache(settings.cache_dir, "hashtag"), cookie_header=settings.hashtag_cookie)

    # -- cookies ---------------------------------------------------------------
    def _load_cookies(self) -> None:
        if self.cookie_file.exists():
            try:
                for c in json.loads(self.cookie_file.read_text("utf-8")):
                    self.http.cookies.set(c["name"], c["value"], domain=c.get("domain", "hashtagbasketball.com"), path=c.get("path", "/"))
            except (ValueError, KeyError, OSError) as exc:
                log.warning("Ignoring unreadable cookie file %s: %s", self.cookie_file, exc)

    def _save_cookies(self) -> None:
        rows = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.http.cookies]
        try:
            self.cookie_file.write_text(json.dumps(rows), "utf-8")
            self.cookie_file.chmod(0o600)
        except OSError as exc:
            log.warning("Could not save cookies to %s: %s", self.cookie_file, exc)

    # -- login -----------------------------------------------------------------
    @staticmethod
    def _is_login_page(resp: requests.Response) -> bool:
        return LOGIN_PATH in resp.url.lower() or "myLogin$UserName" in resp.text

    def login(self) -> None:
        if self._logged_in_this_run:
            raise HashtagAuthError("Login already attempted once this run; refusing to retry (check HASHTAG_EMAIL/HASHTAG_PASSWORD).")
        self._logged_in_this_run = True
        if not self.email or not self.password:
            raise HashtagAuthError("Session expired and no HASHTAG_EMAIL/HASHTAG_PASSWORD to log in with.")
        url = BASE_URL + LOGIN_PATH
        page = self.http.get(url, timeout=self.timeout)
        page.raise_for_status()
        state = extract_form_state(page.text, url)
        payload = dict(state.fields)
        payload[state.name_for("myLogin$UserName")] = self.email
        payload[state.name_for("myLogin$Password")] = self.password
        payload[state.name_for("myLogin$LoginButton")] = state.submit_buttons.get(state.name_for("myLogin$LoginButton"), "Log In")
        resp = self.http.post(state.action, data=payload, headers={"Referer": url}, timeout=self.timeout)
        resp.raise_for_status()
        if self._is_login_page(resp):
            raise HashtagAuthError("Hashtag Basketball login failed: still on the login page (wrong credentials?).")
        self._save_cookies()
        log.info("Logged in to hashtagbasketball.com")

    # -- requests --------------------------------------------------------------
    def _request(self, method: str, url: str, **kw) -> requests.Response:
        resp = self.http.request(method, url, timeout=self.timeout, **kw)
        if self._is_login_page(resp):
            log.info("Not logged in (redirected to %s); logging in", resp.url)
            self.login()
            resp = self.http.request(method, url, timeout=self.timeout, **kw)
            if self._is_login_page(resp):
                raise HashtagAuthError("Still redirected to login after logging in.")
        resp.raise_for_status()
        self.last_html = resp.text
        return resp

    def get(self, path: str, params: Optional[Mapping[str, str]] = None, ttl: float = DEFAULT_TTL, use_cache: bool = True) -> str:
        url = urljoin(BASE_URL, path)
        key = TTLCache.key_for("GET", url, dict(params or {}))
        return self.cache.get_or_fetch(key, ttl, lambda: self._request("GET", url, params=params).text, use_cache=use_cache)

    def postback(self, page_url: str, state: FormState, overrides: Mapping[str, Optional[str]], event_target: str = "",
                 submit: str = "", ttl: float = DEFAULT_TTL, use_cache: bool = True) -> str:
        """Resubmit the form with `overrides` (full control names; None = omit, e.g. uncheck a box).

        Exactly one of `event_target` (AutoPostBack control / link) or `submit` (button name) is sent.
        """
        payload = dict(state.fields)
        for name, value in overrides.items():
            if value is None:
                payload.pop(name, None)
            else:
                payload[name] = value
        if event_target and submit:
            raise ValueError("Pass either event_target or submit, not both.")
        payload["__EVENTTARGET"] = event_target or ""
        payload["__EVENTARGUMENT"] = ""
        if submit:
            payload[submit] = state.submit_buttons.get(submit, "Submit")
        cache_key = TTLCache.key_for("POST", state.action, {k: v for k, v in payload.items() if not k.startswith("__")}, event_target, submit)

        def _post() -> str:
            resp = self._request("POST", state.action, data=payload, headers={"Referer": page_url})
            if "Invalid postback or callback argument" in resp.text:
                self.dump_last("invalid_postback")
                raise HashtagPageError("Hashtag rejected the postback (viewstate/event validation). The form state may be stale.")
            return resp.text

        return self.cache.get_or_fetch(cache_key, ttl, _post, use_cache=use_cache)

    def dump_last(self, label: str = "last_error") -> Optional[Path]:
        if not self.cache.disk_dir or not self.last_html:
            return None
        path = self.cache.disk_dir / f"{label}.html"
        try:
            path.write_text(self.last_html, "utf-8")
        except OSError:
            return None
        return path
