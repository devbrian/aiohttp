# type: ignore
import asyncio
import base64
import datetime
import gc
import platform
from math import isclose, modf
from unittest import mock
from urllib.request import getproxies_environment

import pytest
from multidict import CIMultiDict, MultiDict
from re_assert import Matches
from yarl import URL

from aiohttp import helpers
from aiohttp.helpers import is_expected_content_type, parse_http_date

IS_PYPY = platform.python_implementation() == "PyPy"


# ------------------- parse_mimetype ----------------------------------


@pytest.mark.parametrize(
    "mimetype, expected",
    [
        ("", helpers.MimeType("", "", "", MultiDict())),
        ("*", helpers.MimeType("*", "*", "", MultiDict())),
        ("application/json", helpers.MimeType("application", "json", "", MultiDict())),
        (
            "application/json;  charset=utf-8",
            helpers.MimeType(
                "application", "json", "", MultiDict({"charset": "utf-8"})
            ),
        ),
        (
            """application/json; charset=utf-8;""",
            helpers.MimeType(
                "application", "json", "", MultiDict({"charset": "utf-8"})
            ),
        ),
        (
            'ApPlIcAtIoN/JSON;ChaRseT="UTF-8"',
            helpers.MimeType(
                "application", "json", "", MultiDict({"charset": "UTF-8"})
            ),
        ),
        (
            "application/rss+xml",
            helpers.MimeType("application", "rss", "xml", MultiDict()),
        ),
        (
            "text/plain;base64",
            helpers.MimeType("text", "plain", "", MultiDict({"base64": ""})),
        ),
    ],
)
def test_parse_mimetype(mimetype, expected) -> None:
    result = helpers.parse_mimetype(mimetype)

    assert isinstance(result, helpers.MimeType)
    assert result == expected


# ------------------- guess_filename ----------------------------------


def test_guess_filename_with_file_object(tmp_path) -> None:
    file_path = tmp_path / "test_guess_filename"
    with file_path.open("w+b") as fp:
        assert helpers.guess_filename(fp, "no-throw") is not None


def test_guess_filename_with_path(tmp_path) -> None:
    file_path = tmp_path / "test_guess_filename"
    assert helpers.guess_filename(file_path, "no-throw") is not None


def test_guess_filename_with_default() -> None:
    assert helpers.guess_filename(None, "no-throw") == "no-throw"


# ------------------- BasicAuth -----------------------------------


def test_basic_auth1() -> None:
    # missing password here
    with pytest.raises(ValueError):
        helpers.BasicAuth(None)


def test_basic_auth2() -> None:
    with pytest.raises(ValueError):
        helpers.BasicAuth("nkim", None)


def test_basic_with_auth_colon_in_login() -> None:
    with pytest.raises(ValueError):
        helpers.BasicAuth("nkim:1", "pwd")


def test_basic_auth3() -> None:
    auth = helpers.BasicAuth("nkim")
    assert auth.login == "nkim"
    assert auth.password == ""


def test_basic_auth4() -> None:
    auth = helpers.BasicAuth("nkim", "pwd")
    assert auth.login == "nkim"
    assert auth.password == "pwd"
    assert auth.encode() == "Basic bmtpbTpwd2Q="


@pytest.mark.parametrize(
    "header",
    (
        "Basic bmtpbTpwd2Q=",
        "basic bmtpbTpwd2Q=",
    ),
)
def test_basic_auth_decode(header) -> None:
    auth = helpers.BasicAuth.decode(header)
    assert auth.login == "nkim"
    assert auth.password == "pwd"


def test_basic_auth_invalid() -> None:
    with pytest.raises(ValueError):
        helpers.BasicAuth.decode("bmtpbTpwd2Q=")


def test_basic_auth_decode_not_basic() -> None:
    with pytest.raises(ValueError):
        helpers.BasicAuth.decode("Complex bmtpbTpwd2Q=")


def test_basic_auth_decode_bad_base64() -> None:
    with pytest.raises(ValueError):
        helpers.BasicAuth.decode("Basic bmtpbTpwd2Q")


