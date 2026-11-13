"""Shared constants for SynCanvas.

This module is imported by both the server and the desktop client so that the
wire protocol, permission model and visual palette have a single source of
truth.
"""

from __future__ import annotations

APP_NAME = "SynCanvas"
APP_TAGLINE = "Collaborative whiteboard for your local network"
APP_VERSION = "1.0.0"
PROTOCOL_VERSION = 1

# ---------------------------------------------------------------------------
# Network defaults / limits
# ---------------------------------------------------------------------------
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 45871
DEFAULT_CLIENT_HOST = "127.0.0.1"
DEFAULT_DB_PATH = "database/syncanvas.db"

MAX_MESSAGE_BYTES = 16 * 1024 * 1024      # framing hard limit (per JSON line)
MAX_IMAGE_BYTES = 1536 * 1024            # decoded binary image limit (client downscales)
MAX_TEXT_LENGTH = 8_000
MAX_CHAT_LENGTH = 1_000
MAX_NAME_LENGTH = 32
MAX_PASSWORD_LENGTH = 128
MAX_SESSION_NAME = 64
MAX_OBJECTS = 5_000
MAX_CANVAS_STATE_BYTES = 12 * 1024 * 1024
MAX_UNDO_HISTORY = 60
MAX_REDO_HISTORY = 60
MAX_EVENT_HISTORY = 800
MAX_CHAT_HISTORY = 250
MAX_ACTIVITY_HISTORY = 400
MAX_SNAPSHOTS = 100
AUTH_TIMEOUT_SECONDS = 20
HEARTBEAT_INTERVAL = 8
HEARTBEAT_TIMEOUT = 30
MAX_SEND_QUEUE = 1_500
MAX_PENDING_OUTBOX = 1_000
MAX_RECONNECT_BACKOFF = 20.0

# Undo policy: a user may only undo operations they own and only when the
# affected objects have not been changed by somebody else since the operation.
UNDO_POLICY = (
    "Operation ownership: each participant has a private undo stack. "
    "Undo is only applied when the object revisions still match the recorded "
    "post-operation revisions; otherwise the undo is rejected with a conflict "
    "instead of overwriting another participant's newer work."
)

# ---------------------------------------------------------------------------
# Authentication, roles and permissions
# ---------------------------------------------------------------------------
ROLE_HOST = "host"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_HOST, ROLE_EDITOR, ROLE_VIEWER)
ROLE_LABELS = {ROLE_HOST: "Admin / Host", ROLE_EDITOR: "Editor", ROLE_VIEWER: "Viewer"}
JOINABLE_ROLES = (ROLE_EDITOR, ROLE_VIEWER)

# Server-side action permissions. The server enforces these; the client only
# mirrors them to disable controls early.
PERMISSIONS = {
    ROLE_HOST: {
        "draw", "erase", "chat", "undo", "redo", "clear", "export", "import",
        "snapshot", "restore", "set_role", "kick", "present", "lock_board",
        "image", "manage_session", "laser",
    },
    ROLE_EDITOR: {
        "draw", "erase", "chat", "undo", "redo", "export", "snapshot",
        "image", "laser",
    },
    ROLE_VIEWER: {"chat", "export"},
}

MUTATING_ACTIONS = {
    "create_object", "update_object", "delete_objects", "stroke_append",
    "stroke_end", "clear_canvas", "import_canvas", "undo", "redo",
    "restore_snapshot",
}

# ---------------------------------------------------------------------------
# Object kinds / tools
# ---------------------------------------------------------------------------
KIND_STROKE = "stroke"
KIND_LINE = "line"
KIND_RECT = "rect"
KIND_ROUNDED_RECT = "rounded_rect"
KIND_ELLIPSE = "ellipse"
KIND_CIRCLE = "circle"
KIND_DIAMOND = "diamond"
KIND_TRIANGLE = "triangle"
KIND_ARROW = "arrow"
KIND_CONNECTOR = "connector"
KIND_TEXT = "text"
KIND_STICKY = "sticky"
KIND_IMAGE = "image"
KIND_MINDNODE = "mindnode"
KIND_FLOW_TERMINATOR = "flow_terminator"
KIND_FLOW_PROCESS = "flow_process"
KIND_FLOW_DECISION = "flow_decision"
KIND_FLOW_IO = "flow_io"
KIND_FLOW_DOCUMENT = "flow_document"
KIND_FLOW_DATABASE = "flow_database"

