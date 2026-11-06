import socket
import threading
import codecs
import queue
import re
from dataclasses import dataclass
from rich.console import Console
from rich.markup import escape
import argparse
import protocol

console = Console()

MAX_CONNECTIONS = 100
IDLE_TIMEOUT = 300
SOCKET_TIMEOUT = 5
MAX_LINE_LENGTH = 4096
MAX_QUEUE_SIZE = 100
MAX_USERNAME_LENGTH = 32
USERNAME_TIMEOUT = 30

CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f-\x9f\ud800-\udfff]')

def sanitize(text):
    return CONTROL_CHARS_RE.sub('', text)

connection_slots = threading.Semaphore(MAX_CONNECTIONS)

@dataclass
class ClientInfo:
    username: str
    queue: "queue.Queue"

clients_lock = threading.Lock()
clients: dict[socket.socket, ClientInfo] = {}

def broadcast(message, exclude=None):
    with clients_lock:
        targets = [(sock, info.queue) for sock, info in clients.items() if sock is not exclude]
    for sock, q in targets:
        try:
            q.put_nowait(message)
        except queue.Full:
            console.print("[bold red]Recipient too slow (queue full), dropping connection.[/bold red]")
            try:
                sock.close()
            except OSError:
                pass

def writer_loop(client_socket, address, out_queue, stop_event):
    while not stop_event.is_set():
        try:
            message = out_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            client_socket.sendall(message.encode('utf-8'))
        except (OSError, socket.timeout):
            console.print(f"[bold red]Failed to deliver message to {address}, dropping connection.[/bold red]")
            stop_event.set()
            try:
                client_socket.close()
            except OSError:
                pass
            break

def _send_system(client_socket, event, **fields):
    try:
        client_socket.sendall(protocol.encode(protocol.make_system(event, **fields)).encode('utf-8'))
        return True
    except OSError:
        return False

def _validate_username(name):
    if not name:
        return "Username cannot be empty."
    if sanitize(name) != name:
        return "Username contains disallowed control characters."
    if len(name) > MAX_USERNAME_LENGTH:
        return f"Username must be at most {MAX_USERNAME_LENGTH} characters."
    return None

def read_username(client_socket, address, decoder, out_queue):
    buffer = ""
    elapsed = 0
    while True:
        while "\n" not in buffer:
            try:
                data = client_socket.recv(1024)
            except socket.timeout:
                elapsed += SOCKET_TIMEOUT
                if elapsed >= USERNAME_TIMEOUT:
                    console.print(f"[bold yellow]Connection from {address} timed out waiting for username.[/bold yellow]")
                    return None, buffer
                continue
            except (ConnectionError, OSError):
                return None, buffer
            if not data:
                return None, buffer
            try:
                buffer += decoder.decode(data)
            except UnicodeDecodeError:
                return None, buffer
            if len(buffer) > MAX_LINE_LENGTH:
                return None, buffer
            elapsed = 0

        raw_line, buffer = buffer.split("\n", 1)

        try:
            message = protocol.decode(raw_line)
        except protocol.ProtocolError as e:
            console.print(f"[bold red]Malformed handshake message from {address}, ignoring: {e}[/bold red]")
            if not _send_system(client_socket, "join_invalid", reason="Malformed message: expected a JSON join request."):
                return None, buffer
            continue

        payload = message.get("payload") or {}
        username_field = payload.get("username") if isinstance(payload, dict) else None
        if (
            message.get("type") != protocol.TYPE_SYSTEM
            or not isinstance(payload, dict)
            or payload.get("event") != "join"
            or not isinstance(username_field, str)
        ):
            console.print(f"[bold red]Unexpected handshake message from {address}, ignoring: {escape(repr(message))}[/bold red]")
            if not _send_system(client_socket, "join_invalid", reason="Expected a 'join' system message with a 'username' field."):
                return None, buffer
            continue

        entered = username_field.strip()
        rejection = _validate_username(entered)

        if rejection is not None:
            if not _send_system(client_socket, "join_invalid", reason=rejection):
                return None, buffer
            continue

        # Validation guarantees the name has no control characters, so
        # registering it can't silently change the identity the client asked for.
        requested = entered

        with clients_lock:
            taken = any(info.username == requested for info in clients.values())
            if not taken:
                clients[client_socket] = ClientInfo(username=requested, queue=out_queue)

        if taken:
            if not _send_system(
                client_socket,
                "join_taken",
                reason=f"'{requested}' is already taken. Please choose a different username.",
            ):
                return None, buffer
            continue

        if not _send_system(client_socket, "join_ok", username=requested):
            with clients_lock:
                clients.pop(client_socket, None)
            return None, buffer

        return requested, buffer