@pytest.mark.parametrize("header", ("Basic ???", "Basic   "))
def test_basic_auth_decode_illegal_chars_base64(header) -> None:
    with pytest.raises(ValueError, match="Invalid base64 encoding."):
        helpers.BasicAuth.decode(header)


def test_basic_auth_decode_invalid_credentials() -> None:
    with pytest.raises(ValueError, match="Invalid credentials."):
        header = "Basic {}".format(base64.b64encode(b"username").decode())
        helpers.BasicAuth.decode(header)


@pytest.mark.parametrize(
    "credentials, expected_auth",
    (
        (":", helpers.BasicAuth(login="", password="", encoding="latin1")),
        (
            "username:",
            helpers.BasicAuth(login="username", password="", encoding="latin1"),
        ),
        (
            ":password",
            helpers.BasicAuth(login="", password="password", encoding="latin1"),
        ),
        (
            "username:password",
            helpers.BasicAuth(login="username", password="password", encoding="latin1"),
        ),
    ),
)
def test_basic_auth_decode_blank_username(credentials, expected_auth) -> None:
    header = f"Basic {base64.b64encode(credentials.encode()).decode()}"
    assert helpers.BasicAuth.decode(header) == expected_auth


def test_basic_auth_from_url() -> None:
    url = URL("http://user:pass@example.com")
    auth = helpers.BasicAuth.from_url(url)
    assert auth.login == "user"
    assert auth.password == "pass"


def test_basic_auth_from_not_url() -> None:
    with pytest.raises(TypeError):
        helpers.BasicAuth.from_url("http://user:pass@example.com")


class ReifyMixin:

    reify = NotImplemented

    def test_reify(self) -> None:
        class A:
            def __init__(self):
                self._cache = {}

            @self.reify
            def prop(self):
                return 1

        a = A()
        assert 1 == a.prop

    def test_reify_class(self) -> None:
        class A:
            def __init__(self):
                self._cache = {}

            @self.reify
            def prop(self):
                """Docstring."""
                return 1

        assert isinstance(A.prop, self.reify)
        assert "Docstring." == A.prop.__doc__

    def test_reify_assignment(self) -> None:
        class A:
            def __init__(self):
                self._cache = {}

            @self.reify
            def prop(self):
                return 1

        a = A()

        with pytest.raises(AttributeError):
            a.prop = 123


class TestPyReify(ReifyMixin):
    reify = helpers.reify_py


if not helpers.NO_EXTENSIONS and not IS_PYPY and hasattr(helpers, "reify_c"):

    class TestCReify(ReifyMixin):
        reify = helpers.reify_c


# ----------------------------------- is_ip_address() ----------------------


def test_is_ip_address() -> None:
    assert helpers.is_ip_address("127.0.0.1")
    assert helpers.is_ip_address("::1")
    assert helpers.is_ip_address("FE80:0000:0000:0000:0202:B3FF:FE1E:8329")

    # Hostnames
    assert not helpers.is_ip_address("localhost")
    assert not helpers.is_ip_address("www.example.com")

    # Out of range
    assert not helpers.is_ip_address("999.999.999.999")
    # Contain a port
    assert not helpers.is_ip_address("127.0.0.1:80")
    assert not helpers.is_ip_address("[2001:db8:0:1]:80")
    # Too many "::"
    assert not helpers.is_ip_address("1200::AB00:1234::2552:7777:1313")


def test_is_ip_address_bytes() -> None:
    assert helpers.is_ip_address(b"127.0.0.1")
    assert helpers.is_ip_address(b"::1")
    assert helpers.is_ip_address(b"FE80:0000:0000:0000:0202:B3FF:FE1E:8329")

    # Hostnames
    assert not helpers.is_ip_address(b"localhost")
    assert not helpers.is_ip_address(b"www.example.com")

    # Out of range
    assert not helpers.is_ip_address(b"999.999.999.999")
    # Contain a port
    assert not helpers.is_ip_address(b"127.0.0.1:80")
    assert not helpers.is_ip_address(b"[2001:db8:0:1]:80")
    # Too many "::"
    assert not helpers.is_ip_address(b"1200::AB00:1234::2552:7777:1313")


