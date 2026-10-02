import socket
import threading
import codecs
import argparse
import sys
import re
import logging
import protocol
from typing import Callable, NamedTuple

logger = logging.getLogger("chat.client")

QUIT_TIMEOUT = 5
quitting = threading.Event()

CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f-\x9f\ud800-\udfff]')

def sanitize(text):
    return CONTROL_CHARS_RE.sub('', text)

def _clean(value):
    """Sanitized string, or None if it isn't a string or is empty after sanitizing."""
    if not isinstance(value, str):
        return None
    return sanitize(value) or None

def _render_chat(payload):
    sender, text = _clean(payload.get("sender")), _clean(payload.get("text"))
    if sender and text:
        return f"[{sender}]: {text}"

def _render_join(payload):
    username = _clean(payload.get("username"))
    if username:
        return f"[{username}] has joined the chat."

def _render_leave(payload):
    username = _clean(payload.get("username"))
    if username:
        return f"[{username}] has left the chat."

def _render_error(payload):
    reason = _clean(payload.get("reason"))
    if reason:
        return f"[error] {reason}"

def _render_nick(payload):
    old, new = _clean(payload.get("old")), _clean(payload.get("new"))
    if old and new:
        return f"[{old}] is now known as [{new}]."

def _render_users(payload):
    users = payload.get("users")
    if not isinstance(users, list):
        return None
    names = [name for name in map(_clean, users) if name]
    return f"Connected users ({len(names)}): {', '.join(names)}"

SYSTEM_RENDERERS = {"join": _render_join, "leave": _render_leave, "error": _render_error, "users": _render_users, "nick": _render_nick}

def _render(message):
    """Display text for a decoded message, or None if it can't be rendered confidently."""
    payload = message["payload"]
    if not isinstance(payload, dict):
        return None
    if message["type"] == protocol.TYPE_CHAT:
        return _render_chat(payload)
    if message["type"] == protocol.TYPE_SYSTEM:
        event = payload.get("event")
        renderer = SYSTEM_RENDERERS.get(event) if isinstance(event, str) else None
        return renderer(payload) if renderer else None
    return None

def _format_incoming(line):
    try:
        message = protocol.decode(line)
    except protocol.ProtocolError as e:
        logger.warning("Unrecognized message from server, ignoring: %s", e)
        return None

    text = _render(message)
    if text is None:
        logger.warning("Unrecognized message from server, ignoring: %r", message)
    return text

def _display(line):
    text = _format_incoming(line)
    if text is not None:
        print(text)

class Command(NamedTuple):
    handler: Callable[[socket.socket, str], bool]
    usage: str
    description: str

def cmd_help(client_socket, arg):
    width = max(len(command.usage) for command in COMMANDS.values())
    print("Available commands:")
    for command in COMMANDS.values():
        print(f"  {command.usage:<{width}}  {command.description}")
    return True

def begin_quit(client_socket):
    quitting.set()
    try:
        client_socket.shutdown(socket.SHUT_WR)
    except OSError:
        pass

def cmd_quit(client_socket, arg):
    print("Disconnecting...")
    begin_quit(client_socket)
    return False

def cmd_users(client_socket, arg):
    client_socket.sendall(protocol.encode(protocol.make_command("users")).encode('utf-8'))
    return True

def cmd_nick(client_socket, arg):
    if not arg:
        print("[error] Usage: /nick <new_name>")
        return True
    client_socket.sendall(protocol.encode(protocol.make_command("nick", [arg])).encode('utf-8'))
    return True

COMMANDS: dict[str, Command] = {
    "help": Command(cmd_help, "/help", "Show this list of commands"),
    "users": Command(cmd_users, "/users", "List connected users"),
    "nick": Command(cmd_nick, "/nick <new_name>", "Change your username"),
    "quit": Command(cmd_quit, "/quit", "Disconnect from the server"),
}

def parse_command(line):
    name, _, arg = line[1:].partition(" ")
    return name.lower(), arg.strip()