def _log_name(client_socket, fallback):
    """Markup-safe current username (it can change via /nick), or the fallback."""
    with clients_lock:
        info = clients.get(client_socket)
    return escape(info.username) if info is not None else fallback

def handle_client(client_socket, address):
    console.print(f"[bold green]New connection established from {address}[/bold green]")
    client_socket.settimeout(SOCKET_TIMEOUT)

    out_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
    decoder = codecs.getincrementaldecoder('utf-8')()
    username = None
    try:
        username, buffer = read_username(client_socket, address, decoder, out_queue)
    finally:
        if username is None:
            with clients_lock:
                clients.pop(client_socket, None)
            try:
                client_socket.close()
            except OSError:
                pass
            connection_slots.release()
    if username is None:
        return

    safe_username = escape(username)

    stop_event = threading.Event()
    try:
        console.print(f"[bold cyan]{address} identified as '{safe_username}'[/bold cyan]")
        broadcast(protocol.encode(protocol.make_system("join", username=username)), exclude=client_socket)

        writer = threading.Thread(
            target=writer_loop,
            args=(client_socket, address, out_queue, stop_event),
            daemon=True,
        )
        writer.start()

        while "\n" in buffer:
            raw_line, buffer = buffer.split("\n", 1)
            _handle_chat_line(client_socket, raw_line)

        idle_elapsed = 0
        while not stop_event.is_set():
            try:
                data = client_socket.recv(1024)
            except socket.timeout:
                idle_elapsed += SOCKET_TIMEOUT
                if idle_elapsed >= IDLE_TIMEOUT:
                    console.print(f"[bold yellow]Connection from {address} ({_log_name(client_socket, safe_username)}) timed out (idle).[/bold yellow]")
                    break
                continue
            except (ConnectionError, OSError):
                break
            idle_elapsed = 0
            if not data:
                break
            try:
                buffer += decoder.decode(data)
            except UnicodeDecodeError:
                break 
            while "\n" in buffer:
                raw_line, buffer = buffer.split("\n", 1)
                _handle_chat_line(client_socket, raw_line)
            if len(buffer) > MAX_LINE_LENGTH:
                console.print(f"[bold red]Message from {_log_name(client_socket, safe_username)} exceeded {MAX_LINE_LENGTH} bytes without a newline, disconnecting.[/bold red]")
                break

    finally:
        stop_event.set()
        with clients_lock:
            info = clients.pop(client_socket, None)
        if info is not None:
            username = info.username
            safe_username = escape(username)
        try:
            client_socket.close()
        except OSError:
            pass
        connection_slots.release()
        console.print(f"[bold yellow]Connection from {address} ({safe_username}) closed.[/bold yellow]")
        broadcast(protocol.encode(protocol.make_system("leave", username=username)))

def _send_error(client_socket, reason):
    _send_to(client_socket, protocol.make_system("error", reason=reason))

def _send_to(client_socket, message):
    with clients_lock:
        info = clients.get(client_socket)
    if info is None:
        return
    try:
        info.queue.put_nowait(protocol.encode(message))
    except queue.Full:
        pass

