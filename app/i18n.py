"""Lightweight i18n: gettext-style catalog where the key IS the English text.

Translations live in translations/<locale>/*.json fragment files (one per UI
area, merged at load; later files win on duplicate keys). t(text) returns the
translated string for the request locale, falling back to the English key, so
untranslated strings degrade gracefully instead of breaking.

Locale resolution order (request-scoped):
  ?lang= query param > sm_lang cookie > Accept-Language header > 'en'.
A ?lang= visit persists the choice via the sm_lang cookie (see after_request).
For requestless contexts (cron push/email) use t_for(locale, text) with the
per-user User.locale value.
"""
import json
import os
from flask import g, request

SUPPORTED_LOCALES = ('en', 'hu')
DEFAULT_LOCALE = 'en'
LANG_COOKIE = 'sm_lang'
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

_I18N_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'translations'
)

# locale -> {english_text: translated_text}. English is the identity locale.
_catalogs = {}


def _load_catalog(locale):
    catalog = {}
    locale_dir = os.path.join(_I18N_DIR, locale)
    if not os.path.isdir(locale_dir):
        return catalog
    for fname in sorted(os.listdir(locale_dir)):
        if not fname.endswith('.json'):
            continue
        with open(os.path.join(locale_dir, fname), encoding='utf-8') as f:
            fragment = json.load(f)
        if not isinstance(fragment, dict):
            raise ValueError(f'i18n fragment {locale}/{fname} must be a JSON object')
        catalog.update(fragment)
    return catalog


def load_translations(force=False):
    for locale in SUPPORTED_LOCALES:
        if locale == DEFAULT_LOCALE:
            continue
        if force or locale not in _catalogs:
            _catalogs[locale] = _load_catalog(locale)
    return _catalogs


def resolve_locale():
    lang = (request.args.get('lang') or '').lower()
    if lang in SUPPORTED_LOCALES:
        return lang
    cookie = (request.cookies.get(LANG_COOKIE) or '').lower()
    if cookie in SUPPORTED_LOCALES:
        return cookie
    header = request.accept_languages.best_match(SUPPORTED_LOCALES)
    return header or DEFAULT_LOCALE


def get_locale():
    if not hasattr(g, 'locale'):
        g.locale = resolve_locale()
    return g.locale


def t_for(locale, text, **kwargs):
    """Translate for an explicit locale (cron/email contexts without a request)."""
    translated = _catalogs.get(locale, {}).get(text, text)
    return translated.format(**kwargs) if kwargs else translated


def t(text, **kwargs):
    """Translate for the current request locale; key doubles as English fallback."""
    return t_for(get_locale(), text, **kwargs)


def client_catalog():
    """The dict injected as window.I18N (empty for the default locale)."""
    return _catalogs.get(get_locale(), {})


def init_i18n(app):
    load_translations()

    app.jinja_env.globals['t'] = t

    @app.context_processor
    def inject_i18n():
        return {
            'locale': get_locale(),
            'i18n_client': client_catalog(),
        }

    @app.after_request
    def persist_lang_choice(response):
        lang = (request.args.get('lang') or '').lower()
        if lang in SUPPORTED_LOCALES and request.cookies.get(LANG_COOKIE) != lang:
            response.set_cookie(
                LANG_COOKIE, lang, max_age=_COOKIE_MAX_AGE,
                samesite='Lax', secure=request.is_secure,
            )
        return response