def test_ipv4_addresses() -> None:
    ip_addresses = [
        "0.0.0.0",
        "127.0.0.1",
        "255.255.255.255",
    ]
    for address in ip_addresses:
        assert helpers.is_ipv4_address(address)
        assert not helpers.is_ipv6_address(address)
        assert helpers.is_ip_address(address)


def test_ipv6_addresses() -> None:
    ip_addresses = [
        "0:0:0:0:0:0:0:0",
        "FFFF:FFFF:FFFF:FFFF:FFFF:FFFF:FFFF:FFFF",
        "00AB:0002:3008:8CFD:00AB:0002:3008:8CFD",
        "00ab:0002:3008:8cfd:00ab:0002:3008:8cfd",
        "AB:02:3008:8CFD:AB:02:3008:8CFD",
        "AB:02:3008:8CFD::02:3008:8CFD",
        "::",
        "1::1",
    ]
    for address in ip_addresses:
        assert not helpers.is_ipv4_address(address)
        assert helpers.is_ipv6_address(address)
        assert helpers.is_ip_address(address)


def test_host_addresses() -> None:
    hosts = [
        "www.four.part.host" "www.python.org",
        "foo.bar",
        "localhost",
    ]
    for host in hosts:
        assert not helpers.is_ip_address(host)


def test_is_ip_address_invalid_type() -> None:
    with pytest.raises(TypeError):
        helpers.is_ip_address(123)

    with pytest.raises(TypeError):
        helpers.is_ip_address(object())


# ----------------------------------- TimeoutHandle -------------------


def test_timeout_handle(loop) -> None:
    handle = helpers.TimeoutHandle(loop, 10.2)
    cb = mock.Mock()
    handle.register(cb)
    assert cb == handle._callbacks[0][0]
    handle.close()
    assert not handle._callbacks


def test_when_timeout_smaller_second(loop) -> None:
    timeout = 0.1
    timer = loop.time() + timeout

    handle = helpers.TimeoutHandle(loop, timeout)
    when = handle.start()._when
    handle.close()

    assert isinstance(when, float)
    assert isclose(when - timer, 0, abs_tol=0.001)


def test_timeout_handle_cb_exc(loop) -> None:
    handle = helpers.TimeoutHandle(loop, 10.2)
    cb = mock.Mock()
    handle.register(cb)
    cb.side_effect = ValueError()
    handle()
    assert cb.called
    assert not handle._callbacks


def test_timer_context_not_cancelled() -> None:
    with mock.patch("aiohttp.helpers.asyncio") as m_asyncio:
        m_asyncio.TimeoutError = asyncio.TimeoutError
        loop = mock.Mock()
        ctx = helpers.TimerContext(loop)
        ctx.timeout()

        with pytest.raises(asyncio.TimeoutError):
            with ctx:
                pass

        assert not m_asyncio.current_task.return_value.cancel.called


def test_timer_context_no_task(loop) -> None:
    with pytest.raises(RuntimeError):
        with helpers.TimerContext(loop):
            pass


async def test_weakref_handle(loop) -> None:
    cb = mock.Mock()
    helpers.weakref_handle(cb, "test", 0.01, loop)
    await asyncio.sleep(0.1)
    assert cb.test.called


async def test_weakref_handle_weak(loop) -> None:
    cb = mock.Mock()
    helpers.weakref_handle(cb, "test", 0.01, loop)
    del cb
    gc.collect()
    await asyncio.sleep(0.1)


# -------------------- ceil math -------------------------


def test_ceil_call_later() -> None:
    cb = mock.Mock()
    loop = mock.Mock()
    loop.time.return_value = 10.1
    helpers.call_later(cb, 10.1, loop)
    loop.call_at.assert_called_with(21.0, cb)


