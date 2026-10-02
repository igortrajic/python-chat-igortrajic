import socket
import threading
import codecs
import argparse
import sys
import re
import logging
import protocol

logger = logging.getLogger("chat.client")

CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f-\x9f\ud800-\udfff]')

def sanitize(text):
    return CONTROL_CHARS_RE.sub('', text)

def _format_incoming(line):
    try:
        message = protocol.decode(line)
    except protocol.ProtocolError as e:
        logger.warning("Unrecognized message from server, ignoring: %s", e)
        return None

    payload = message["payload"]
    msg_type = message["type"]

    if isinstance(payload, dict):
        if msg_type == protocol.TYPE_CHAT:
            sender, text = payload.get("sender"), payload.get("text")
            if isinstance(sender, str) and isinstance(text, str):
                sender, text = sanitize(sender), sanitize(text)
                if sender and text:
                    return f"[{sender}]: {text}"
        elif msg_type == protocol.TYPE_SYSTEM:
            event, username = payload.get("event"), payload.get("username")
            if event in ("join", "leave") and isinstance(username, str):
                username = sanitize(username)
                if username:
                    return f"[{username}] has {'joined' if event == 'join' else 'left'} the chat."
            elif event == "error" and isinstance(payload.get("reason"), str):
                reason = sanitize(payload["reason"])
                if reason:
                    return f"[error] {reason}"

    logger.warning("Unrecognized message from server, ignoring: %r", message)
    return None

def _display(line):
    text = _format_incoming(line)
    if text is not None:
        print(text)

def receive_messages(client_socket, decoder=None, buffer=""):
    """Listens for incoming messages from the server."""
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
                print("Connection closed by the server.")
                break
            buffer += decoder.decode(data)
            while "\n" in buffer:
                message, buffer = buffer.split("\n", 1)
                if message:
                    _display(message)
        except ConnectionError:
            print("Connection to the server was lost.")
            break
        except Exception as e:
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
            client_socket.sendall(protocol.encode(protocol.make_chat_request(message)).encode('utf-8'))
    except (KeyboardInterrupt, EOFError, OSError):
        pass
    finally:
        client_socket.close()

if __name__ == "__main__":
    main()