OBJECT_KINDS = (
    KIND_STROKE, KIND_LINE, KIND_RECT, KIND_ROUNDED_RECT, KIND_ELLIPSE,
    KIND_CIRCLE, KIND_DIAMOND, KIND_TRIANGLE, KIND_ARROW, KIND_CONNECTOR,
    KIND_TEXT, KIND_STICKY, KIND_IMAGE, KIND_MINDNODE, KIND_FLOW_TERMINATOR,
    KIND_FLOW_PROCESS, KIND_FLOW_DECISION, KIND_FLOW_IO, KIND_FLOW_DOCUMENT,
    KIND_FLOW_DATABASE,
)

FILLABLE_KINDS = {
    KIND_RECT, KIND_ROUNDED_RECT, KIND_ELLIPSE, KIND_CIRCLE, KIND_DIAMOND,
    KIND_TRIANGLE, KIND_STICKY, KIND_MINDNODE, KIND_FLOW_TERMINATOR,
    KIND_FLOW_PROCESS, KIND_FLOW_DECISION, KIND_FLOW_IO, KIND_FLOW_DOCUMENT,
    KIND_FLOW_DATABASE,
}

# Tool identifiers used by the client toolbar.
TOOL_SELECT = "select"
TOOL_PAN = "pan"
TOOL_PENCIL = "pencil"
TOOL_BRUSH = "brush"
TOOL_ERASER = "eraser"
TOOL_TEXT = "text"
TOOL_LINE = "line"
TOOL_RECT = "rect"
TOOL_ROUNDED_RECT = "rounded_rect"
TOOL_ELLIPSE = "ellipse"
TOOL_CIRCLE = "circle"
TOOL_DIAMOND = "diamond"
TOOL_TRIANGLE = "triangle"
TOOL_ARROW = "arrow"
TOOL_CONNECTOR = "connector"
TOOL_STICKY = "sticky"
TOOL_MINDNODE = "mindnode"
TOOL_IMAGE = "image"
TOOL_LASER = "laser"
TOOL_FLOW_START = "flow_terminator"
TOOL_FLOW_PROCESS = "flow_process"
TOOL_FLOW_DECISION = "flow_decision"
TOOL_FLOW_IO = "flow_io"

TOOL_LABELS = {
    TOOL_SELECT: "Select",
    TOOL_PAN: "Pan",
    TOOL_PENCIL: "Pencil",
    TOOL_BRUSH: "Brush",
    TOOL_ERASER: "Eraser",
    TOOL_TEXT: "Text",
    TOOL_LINE: "Line",
    TOOL_RECT: "Rectangle",
    TOOL_ROUNDED_RECT: "Rounded rectangle",
    TOOL_ELLIPSE: "Ellipse",
    TOOL_CIRCLE: "Circle",
    TOOL_DIAMOND: "Diamond",
    TOOL_TRIANGLE: "Triangle",
    TOOL_ARROW: "Arrow",
    TOOL_CONNECTOR: "Connector",
    TOOL_STICKY: "Sticky note",
    TOOL_MINDNODE: "Mind map node",
    TOOL_IMAGE: "Image",
    TOOL_LASER: "Laser pointer",
    TOOL_FLOW_START: "Start / End",
    TOOL_FLOW_PROCESS: "Process",
    TOOL_FLOW_DECISION: "Decision",
    TOOL_FLOW_IO: "Input / Output",
}

