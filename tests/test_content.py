from zet_discord.content import embeds_of, flatten, iso, reaction_label, snowflake_time

CHANNELS = {"10": "general"}
ROLES = {"20": "mods"}


def test_user_mentions_are_normalised_and_kept():
    assert flatten("hi <@!5> and <@6>", CHANNELS, ROLES) == "hi <@5> and <@6>"


def test_channel_and_role_mentions_become_text():
    assert flatten("see <#10>, ping <@&20>", CHANNELS, ROLES) == "see #general, ping @mods"


def test_unknown_channel_and_role_get_placeholders():
    assert flatten("<#99> <@&98>", CHANNELS, ROLES) == "#deleted-channel @deleted-role"


def test_custom_emoji_become_names_and_unicode_stays():
    assert flatten("<:party:123> <a:wave:456> \N{THUMBS UP SIGN}", CHANNELS, ROLES) == ":party: :wave: \N{THUMBS UP SIGN}"


def test_markdown_is_untouched():
    text = "**bold** `code` <https://example.com> ```py\nx\n```"
    assert flatten(text, CHANNELS, ROLES) == text


def test_iso_normalises_to_utc_seconds():
    assert iso("2024-03-01T12:00:00.123000+02:00") == "2024-03-01T10:00:00Z"


def test_snowflake_time():
    assert iso(snowflake_time("175928847299117063")) == "2016-04-30T11:18:25Z"


def test_reaction_label():
    assert reaction_label({"id": None, "name": "\N{THUMBS UP SIGN}"}) == "\N{THUMBS UP SIGN}"
    assert reaction_label({"id": "1", "name": "party"}) == ":party:"


def test_embeds_use_original_image_and_skip_urlless():
    raw = [
        {"url": "https://a.test", "title": "T", "description": "D",
         "provider": {"name": "A"}, "thumbnail": {"url": "https://a.test/t.png", "proxy_url": "https://media.discordapp.net/t"}},
        {"title": "no url"},
        {"url": "https://b.test", "image": {"url": "https://b.test/i.png", "proxy_url": "p"}, "thumbnail": {"url": "x"}},
    ]
    assert embeds_of(raw) == [
        {"url": "https://a.test", "title": "T", "description": "D", "site_name": "A", "image_url": "https://a.test/t.png"},
        {"url": "https://b.test", "title": None, "description": None, "site_name": None, "image_url": "https://b.test/i.png"},
    ]