def test_ceil_call_later_no_timeout() -> None:
    cb = mock.Mock()
    loop = mock.Mock()
    helpers.call_later(cb, 0, loop)
    assert not loop.call_at.called


async def test_ceil_timeout_none(loop) -> None:
    async with helpers.ceil_timeout(None) as cm:
        assert cm.deadline is None


async def test_ceil_timeout_round(loop) -> None:
    async with helpers.ceil_timeout(7.5) as cm:
        frac, integer = modf(cm.deadline)
        assert frac == 0


async def test_ceil_timeout_small(loop) -> None:
    async with helpers.ceil_timeout(1.1) as cm:
        frac, integer = modf(cm.deadline)
        # a chance for exact integer with zero fraction is negligible
        assert frac != 0


# -------------------------------- ContentDisposition -------------------


@pytest.mark.parametrize(
    "kwargs, result",
    [
        (dict(foo="bar"), 'attachment; foo="bar"'),
        (dict(foo="bar[]"), 'attachment; foo="bar[]"'),
        (dict(foo=' a""b\\'), 'attachment; foo="\\ a\\"\\"b\\\\"'),
        (dict(foo="bär"), "attachment; foo*=utf-8''b%C3%A4r"),
        (dict(foo='bär "\\', quote_fields=False), 'attachment; foo="bär \\"\\\\"'),
        (dict(foo="bär", _charset="latin-1"), "attachment; foo*=latin-1''b%E4r"),
        (dict(filename="bär"), 'attachment; filename="b%C3%A4r"'),
        (dict(filename="bär", _charset="latin-1"), 'attachment; filename="b%E4r"'),
        (
            dict(filename='bär "\\', quote_fields=False),
            'attachment; filename="bär \\"\\\\"',
        ),
    ],
)
def test_content_disposition(kwargs, result) -> None:
    assert helpers.content_disposition_header("attachment", **kwargs) == result


def test_content_disposition_bad_type() -> None:
    with pytest.raises(ValueError):
        helpers.content_disposition_header("foo bar")
    with pytest.raises(ValueError):
        helpers.content_disposition_header("—Ç–µ—Å—Ç")
    with pytest.raises(ValueError):
        helpers.content_disposition_header("foo\x00bar")
    with pytest.raises(ValueError):
        helpers.content_disposition_header("")


def test_set_content_disposition_bad_param() -> None:
    with pytest.raises(ValueError):
        helpers.content_disposition_header("inline", **{"foo bar": "baz"})
    with pytest.raises(ValueError):
        helpers.content_disposition_header("inline", **{"—Ç–µ—Å—Ç": "baz"})
    with pytest.raises(ValueError):
        helpers.content_disposition_header("inline", **{"": "baz"})
    with pytest.raises(ValueError):
        helpers.content_disposition_header("inline", **{"foo\x00bar": "baz"})


# --------------------- proxies_from_env ------------------------------


@pytest.mark.parametrize(
    ("proxy_env_vars", "url_input", "expected_scheme"),
    (
        ({"http_proxy": "http://aiohttp.io/path"}, "http://aiohttp.io/path", "http"),
        ({"https_proxy": "http://aiohttp.io/path"}, "http://aiohttp.io/path", "https"),
        ({"ws_proxy": "http://aiohttp.io/path"}, "http://aiohttp.io/path", "ws"),
        ({"wss_proxy": "http://aiohttp.io/path"}, "http://aiohttp.io/path", "wss"),
    ),
    indirect=["proxy_env_vars"],
    ids=("http", "https", "ws", "wss"),
)
@pytest.mark.usefixtures("proxy_env_vars")
def test_proxies_from_env(url_input, expected_scheme) -> None:
    url = URL(url_input)
    ret = helpers.proxies_from_env()
    assert ret.keys() == {expected_scheme}
    assert ret[expected_scheme].proxy == url
    assert ret[expected_scheme].proxy_auth is None