def _cmd_users(client_socket, args):
    with clients_lock:
        usernames = sorted(info.username for info in clients.values())
    _send_to(client_socket, protocol.make_system("users", users=usernames))

def _cmd_nick(client_socket, args):
    if len(args) != 1:
        _send_error(client_socket, "Usage: /nick <new_name>")
        return
    new_name = args[0].strip()
    rejection = _validate_username(new_name)
    if rejection is None:
        with clients_lock:
            info = clients.get(client_socket)
            if info is None:
                return
            old_name = info.username
            if new_name == old_name:
                rejection = "That is already your username."
            elif any(other.username == new_name for other in clients.values()):
                rejection = f"'{new_name}' is already taken."
            else:
                info.username = new_name
    if rejection is not None:
        _send_error(client_socket, rejection)
        return
    console.print(f"[bold cyan]'{escape(old_name)}' is now known as '{escape(new_name)}'[/bold cyan]")
    broadcast(protocol.encode(protocol.make_system("nick", old=old_name, new=new_name)))

COMMAND_HANDLERS = {"users": _cmd_users, "nick": _cmd_nick}

def _handle_command(client_socket, safe_username, payload):
    if not isinstance(payload, dict):
        payload = {}
    name = payload.get("name")
    args = payload.get("args")
    handler = COMMAND_HANDLERS.get(name) if isinstance(name, str) else None
    if handler is None:
        console.print(f"[bold red]Unknown command from {safe_username}, ignoring: {escape(repr(payload))}[/bold red]")
        _send_error(client_socket, "Unknown command.")
        return
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        console.print(f"[bold red]Malformed command arguments from {safe_username}, ignoring: {escape(repr(payload))}[/bold red]")
        _send_error(client_socket, "Malformed command arguments.")
        return
    handler(client_socket, args)

def _handle_chat_line(client_socket, raw_line):
    with clients_lock:
        info = clients.get(client_socket)
    if info is None:
        return
    username = info.username
    safe_username = escape(username)

    try:
        parsed = protocol.decode(raw_line)
    except protocol.ProtocolError as e:
        console.print(f"[bold red]Malformed message from {safe_username}, ignoring: {e}[/bold red]")
        _send_error(client_socket, "Malformed message: expected a JSON object with 'type' and 'payload'.")
        return

    payload = parsed.get("payload") or {}
    if parsed.get("type") == protocol.TYPE_COMMAND:
        _handle_command(client_socket, safe_username, payload)
        return

    text = payload.get("text") if isinstance(payload, dict) else None
    if parsed.get("type") != protocol.TYPE_CHAT or not isinstance(text, str):
        console.print(f"[bold red]Unexpected message from {safe_username}, ignoring: {escape(repr(parsed))}[/bold red]")
        _send_error(client_socket, "Expected a 'chat' message with a 'text' field.")
        return

    text = sanitize(text)
    if not text:
        return

    console.print(f"[{safe_username}]: {escape(text)}")
    broadcast(protocol.encode(protocol.make_chat(username, text)), exclude=client_socket)

def main():

    parser = argparse.ArgumentParser(description="TCP Chat Server")
    parser.add_argument("-p", "--port", type=int, default=12345, help="Port to listen on (default: 12345)")
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind(('0.0.0.0', args.port))
        server.listen(5)
        console.print(f"[bold blue]Server listening on 0.0.0.0:{args.port}...[/bold blue]")

        while True:
            client_socket, address = server.accept()
            if not connection_slots.acquire(blocking=False):
                console.print(f"[bold red]Connection limit reached ({MAX_CONNECTIONS}), rejecting {address}[/bold red]")
                client_socket.close()
                continue
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, address),
                daemon=True,
            )
            client_thread.start()

    except KeyboardInterrupt:
        console.print("\n[bold red]Shutdown signal received. Shutting down gracefully...[/bold red]")
    finally:
        server.close()
        console.print("[bold red]Server closed.[/bold red]")

if __name__ == "__main__":
    main()
