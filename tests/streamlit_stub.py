"""A minimal Streamlit stand-in, so the dashboard can be executed in CI.

Streamlit's /_stcore/health endpoint reports that the Tornado server came
up, not that the page ran: Streamlit does not execute the script until a
browser session connects, so a NameError in the app still returns a
healthy check and ships green. This stub records every call the app makes
and lets the suite run the real app top to bottom.
"""
import contextlib
import functools

LOG = []
CTX = ["<page>"]
SELECT_INDEX = 0
# Widget values a test wants to simulate, by widget label. Anything not
# listed is left at the default the app passes, so the suite tests the
# page at the shipped thresholds unless a test says otherwise.
WIDGET_OVERRIDES = {}


class StopExecution(Exception):
    pass


class _SessionState(dict):
    """Real Streamlit allows both st.session_state["x"] and
    st.session_state.x; widgets keyed with `key=` read and write through
    here across a rerun. Fresh per reset() the same way a real session's
    state is fresh per browser session in these tests, each of which
    exercises exactly one simulated run."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def __setattr__(self, key, value):
        self[key] = value


session_state = _SessionState()


def reset(select_index=0, widget_overrides=None):
    global SELECT_INDEX
    LOG.clear()
    CTX[:] = ["<page>"]
    SELECT_INDEX = select_index
    WIDGET_OVERRIDES.clear()
    WIDGET_OVERRIDES.update(widget_overrides or {})
    session_state.clear()


def _rec(kind, *payload):
    LOG.append((CTX[-1], kind, payload))


class _Container:
    def __init__(self, name):
        self._name = name

    def __enter__(self):
        CTX.append(self._name)
        return self

    def __exit__(self, *exc):
        CTX.pop()
        return False

    def __getattr__(self, item):
        def call(*a, **k):
            CTX.append(self._name)
            try:
                return globals()[item](*a, **k)
            finally:
                CTX.pop()
        return call


def set_page_config(**k): _rec("set_page_config", k)
def title(t, **k): _rec("title", t)
def caption(t, **k): _rec("caption", t)
def header(t, **k): _rec("header", t)
def subheader(t, **k): _rec("subheader", t)
def markdown(t, **k): _rec("markdown", t)
def write(t, **k): _rec("write", t)
def info(t, **k): _rec("info", t)
def success(t, **k): _rec("success", t)
def warning(t, **k): _rec("warning", t)
def error(t, **k): _rec("error", t)
def code(t, **k): _rec("code", t)
def divider(**k): _rec("divider")
def metric(label, value, delta=None, **k): _rec("metric", label, value, delta)
def dataframe(df, **k): _rec("dataframe", df, k)
def table(df, **k): _rec("table", df, k)


def stop():
    _rec("stop")
    raise StopExecution()


def columns(spec, **k):
    n = spec if isinstance(spec, int) else len(spec)
    _rec("columns", n)
    return [_Container(f"col{i + 1}/{n}") for i in range(n)]


def tabs(labels, **k):
    _rec("tabs", list(labels))
    return [_Container(f"tab:{lab}") for lab in labels]


def expander(label, **k):
    _rec("expander", label)
    return _Container(f"expander:{label}")


class _BorderedContainer(_Container):
    """As _Container, but its own open and close are logged (unlike
    columns/tabs/expander, which only log their own creation), so a
    consumer of LOG can tell a bordered region's extent. Python looks up
    __enter__/__exit__ on the TYPE for the `with` protocol, bypassing an
    instance attribute override -- hence a real subclass here rather than
    patching an attribute onto a plain _Container instance."""

    def __exit__(self, *exc):
        result = super().__exit__(*exc)
        _rec("container_close")
        return result


def container(border=False, **k):
    _rec("container_open", border)
    return _BorderedContainer("container")


def selectbox(label, options, **k):
    options = list(options)
    _rec("selectbox", label, options, SELECT_INDEX)
    return options[SELECT_INDEX]


def _in_range(label, value, min_value, max_value):
    """Real Streamlit refuses a value outside the widget's range; a test
    that simulates one must not silently get away with it."""
    for v in (value if isinstance(value, tuple) else (value,)):
        if min_value is not None and v < min_value:
            raise ValueError(f"{label}: {v} is below min_value {min_value}")
        if max_value is not None and v > max_value:
            raise ValueError(f"{label}: {v} is above max_value {max_value}")


def slider(label, min_value=None, max_value=None, value=None, step=None, **k):
    """Simulates leaving every slider at its default unless a test set a
    WIDGET_OVERRIDES entry for it: the suite is testing that the page
    renders correctly at the shipped thresholds, not sampling the control
    surface. value is returned unchanged, tuple or scalar."""
    value = WIDGET_OVERRIDES.get(label, value)
    _in_range(label, value, min_value, max_value)
    _rec("slider", label, value)
    return value


def select_slider(label, options=None, value=None, **k):
    options = list(options) if options is not None else []
    if value is None:
        value = options[0] if options else None
    _rec("select_slider", label, value)
    return value


def number_input(label, min_value=None, max_value=None, value=None, step=None, **k):
    value = WIDGET_OVERRIDES.get(label, value)
    _in_range(label, value, min_value, max_value)
    _rec("number_input", label, value)
    return value


def checkbox(label, value=False, key=None, help=None, **k):
    if key is not None and key not in session_state:
        session_state[key] = value
    _rec("checkbox", label, session_state.get(key, value) if key else value)
    return session_state[key] if key is not None else value


def text_input(label, value="", key=None, **k):
    if key is not None and key not in session_state:
        session_state[key] = value
    out = session_state[key] if key is not None else value
    _rec("text_input", label, out)
    return out


def button(label, **k):
    _rec("button", label)
    return False


def file_uploader(label, accept_multiple_files=False, key=None, **k):
    """No file picked by default, same as a real page nobody has touched
    yet. A test that wants to simulate an upload passes a list of
    stand-in file objects (each needs a .name and to be readable by
    pandas.read_csv) through WIDGET_OVERRIDES, keyed by this label."""
    default = [] if accept_multiple_files else None
    out = WIDGET_OVERRIDES.get(label, default)
    _rec("file_uploader", label, out if isinstance(out, list) else bool(out))
    return out


def progress(value, **k):
    _rec("progress", value)


def download_button(label, data, file_name=None, mime=None, **k):
    _rec("download_button", label, file_name, data)
    return False


sidebar = _Container("sidebar")


def cache_data(func=None, **kwargs):
    def deco(f):
        @functools.wraps(f)
        def wrapper(*a, **k):
            key = (a, tuple(sorted(k.items())))
            if not hasattr(wrapper, "_cache"):
                wrapper._cache = {}
            if key not in wrapper._cache:
                wrapper._cache[key] = f(*a, **k)
            return wrapper._cache[key]
        wrapper.clear = lambda: wrapper.__dict__.pop("_cache", None)
        return wrapper
    return deco(func) if func is not None else deco


cache_resource = cache_data


@contextlib.contextmanager
def spinner(*a, **k):
    yield