@pytest.mark.parametrize(
    ("proxy_env_vars", "url_input", "expected_scheme"),
    (
        (
            {"https_proxy": "https://aiohttp.io/path"},
            "https://aiohttp.io/path",
            "https",
        ),
        ({"wss_proxy": "wss://aiohttp.io/path"}, "wss://aiohttp.io/path", "wss"),
    ),
    indirect=["proxy_env_vars"],
    ids=("https", "wss"),
)
@pytest.mark.usefixtures("proxy_env_vars")
def test_proxies_from_env_skipped(caplog, url_input, expected_scheme) -> None:
    url = URL(url_input)
    assert helpers.proxies_from_env() == {}
    assert len(caplog.records) == 1
    log_message = "{proto!s} proxies {url!s} are not supported, ignoring".format(
        proto=expected_scheme.upper(), url=url
    )
    assert caplog.record_tuples == [("aiohttp.client", 30, log_message)]


@pytest.mark.parametrize(
    ("proxy_env_vars", "url_input", "expected_scheme"),
    (
        (
            {"http_proxy": "http://user:pass@aiohttp.io/path"},
            "http://user:pass@aiohttp.io/path",
            "http",
        ),
    ),
    indirect=["proxy_env_vars"],
    ids=("http",),
)
@pytest.mark.usefixtures("proxy_env_vars")
def test_proxies_from_env_http_with_auth(url_input, expected_scheme) -> None:
    url = URL("http://user:pass@aiohttp.io/path")
    ret = helpers.proxies_from_env()
    assert ret.keys() == {expected_scheme}
    assert ret[expected_scheme].proxy == url.with_user(None)
    proxy_auth = ret[expected_scheme].proxy_auth
    assert proxy_auth.login == "user"
    assert proxy_auth.password == "pass"
    assert proxy_auth.encoding == "latin1"


# --------------------- get_env_proxy_for_url ------------------------------


@pytest.fixture
def proxy_env_vars(monkeypatch, request):
    for schema in getproxies_environment().keys():
        monkeypatch.delenv(f"{schema}_proxy", False)

    for proxy_type, proxy_list in request.param.items():
        monkeypatch.setenv(proxy_type, proxy_list)

    return request.param


@pytest.mark.parametrize(
    ("proxy_env_vars", "url_input", "expected_err_msg"),
    (
        (
            {"no_proxy": "aiohttp.io"},
            "http://aiohttp.io/path",
            r"Proxying is disallowed for `'aiohttp.io'`",
        ),
        (
            {"no_proxy": "aiohttp.io,proxy.com"},
            "http://aiohttp.io/path",
            r"Proxying is disallowed for `'aiohttp.io'`",
        ),
        (
            {"http_proxy": "http://example.com"},
            "https://aiohttp.io/path",
            r"No proxies found for `https://aiohttp.io/path` in the env",
        ),
        (
            {"https_proxy": "https://example.com"},
            "http://aiohttp.io/path",
            r"No proxies found for `http://aiohttp.io/path` in the env",
        ),
        (
            {},
            "https://aiohttp.io/path",
            r"No proxies found for `https://aiohttp.io/path` in the env",
        ),
        (
            {"https_proxy": "https://example.com"},
            "",
            r"No proxies found for `` in the env",
        ),
    ),
    indirect=["proxy_env_vars"],
    ids=(
        "url_matches_the_no_proxy_list",
        "url_matches_the_no_proxy_list_multiple",
        "url_scheme_does_not_match_http_proxy_list",
        "url_scheme_does_not_match_https_proxy_list",
        "no_proxies_are_set",
        "url_is_empty",
    ),
)
@pytest.mark.usefixtures("proxy_env_vars")
def test_get_env_proxy_for_url_negative(url_input, expected_err_msg) -> None:
    url = URL(url_input)
    with pytest.raises(LookupError, match=expected_err_msg):
        helpers.get_env_proxy_for_url(url)


