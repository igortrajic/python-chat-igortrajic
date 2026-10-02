# python-chat-igortrajic

A simple TCP chat application in Python, with a threaded server and a CLI client.

## Requirements

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
```

## Usage

Start the server (default port `12345`):

```bash
uv run src/server.py
```

Start a client in another terminal:

```bash
uv run src/client.py
```

## Project structure

```
src/
├── protocol.py  # Shared message protocol (used by both server and client)
├── server.py    # TCP server, handles each client in its own thread
└── client.py    # TCP client
```

## Protocol

The server and client speak a simple JSON-based protocol over the TCP
connection, defined in `src/protocol.py`. All communication after a client
connects — including the username handshake — uses this protocol; there is
no separate plain-text sub-protocol.

### Wire format

Each message is a single JSON object, serialized with Python's `json`
module, encoded as UTF-8, and terminated with a newline (`\n`) —
newline-delimited JSON. One message = one line:

```
{"type": "chat", "payload": {"sender": "alice", "text": "hi"}}\n
```

`protocol.encode(message)` serializes a message dict to a line (including
the trailing `\n`); `protocol.decode(line)` parses a line back into a
message dict.

### Message envelope

Every message is a JSON object with at least two fields:

| Field     | Type   | Description                                   |
|-----------|--------|------------------------------------------------|
| `type`    | string | One of `"chat"`, `"system"`, `"command"`.      |
| `payload` | object | Fields specific to that message's type/event. |

### Message types

**`chat`** — a chat message. The payload shape differs by direction:

- Client → server (`protocol.make_chat_request`): the client only sends the
  text; it never claims a sender identity.
  ```json
  {"type": "chat", "payload": {"text": "hi"}}
  ```
- Server → client (`protocol.make_chat`): the server broadcasts the message
  with the sender it authenticated at the handshake — a client's own claimed
  identity (if any) is never trusted or forwarded.
  ```json
  {"type": "chat", "payload": {"sender": "alice", "text": "hi"}}
  ```

**`system`** — a server notification. The payload always has an `event`
field plus event-specific fields (built with `protocol.make_system(event,
**fields)`):

| Event          | Direction       | Fields                  | Meaning                                              |
|----------------|-----------------|--------------------------|-------------------------------------------------------|
| `join`         | client → server | `username`               | Request to register a username (the handshake).       |
| `join_ok`      | server → client | `username`               | Handshake succeeded; `username` is now registered.    |
| `join_taken`   | server → client | `reason`                 | Handshake failed: username already in use.             |
| `join_invalid` | server → client | `reason`                 | Handshake failed: empty, contains control characters, too long, or the line was malformed or not a `join` request. |
| `join`         | server → all    | `username`               | Broadcast: a client has joined the chat.               |
| `leave`        | server → all    | `username`               | Broadcast: a client has disconnected.                  |
| `error`        | server → client | `reason`                 | After the handshake: the server received a malformed or unexpected message from this client (see "Errors after joining"). |

**`command`** — reserved for client commands. The message shape
(`{"type": "command", "payload": {...}}`) is defined, but no command is
parsed or acted on yet.

### Handshake

The first message on a connection must be a `join` request. The server
replies with `join_ok`, `join_taken` or `join_invalid` (the last two carry a
`reason`). After `join_taken` or `join_invalid` the connection stays open and
the client may retry with another `join`.

**Any** invalid line during the handshake — malformed JSON, a non-object
payload, the wrong type or event, a missing or non-string `username` — gets
`join_invalid`, never `error`.

The server closes the connection without a reply if the handshake takes
longer than 30 seconds, the data isn't valid UTF-8, or a line exceeds 4096
characters without a newline.

### Errors after joining

After `join_ok`, a line that isn't valid JSON, isn't a JSON object, is
missing `type` or `payload`, or isn't a `chat` message with a `text` string
is handled gracefully rather than crashing the connection:

- The **server** logs the line to its console, sends the offending client a
  `system`/`error` message with a `reason`, and drops just that line — the
  connection stays open for the next message.
- The **client** logs a warning (to stderr) and drops any line it can't
  render, leaving the rest of the session unaffected.
