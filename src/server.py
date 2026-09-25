import socket
import threading
import codecs
import queue
import re
from rich.console import Console
from rich.markup import escape
import argparse

console = Console()

MAX_CONNECTIONS = 100
IDLE_TIMEOUT = 300
SOCKET_TIMEOUT = 5
MAX_LINE_LENGTH = 4096
MAX_QUEUE_SIZE = 100

CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')

def sanitize(text):
    """Strip ASCII control/escape characters so a client can't inject
    terminal control sequences into other clients' displays."""
    return CONTROL_CHARS_RE.sub('', text)

connection_slots = threading.Semaphore(MAX_CONNECTIONS)

clients_lock = threading.Lock()
clients = {}  

def broadcast(message, exclude=None):
    with clients_lock:
        targets = [(sock, q) for sock, q in clients.items() if sock is not exclude]
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
            client_socket.sendall((message + "\n").encode('utf-8'))
        except (OSError, socket.timeout):
            console.print(f"[bold red]Failed to deliver message to {address}, dropping connection.[/bold red]")
            stop_event.set()
            try:
                client_socket.close()
            except OSError:
                pass
            break

def handle_client(client_socket, address):
    console.print(f"[bold green]New connection established from {address}[/bold green]")
    client_socket.settimeout(SOCKET_TIMEOUT)

    out_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
    stop_event = threading.Event()
    with clients_lock:
        clients[client_socket] = out_queue
    broadcast(f"{address} has joined the chat.", exclude=client_socket)

    writer = threading.Thread(
        target=writer_loop,
        args=(client_socket, address, out_queue, stop_event),
        daemon=True,
    )
    writer.start()

    decoder = codecs.getincrementaldecoder('utf-8')()
    buffer = ""
    idle_elapsed = 0
    try:
        while not stop_event.is_set():
            try:
                data = client_socket.recv(1024)
            except socket.timeout:
                idle_elapsed += SOCKET_TIMEOUT
                if idle_elapsed >= IDLE_TIMEOUT:
                    console.print(f"[bold yellow]Connection from {address} timed out (idle).[/bold yellow]")
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
                message, buffer = buffer.split("\n", 1)
                message = sanitize(message)
                if not message:
                    continue
                console.print(f"{address}: {escape(message)}")
                broadcast(f"{address}: {message}", exclude=client_socket)
            if len(buffer) > MAX_LINE_LENGTH:
                console.print(f"[bold red]Message from {address} exceeded {MAX_LINE_LENGTH} bytes without a newline, disconnecting.[/bold red]")
                break

    finally:
        stop_event.set()
        with clients_lock:
            clients.pop(client_socket, None)
        try:
            client_socket.close()
        except OSError:
            pass
        connection_slots.release()
        console.print(f"[bold yellow]Connection from {address} closed.[/bold yellow]")
        broadcast(f"{address} has left the chat.")

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