@pytest.mark.parametrize(
    ("proxy_env_vars", "url_input"),
    (
        ({"http_proxy": "http://example.com"}, "http://aiohttp.io/path"),
        ({"https_proxy": "http://example.com"}, "https://aiohttp.io/path"),
        (
            {"http_proxy": "http://example.com,http://proxy.org"},
            "http://aiohttp.io/path",
        ),
    ),
    indirect=["proxy_env_vars"],
    ids=(
        "url_scheme_match_http_proxy_list",
        "url_scheme_match_https_proxy_list",
        "url_scheme_match_http_proxy_list_multiple",
    ),
)
def test_get_env_proxy_for_url(proxy_env_vars, url_input) -> None:
    url = URL(url_input)
    proxy, proxy_auth = helpers.get_env_proxy_for_url(url)
    proxy_list = proxy_env_vars[url.scheme + "_proxy"]
    assert proxy == URL(proxy_list)
    assert proxy_auth is None


# ------------- set_result / set_exception ----------------------


async def test_set_result(loop) -> None:
    fut = loop.create_future()
    helpers.set_result(fut, 123)
    assert 123 == await fut


async def test_set_result_cancelled(loop) -> None:
    fut = loop.create_future()
    fut.cancel()
    helpers.set_result(fut, 123)

    with pytest.raises(asyncio.CancelledError):
        await fut


async def test_set_exception(loop) -> None:
    fut = loop.create_future()
    helpers.set_exception(fut, RuntimeError())
    with pytest.raises(RuntimeError):
        await fut


async def test_set_exception_cancelled(loop) -> None:
    fut = loop.create_future()
    fut.cancel()
    helpers.set_exception(fut, RuntimeError())

    with pytest.raises(asyncio.CancelledError):
        await fut


# ----------- ChainMapProxy --------------------------