def handle_command(client_socket, line):
    name, arg = parse_command(line)
    command = COMMANDS.get(name)
    if command is None:
        print(f"[error] Unknown command '/{name}'. Type /help for a list of commands.")
        return True
    return command.handler(client_socket, arg)

def receive_messages(client_socket, decoder=None, buffer=""):
    if decoder is None:
        decoder = codecs.getincrementaldecoder('utf-8')()
    while "\n" in buffer:
        message, buffer = buffer.split("\n", 1)
        if message:
            _display(message)
    while True:
        try:
            data = client_socket.recv(1024)
            if not data:
                print("Disconnected." if quitting.is_set() else "Connection closed by the server.")
                break
            buffer += decoder.decode(data)
            while "\n" in buffer:
                message, buffer = buffer.split("\n", 1)
                if message:
                    _display(message)
        except ConnectionError:
            print("Disconnected." if quitting.is_set() else "Connection to the server was lost.")
            break
        except Exception as e:
            if not quitting.is_set():
                print(f"Error receiving message: {e}")
            break

    client_socket.close()

def main():
    parser = argparse.ArgumentParser(description="TCP Chat Client")
    parser.add_argument("-H", "--host", type=str, default="localhost", help="Server host (default: localhost)")
    parser.add_argument("-p", "--port", type=int, default=12345, help="Server port (default: 12345)")
    args = parser.parse_args()

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        client_socket.connect((args.host, args.port))
    except (ConnectionRefusedError, socket.gaierror, TimeoutError, OSError) as e:
        print(f"Error: Could not connect to {args.host}:{args.port} ({e})")
        sys.exit(1)

    decoder = codecs.getincrementaldecoder('utf-8')()
    buffer = ""

    def recv_line():
        nonlocal buffer
        while "\n" not in buffer:
            data = client_socket.recv(1024)
            if not data:
                raise ConnectionError("Connection closed by the server.")
            buffer += decoder.decode(data)
        line, buffer = buffer.split("\n", 1)
        return line

    while True:
        username = ""
        while not username:
            try:
                username = input("Enter your username: ").strip()
            except (KeyboardInterrupt, EOFError):
                client_socket.close()
                sys.exit(0)
            if not username:
                print("Username cannot be empty.")

        try:
            client_socket.sendall(protocol.encode(protocol.make_system("join", username=username)).encode('utf-8'))
            response = recv_line()
        except (OSError, ConnectionError) as e:
            print(f"Error: Could not negotiate username ({e})")
            client_socket.close()
            sys.exit(1)

        try:
            message = protocol.decode(response)
        except protocol.ProtocolError as e:
            print(f"Error: Received a malformed response from the server ({e})")
            client_socket.close()
            sys.exit(1)

        payload = message.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        event = payload.get("event") if message.get("type") == protocol.TYPE_SYSTEM else None

        if event == "join_ok":
            accepted_username = payload.get("username")
            if accepted_username != username:
                # Should not happen (the server rejects names it can't
                # register as-is), but don't let the display silently
                # diverge from what the server actually registered.
                print(f"You are registered as '{accepted_username}'.")
            break
        elif event in ("join_taken", "join_invalid"):
            print(payload.get("reason", "Username rejected."))
        else:
            print(f"Unexpected response from server: {response}")
            client_socket.close()
            sys.exit(1)

    receive_thread = threading.Thread(target=receive_messages, args=(client_socket, decoder, buffer), daemon=True)
    receive_thread.start()

    try:
        while True:
            message = input()
            if not message:
                continue
            if message.startswith("/"):
                if not handle_command(client_socket, message):
                    break
                continue
            client_socket.sendall(protocol.encode(protocol.make_chat_request(message)).encode('utf-8'))
    except (KeyboardInterrupt, EOFError):
        begin_quit(client_socket)
    except OSError:
        pass
    finally:
        if quitting.is_set():
            receive_thread.join(timeout=QUIT_TIMEOUT)
        client_socket.close()

if __name__ == "__main__":
    main()
