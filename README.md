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
├── server.py   # TCP server, handles each client in its own thread
└── client.py   # TCP client
```