class TestChainMapProxy:
    def test_inheritance(self) -> None:
        with pytest.raises(TypeError):

            class A(helpers.ChainMapProxy):
                pass

    def test_getitem(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        assert cp["a"] == 2
        assert cp["b"] == 3

    def test_getitem_not_found(self) -> None:
        d = {"a": 1}
        cp = helpers.ChainMapProxy([d])
        with pytest.raises(KeyError):
            cp["b"]

    def test_get(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        assert cp.get("a") == 2

    def test_get_default(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        assert cp.get("c", 4) == 4

    def test_get_non_default(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        assert cp.get("a", 4) == 2

    def test_len(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        assert len(cp) == 2

    def test_iter(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        assert set(cp) == {"a", "b"}

    def test_contains(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        assert "a" in cp
        assert "b" in cp
        assert "c" not in cp

    def test_bool(self) -> None:
        assert helpers.ChainMapProxy([{"a": 1}])
        assert not helpers.ChainMapProxy([{}, {}])
        assert not helpers.ChainMapProxy([])

    def test_repr(self) -> None:
        d1 = {"a": 2, "b": 3}
        d2 = {"a": 1}
        cp = helpers.ChainMapProxy([d1, d2])
        expected = f"ChainMapProxy({d1!r}, {d2!r})"
        assert expected == repr(cp)


def test_is_expected_content_type_json_match_exact():
    expected_ct = "application/json"
    response_ct = "application/json"
    assert is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_is_expected_content_type_json_match_partially():
    expected_ct = "application/json"
    response_ct = "application/alto-costmap+json"  # mime-type from rfc7285
    assert is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_is_expected_content_type_non_application_json_suffix():
    expected_ct = "application/json"
    response_ct = "model/gltf+json"  # rfc 6839
    assert is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_is_expected_content_type_non_application_json_private_suffix():
    expected_ct = "application/json"
    response_ct = "x-foo/bar+json"  # rfc 6839
    assert is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_is_expected_content_type_json_non_lowercase():
    """Per RFC 2045, media type matching is case insensitive."""
    expected_ct = "application/json"
    response_ct = "Application/JSON"
    assert is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_is_expected_content_type_json_trailing_chars():
    expected_ct = "application/json"
    response_ct = "application/json-seq"
    assert not is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_is_expected_content_type_non_json_match_exact():
    expected_ct = "text/javascript"
    response_ct = "text/javascript"
    assert is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_is_expected_content_type_non_json_not_match():
    expected_ct = "application/json"
    response_ct = "text/plain"
    assert not is_expected_content_type(
        response_content_type=response_ct, expected_content_type=expected_ct
    )


def test_cookies_mixin():
    sut = helpers.CookieMixin()

    assert sut.cookies == {}
    assert str(sut.cookies) == ""

    sut.set_cookie("name", "value")
    assert str(sut.cookies) == "Set-Cookie: name=value; Path=/"
    sut.set_cookie("name", "other_value")
    assert str(sut.cookies) == "Set-Cookie: name=other_value; Path=/"

    sut.cookies["name"] = "another_other_value"
    sut.cookies["name"]["max-age"] = 10
    assert (
        str(sut.cookies) == "Set-Cookie: name=another_other_value; Max-Age=10; Path=/"
    )

    sut.del_cookie("name")
    expected = (
        'Set-Cookie: name=("")?; '
        "expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/"
    )
    assert Matches(expected) == str(sut.cookies)

    sut.set_cookie("name", "value", domain="local.host")
    expected = "Set-Cookie: name=value; Domain=local.host; Path=/"
    assert str(sut.cookies) == expected


def test_cookies_mixin_path():
    sut = helpers.CookieMixin()

    assert sut.cookies == {}

    sut.set_cookie("name", "value", path="/some/path")
    assert str(sut.cookies) == "Set-Cookie: name=value; Path=/some/path"
    sut.set_cookie("name", "value", expires="123")
    assert str(sut.cookies) == "Set-Cookie: name=value; expires=123; Path=/"
    sut.set_cookie(
        "name",
        "value",
        domain="example.com",
        path="/home",
        expires="123",
        max_age="10",
        secure=True,
        httponly=True,
        version="2.0",
        samesite="lax",
    )
    assert (
        str(sut.cookies).lower() == "set-cookie: name=value; "
        "domain=example.com; "
        "expires=123; "
        "httponly; "
        "max-age=10; "
        "path=/home; "
        "samesite=lax; "
        "secure; "
        "version=2.0"
    )


def test_sutonse_cookie__issue_del_cookie():
    sut = helpers.CookieMixin()

    assert sut.cookies == {}
    assert str(sut.cookies) == ""

    sut.del_cookie("name")
    expected = (
        'Set-Cookie: name=("")?; '
        "expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/"
    )
    assert Matches(expected) == str(sut.cookies)


def test_cookie_set_after_del():
    sut = helpers.CookieMixin()

    sut.del_cookie("name")
    sut.set_cookie("name", "val")
    # check for Max-Age dropped
    expected = "Set-Cookie: name=val; Path=/"
    assert str(sut.cookies) == expected


def test_populate_with_cookies():
    cookies_mixin = helpers.CookieMixin()
    cookies_mixin.set_cookie("name", "value")
    headers = CIMultiDict()

    helpers.populate_with_cookies(headers, cookies_mixin.cookies)
    assert headers == CIMultiDict({"Set-Cookie": "name=value; Path=/"})


@pytest.mark.parametrize(
    ["value", "expected"],
    [
        # email.utils.parsedate returns None
        pytest.param("xxyyzz", None),
        # datetime.datetime fails with ValueError("year 4446413 is out of range")
        pytest.param("Tue, 08 Oct 4446413 00:56:40 GMT", None),
        # datetime.datetime fails with ValueError("second must be in 0..59")
        pytest.param("Tue, 08 Oct 2000 00:56:80 GMT", None),
        # OK
        pytest.param(
            "Tue, 08 Oct 2000 00:56:40 GMT",
            datetime.datetime(2000, 10, 8, 0, 56, 40, tzinfo=datetime.timezone.utc),
        ),
        # OK (ignore timezone and overwrite to UTC)
        pytest.param(
            "Tue, 08 Oct 2000 00:56:40 +0900",
            datetime.datetime(2000, 10, 8, 0, 56, 40, tzinfo=datetime.timezone.utc),
        ),
    ],
)
def test_parse_http_date(value, expected):
    assert parse_http_date(value) == expected
