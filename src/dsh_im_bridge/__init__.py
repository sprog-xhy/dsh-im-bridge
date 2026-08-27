"""dsh-im-bridge: connect DeepSeek Harness agents to IM / collaboration tools.

The package is a local bridge that runs on the same machine as `dsh web` and
talks to it over the loopback ``/api`` protocol (unary RPC + ``events.mux``
WebSocket). IM channels (Feishu, QQ via OneBot11, WOA, console) plug into a
hub that routes messages both ways.
"""

__version__ = "0.1.0"