# ---------------------------------------------------------------------------
# Palette / UX defaults
# ---------------------------------------------------------------------------
PALETTE = [
    "#09090b", "#27272a", "#52525b", "#71717a", "#a1a1aa",
    "#d4d4d8", "#f4f4f5", "#ffffff", "#e11d48", "#ea580c",
    "#d97706", "#059669",
]
STICKY_COLORS = ["#fef9c3", "#f4f4f5", "#e4e4e7", "#ffedd5", "#f0fdf4", "#e0f2fe"]
USER_COLORS = [
    "#a1a1aa", "#38bdf8", "#34d399", "#fbbf24", "#f87171",
    "#c084fc", "#fb923c", "#2dd4bf", "#e2e8f0", "#94a3b8",
]
DEFAULT_STROKE = "#f4f4f5"
DEFAULT_FILL = "#27272a"
DEFAULT_CANVAS_BG = "#0f0f11"
GRID_SIZE = 24
CANVAS_SIZE = 12_000
MIN_ZOOM = 0.15
MAX_ZOOM = 5.0

# ---------------------------------------------------------------------------
# Protocol message types
# ---------------------------------------------------------------------------
# Client -> server
HELLO = "HELLO"
CREATE_SESSION = "CREATE_SESSION"
JOIN_SESSION = "JOIN_SESSION"
RESUME_SESSION = "RESUME_SESSION"
LEAVE_SESSION = "LEAVE_SESSION"
CREATE_OBJECT = "CREATE_OBJECT"
UPDATE_OBJECT = "UPDATE_OBJECT"
DELETE_OBJECTS = "DELETE_OBJECTS"
STROKE_APPEND = "STROKE_APPEND"
STROKE_END = "STROKE_END"
CHAT = "CHAT"
CURSOR_UPDATE = "CURSOR_UPDATE"
PING = "PING"
PONG = "PONG"
UNDO = "UNDO"
REDO = "REDO"
CLEAR_CANVAS = "CLEAR_CANVAS"
SET_ROLE = "SET_ROLE"
KICK_USER = "KICK_USER"
SNAPSHOT_CREATE = "SNAPSHOT_CREATE"
SNAPSHOT_LIST = "SNAPSHOT_LIST"
SNAPSHOT_GET = "SNAPSHOT_GET"
SNAPSHOT_RESTORE = "SNAPSHOT_RESTORE"
PRESENTATION_START = "PRESENTATION_START"
PRESENTATION_UPDATE = "PRESENTATION_UPDATE"
PRESENTATION_STOP = "PRESENTATION_STOP"
SET_BOARD_LOCK = "SET_BOARD_LOCK"
LASER = "LASER"
REQUEST_SYNC = "REQUEST_SYNC"
BOARD_IMPORT = "BOARD_IMPORT"
GET_SERVER_STATS = "GET_SERVER_STATS"
SET_PROFILE = "SET_PROFILE"

# Server -> client
WELCOME = "WELCOME"
JOINED = "JOINED"
LEFT = "LEFT"
EVENT = "EVENT"
ACK = "ACK"
ERROR = "ERROR"
CANVAS_STATE = "CANVAS_STATE"
SYNC_COMPLETE = "SYNC_COMPLETE"
CHAT_MESSAGE = "CHAT_MESSAGE"
PRESENCE = "PRESENCE"
CURSOR = "CURSOR"
SERVER_STATS = "SERVER_STATS"
SNAPSHOT_LIST_RESULT = "SNAPSHOT_LIST_RESULT"
SNAPSHOT_PREVIEW = "SNAPSHOT_PREVIEW"
SNAPSHOT_SAVED = "SNAPSHOT_SAVED"
VERSION_RESTORED = "VERSION_RESTORED"
PRESENTATION_STATE = "PRESENTATION_STATE"
KICKED = "KICKED"
SYSTEM = "SYSTEM"
LASER_POINT = "LASER_POINT"
CONFLICT = "CONFLICT"
ACTIVITY = "ACTIVITY"

