# Package agent_core: luồng agent-graph (LangGraph) phục vụ /api/chat.
import logging as _logging

_log = _logging.getLogger("agent_core")
if not _log.handlers:
    _h = _logging.StreamHandler()
    try:
        _h.stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _h.setFormatter(_logging.Formatter("[agent_core] %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(_logging.INFO)
    _log.propagate = False
