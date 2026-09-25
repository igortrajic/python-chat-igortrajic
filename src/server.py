import socket
import threading
import codecs
from rich.console import Console
from rich.markup import escape
import argparse

console = Console()

MAX_CONNECTIONS = 100
IDLE_TIMEOUT = 300

connection_slots = threading.Semaphore(MAX_CONNECTIONS)

clients_lock = threading.Lock()
clients = set()

def broadcast(message, exclude=None):
    data = message.encode('utf-8')
    with clients_lock:
        targets = [sock for sock in clients if sock is not exclude]
    for sock in targets:
        try:
            sock.sendall(data)
        except OSError:
            pass

def handle_client(client_socket, address):
    console.print(f"[bold green]New connection established from {address}[/bold green]")
    client_socket.settimeout(IDLE_TIMEOUT)
    with clients_lock:
        clients.add(client_socket)
    broadcast(f"{address} has joined the chat.", exclude=client_socket)
    decoder = codecs.getincrementaldecoder('utf-8')()
    try:
        while True:
            try:
                data = client_socket.recv(1024)
            except socket.timeout:
                console.print(f"[bold yellow]Connection from {address} timed out (idle).[/bold yellow]")
                break
            except (ConnectionError, OSError):
                break
            if not data:
                break
            try:
                message = decoder.decode(data)
            except UnicodeDecodeError:
                break
            if not message:
                continue
            console.print(f"{address}: {escape(message)}")
            broadcast(f"{address}: {message}", exclude=client_socket)

    finally:
        with clients_lock:
            clients.discard(client_socket)
        client_socket.close()
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