CLIENT_MESSAGE_TYPES = {
    HELLO, CREATE_SESSION, JOIN_SESSION, RESUME_SESSION, LEAVE_SESSION,
    CREATE_OBJECT, UPDATE_OBJECT, DELETE_OBJECTS, STROKE_APPEND, STROKE_END,
    CHAT, CURSOR_UPDATE, PING, PONG, UNDO, REDO, CLEAR_CANVAS, SET_ROLE,
    KICK_USER, SNAPSHOT_CREATE, SNAPSHOT_LIST, SNAPSHOT_GET, SNAPSHOT_RESTORE,
    PRESENTATION_START, PRESENTATION_UPDATE, PRESENTATION_STOP,
    SET_BOARD_LOCK, LASER, REQUEST_SYNC, BOARD_IMPORT, GET_SERVER_STATS,
    SET_PROFILE,
}

# Actions carried in EVENT broadcasts.
ACTION_OBJECT_CREATED = "object_created"
ACTION_STROKE_APPENDED = "stroke_appended"
ACTION_OBJECT_UPDATED = "object_updated"
ACTION_OBJECTS_DELETED = "objects_deleted"
ACTION_CANVAS_CLEARED = "canvas_cleared"
ACTION_CANVAS_REPLACED = "canvas_replaced"
ACTION_UNDO = "undo"
ACTION_REDO = "redo"
ACTION_VERSION_RESTORED = "version_restored"
ACTION_BOARD_LOCKED = "board_locked"
ACTION_PRESENTATION = "presentation"
ACTION_USER_JOINED = "user_joined"
ACTION_USER_LEFT = "user_left"
ACTION_CHAT = "chat"

# Error codes
ERR_AUTH_REQUIRED = "AUTH_REQUIRED"
ERR_UNAUTHORIZED = "UNAUTHORIZED"
ERR_FORBIDDEN = "FORBIDDEN"
ERR_NOT_FOUND = "NOT_FOUND"
ERR_BAD_REQUEST = "BAD_REQUEST"
ERR_PROTOCOL = "PROTOCOL_ERROR"
ERR_RATE_LIMIT = "RATE_LIMIT"
ERR_REVISION_CONFLICT = "REVISION_CONFLICT"
ERR_UNDO_CONFLICT = "UNDO_CONFLICT"
ERR_SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
ERR_WRONG_PASSWORD = "WRONG_PASSWORD"
ERR_NAME_TAKEN = "NAME_TAKEN"
ERR_ROOM_FULL = "ROOM_FULL"
ERR_BOARD_LOCKED = "BOARD_LOCKED"
ERR_TOO_LARGE = "TOO_LARGE"
ERR_INVALID_STATE = "INVALID_STATE"
ERR_SERVER_BUSY = "SERVER_BUSY"

MAX_CLIENTS_PER_SESSION = 40
MAX_SESSIONS = 50

# Keyboard shortcuts are documented centrally for the help dialog.
SHORTCUTS = [
    ("V", "Select / move objects"),
    ("H or Space", "Pan the canvas"),
    ("P", "Pencil"),
    ("B", "Brush"),
    ("E", "Eraser"),
    ("T", "Text"),
    ("R", "Rectangle"),
    ("O", "Ellipse"),
    ("L", "Line"),
    ("A", "Arrow"),
    ("S", "Sticky note"),
    ("M", "Mind-map node"),
    ("Ctrl+Z", "Undo my last operation"),
    ("Ctrl+Shift+Z / Ctrl+Y", "Redo my last undo"),
    ("Ctrl+S", "Save a snapshot"),
    ("Ctrl+E", "Export board"),
    ("Ctrl++ / Ctrl+-", "Zoom in / out"),
    ("Ctrl+0", "Reset zoom"),
    ("Delete", "Delete selected objects"),
    ("Ctrl+D", "Duplicate selection"),
    ("Enter", "Open presentation mode (host)"),
    ("F1", "Keyboard shortcuts"),
]
