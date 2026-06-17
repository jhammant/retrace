"""Caption generation: template + Foundation Models path with graceful fallback."""

from __future__ import annotations

from retrace.capture import caption as cap


def test_domain_of():
    assert cap.domain_of("https://www.example.com/x") == "example.com"
    assert cap.domain_of("https://news.ycombinator.com") == "news.ycombinator.com"
    assert cap.domain_of(None) is None
    assert cap.domain_of("not a url") is None


def test_template_caption_variants():
    assert "example.com" in cap.template_caption("Chrome", "Title", "https://example.com")
    assert cap.template_caption("Notes", "My note", None) == "Working in Notes: My note."
    assert cap.template_caption("Finder", None, None) == "Using Finder."


def test_make_caption_disabled(settings):
    from retrace import config as cfg

    cfg.update_config({"enable_caption": False})
    s = cfg.get_settings()
    caption, model = cap.make_caption(app="Safari", window="W", url=None, text="t", settings=s)
    assert caption is None and model == "disabled"


def test_make_caption_uses_native_when_available(settings, monkeypatch):
    monkeypatch.setattr("retrace.capture.caption_native.native_caption",
                        lambda **kw: "A crisp on-device summary.")
    caption, model = cap.make_caption(app="Safari", window="W", url=None, text="t", settings=settings)
    assert caption == "A crisp on-device summary."
    assert model == "foundation-models"


def test_make_caption_falls_back_to_template(settings, monkeypatch):
    monkeypatch.setattr("retrace.capture.caption_native.native_caption", lambda **kw: None)
    caption, model = cap.make_caption(
        app="Safari", window="Apple", url="https://apple.com", text="t", settings=settings
    )
    assert model == "template"
    assert "apple.com" in caption
